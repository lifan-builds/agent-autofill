import json
import os
import re
import secrets
import sqlite3
import base64
import sys
from cryptography.fernet import Fernet, InvalidToken
from pathlib import Path
from typing import Any, Iterable


def _credential_store_error_message() -> str:
    """Return safe recovery guidance for an unavailable OS credential store."""

    if sys.platform == "darwin":
        return (
            "OS credential store unavailable: unlock the macOS login Keychain "
            "and run Agent Autofill in a host process with Keychain access; "
            "do not move secrets into files or environment variables"
        )
    return (
        "OS credential store unavailable: configure or unlock the operating-system "
        "credential store, then run Agent Autofill again"
    )


_SECRET_AUDIT_KEY_PARTS = {
    "access",
    "account_number",
    "auth_code",
    "body",
    "card_number",
    "code",
    "cookie",
    "credential_handle",
    "cvc",
    "cvv",
    "handle",
    "number",
    "otp",
    "password",
    "raw",
    "refresh",
    "routing_number",
    "secret",
    "security_code",
    "session",
    "token",
    "value",
    "values",
}


def _is_secret_audit_key(key: object) -> bool:
    """Return whether an audit metadata key can carry private material."""

    if not isinstance(key, str):
        return False
    normalized = key.replace("-", "_")
    normalized = re.sub(
        r"(?<=[a-z0-9])(?=[A-Z])", "_", normalized
    ).lower()
    parts = {part for part in normalized.split("_") if part}
    return (
        normalized in _SECRET_AUDIT_KEY_PARTS
        or bool(parts & _SECRET_AUDIT_KEY_PARTS)
        or "credential_handle" in normalized
        or "access_token" in normalized
        or "refresh_token" in normalized
    )


def _redact_audit(value: Any) -> Any:
    """Recursively remove secret-bearing keys from audit metadata.

    Audits are intentionally assembled from metadata-only values by the
    broker.  This recursive filter is a second defensive boundary for future
    callers and prevents a nested payload from becoming a logging escape
    hatch.
    """

    if isinstance(value, dict):
        return {
            key: _redact_audit(item)
            for key, item in value.items()
            if isinstance(key, str) and not _is_secret_audit_key(key)
        }
    if isinstance(value, list):
        return [_redact_audit(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_audit(item) for item in value]
    return value


class KeyProvider:
    def load_or_create(self, name: str) -> bytes:
        raise NotImplementedError


class KeyringProvider(KeyProvider):
    """Use one OS credential-store vault for the database and connectors.

    Older releases created one Keychain item per database path and connector
    secret.  The current format keeps those values in one versioned Keychain
    item and caches it for the lifetime of the process.  Legacy items are read
    only when needed, copied into the vault, and removed after the copy is
    durable.  Connector credentials therefore remain OS-store-backed while a
    normal broker process needs only one Keychain access.
    """

    _VAULT_ACCOUNT = "vault:v1"
    _VAULT_VERSION = 1

    def __init__(self, service: str = "agent-autofill", backend=None):
        self.service = service
        self._backend = backend
        self._vault: dict | None = None

    def _keyring(self):
        if self._backend is not None:
            return self._backend
        import keyring

        return keyring

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii")

    @staticmethod
    def _decode(value: object, label: str) -> bytes:
        if not isinstance(value, str):
            raise RuntimeError(f"Keychain vault contains an invalid {label}")
        try:
            decoded = base64.urlsafe_b64decode(value.encode("ascii"))
        except Exception as exc:
            raise RuntimeError(f"Keychain vault contains an invalid {label}") from exc
        if not decoded:
            raise RuntimeError(f"Keychain vault contains an empty {label}")
        return decoded

    def _empty_vault(self) -> dict:
        return {"version": self._VAULT_VERSION, "database_keys": {}, "secrets": {}}

    @staticmethod
    def _copy_vault(vault: dict) -> dict:
        """Copy the small mutable sections before attempting a Keychain write.

        A failed ``set_password`` must not leave the in-memory cache claiming
        that a value was persisted.  Keeping this copy explicit also avoids
        retaining references to dictionaries returned by a test backend.
        """

        return {
            "version": vault["version"],
            "database_keys": dict(vault["database_keys"]),
            "secrets": dict(vault["secrets"]),
        }

    def _load_vault(self) -> dict:
        if self._vault is not None:
            return self._vault
        try:
            serialized = self._keyring().get_password(
                self.service, self._VAULT_ACCOUNT
            )
            if not serialized:
                vault = self._empty_vault()
            else:
                vault = json.loads(serialized)
                if (
                    not isinstance(vault, dict)
                    or vault.get("version") != self._VAULT_VERSION
                    or not isinstance(vault.get("database_keys"), dict)
                    or not isinstance(vault.get("secrets"), dict)
                ):
                    raise RuntimeError("Keychain vault format is unsupported")
            self._vault = vault
            return vault
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(_credential_store_error_message()) from exc

    def _persist_vault(self, vault: dict) -> None:
        serialized = json.dumps(vault, separators=(",", ":"), sort_keys=True)
        try:
            self._keyring().set_password(
                self.service, self._VAULT_ACCOUNT, serialized
            )
        except Exception as exc:
            raise RuntimeError(_credential_store_error_message()) from exc
        self._vault = vault

    def _legacy_get(self, name: str) -> bytes | None:
        if name == self._VAULT_ACCOUNT:
            return None
        try:
            value = self._keyring().get_password(self.service, name)
            return self._decode(value, "legacy secret") if value else None
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(_credential_store_error_message()) from exc

    def _legacy_delete(self, name: str) -> None:
        if name == self._VAULT_ACCOUNT:
            return
        try:
            self._keyring().delete_password(self.service, name)
        except Exception as exc:
            message = str(exc).lower()
            if (
                isinstance(exc, (KeyError, LookupError))
                or "not found" in message
                or "no password" in message
                or "does not exist" in message
                or "no such" in message
            ):
                return
            raise RuntimeError(_credential_store_error_message()) from exc

    def load_or_create(self, name: str) -> bytes:
        try:
            vault = self._load_vault()
            encoded = vault["database_keys"].get(name)
            if encoded:
                raw = self._decode(encoded, "database key")
                if len(raw) < 16:
                    raise RuntimeError("Keychain vault contains an invalid database key")
                return raw

            legacy = self._legacy_get(name)
            raw = legacy or secrets.token_bytes(32)
            updated = self._copy_vault(vault)
            updated["database_keys"][name] = self._encode(raw)
            self._persist_vault(updated)
            if legacy is not None:
                self._legacy_delete(name)
            return raw
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(_credential_store_error_message()) from exc

    def get_secret(self, name: str) -> bytes | None:
        vault = self._load_vault()
        encoded = vault["secrets"].get(name)
        if encoded:
            return self._decode(encoded, "connector secret")
        legacy = self._legacy_get(name)
        if legacy is None:
            return None
        updated = self._copy_vault(vault)
        updated["secrets"][name] = self._encode(legacy)
        self._persist_vault(updated)
        self._legacy_delete(name)
        return legacy

    def set_secret(self, name: str, value: bytes) -> None:
        if not isinstance(value, bytes) or not value:
            raise RuntimeError("connector secret must be nonempty bytes")
        vault = self._load_vault()
        updated = self._copy_vault(vault)
        updated["secrets"][name] = self._encode(value)
        self._persist_vault(updated)
        self._legacy_delete(name)

    def delete_secret(self, name: str) -> None:
        vault = self._load_vault()
        if name in vault["secrets"]:
            updated = self._copy_vault(vault)
            del updated["secrets"][name]
            self._persist_vault(updated)
        self._legacy_delete(name)

    def migrate_legacy(self, names: Iterable[str]) -> int:
        """Migrate known connector accounts without returning their values."""

        migrated = 0
        for name in names:
            before = self._load_vault()["secrets"].get(name)
            if self.get_secret(name) is not None and before is None:
                migrated += 1
        return migrated


class Store:
    """SQLite store with application-level authenticated encryption for values.

    The key is supplied by the caller (normally a Keychain secret). This avoids
    persisting connector credentials in the database or returning them to agents.
    """

    def __init__(
        self,
        path: str | Path = ":memory:",
        key: bytes | None = None,
        key_provider: KeyProvider | None = None,
        key_name: str = "default",
    ):
        raw_key = key or (
            key_provider.load_or_create(key_name)
            if key_provider
            else secrets.token_bytes(32)
        )
        self.cipher = Fernet(base64.urlsafe_b64encode(raw_key[:32].ljust(32, b"0")))
        db_path = (
            ":memory:" if str(path) == ":memory:" else str(Path(path).expanduser())
        )
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        if db_path != ":memory:":
            os.chmod(db_path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        create table if not exists kv (namespace text, key text, value text not null, primary key(namespace,key));
        create table if not exists audit (id integer primary key, agent_id text, operation text, resource text, created_at text, metadata text);
        """)
        # Older releases used the plaintext OTP in the ``otp-used`` key.  The
        # current broker stores only a non-secret hash in ``otp-consumed``;
        # remove legacy keys as soon as a compatible store is opened so a code
        # cannot remain in SQLite identifiers indefinitely.
        self.purge_legacy_otp_markers()

    def _seal(self, value: Any) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return self.cipher.encrypt(raw).decode()

    def _open(self, envelope: str) -> Any:
        try:
            return json.loads(self.cipher.decrypt(envelope.encode()))
        except InvalidToken as exc:
            raise ValueError("store integrity check failed") from exc

    def put(self, namespace: str, key: str, value: Any) -> None:
        self.db.execute(
            "insert or replace into kv values (?,?,?)",
            (namespace, key, self._seal(value)),
        )
        self.db.commit()

    def put_many(self, records: Iterable[tuple[str, str, Any]]) -> None:
        """Persist a small group of values atomically.

        Connector replacement writes the connection and its source metadata as
        one logical operation.  Keeping this transaction primitive here avoids
        callers having to own SQLite encryption details or leave half-written
        configurations behind when a write fails.
        """

        self.apply_changes(records, [])

    def apply_changes(
        self,
        records: Iterable[tuple[str, str, Any]],
        deletes: Iterable[tuple[str, str]],
        audits: Iterable[tuple[str, str, str, str, dict[str, Any]]] | None = None,
    ) -> None:
        """Apply encrypted puts and deletes in one SQLite transaction.

        Values are sealed before the transaction starts.  This means a bad
        value cannot leave an earlier item from the same batch committed, and
        a database failure rolls back both puts and deletes together.  Optional
        audit rows are sealed and inserted in the same transaction so a failed
        audit write cannot leave a caller-visible mutation without its audit.
        """

        sealed = [
            (namespace, key, self._seal(value))
            for namespace, key, value in records
        ]
        delete_keys = list(deletes)
        sealed_audits = [
            (
                agent_id,
                operation,
                resource,
                created_at,
                self._seal(_redact_audit(metadata)),
            )
            for agent_id, operation, resource, created_at, metadata in (audits or [])
        ]
        try:
            if sealed:
                self.db.executemany(
                    "insert or replace into kv values (?,?,?)", sealed
                )
            if delete_keys:
                self.db.executemany(
                    "delete from kv where namespace=? and key=?", delete_keys
                )
            if sealed_audits:
                self.db.executemany(
                    "insert into audit(agent_id,operation,resource,created_at,metadata) values(?,?,?,?,?)",
                    sealed_audits,
                )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def delete_many(self, records: Iterable[tuple[str, str]]) -> None:
        """Delete a group of namespace/key rows atomically."""

        self.apply_changes([], records)

    def put_if_absent(self, namespace: str, key: str, value: Any) -> bool:
        """Insert one value only when its namespace/key is unused.

        SQLite's primary key makes this safe when more than one broker process
        tries to consume the same one-time marker at once.  A failed insert is
        a normal "already present" result; other database failures still
        propagate to the caller.
        """

        sealed = self._seal(value)
        try:
            self.db.execute(
                "insert into kv values (?,?,?)", (namespace, key, sealed)
            )
            self.db.commit()
        except sqlite3.IntegrityError:
            self.db.rollback()
            return False
        except Exception:
            self.db.rollback()
            raise
        return True

    def get(self, namespace: str, key: str) -> Any | None:
        row = self.db.execute(
            "select value from kv where namespace=? and key=?", (namespace, key)
        ).fetchone()
        return None if row is None else self._open(row["value"])

    def delete(self, namespace: str, key: str) -> None:
        self.db.execute(
            "delete from kv where namespace=? and key=?", (namespace, key)
        )
        self.db.commit()

    def purge_legacy_otp_markers(self) -> None:
        """Remove pre-hash OTP markers whose keys contained the code."""

        self.db.execute("delete from kv where namespace='otp-used'")
        self.db.commit()

    def audit(
        self,
        agent_id: str,
        operation: str,
        resource: str,
        created_at: str,
        metadata: dict[str, Any],
    ) -> None:
        safe = _redact_audit(metadata)
        self.db.execute(
            "insert into audit(agent_id,operation,resource,created_at,metadata) values(?,?,?,?,?)",
            (agent_id, operation, resource, created_at, self._seal(safe)),
        )
        self.db.commit()

    def audits(self) -> list[dict[str, Any]]:
        result = []
        for row in self.db.execute("select * from audit order by id"):
            item = dict(row)
            try:
                item["metadata"] = self._open(item["metadata"])
            except (ValueError, json.JSONDecodeError):
                item["metadata"] = json.loads(item["metadata"])
            result.append(item)
        return result
