import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import tempfile
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

try:
    import fcntl
except ImportError:  # pragma: no cover - the supported host is macOS/Unix.
    fcntl = None  # type: ignore[assignment]


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


class CredentialStoreError(RuntimeError):
    """Safe OS failure category; never include a backend's secret-bearing text."""

    def __init__(self, error: Exception):
        self.code = "credential_store_unavailable"
        self.os_status = None
        self.next_action = _credential_store_error_message()
        message = self.next_action
        current: BaseException | None = error
        seen = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            status = next((arg for arg in current.args if type(arg) is int), None)
            if sys.platform == "darwin" and status in {-25293, -25308, -128}:
                self.os_status = status
                break
            current = current.__cause__ or current.__context__
        if self.os_status == -25293:
            self.code = "keychain_auth_failed"
            message = "macOS Keychain authentication failed (OSStatus -25293) for this process"
            self.next_action = (
                "Run `agent-autofill doctor` in an interactive Terminal using the same "
                "installation. Check the agent-autofill vault item's Access Control and "
                "the Python executable's signing identity. This error alone does not "
                "mean the Keychain is locked or the executable is unsigned. If Apple's "
                "`security show-keychain-info ~/Library/Keychains/login.keychain-db` "
                "also fails, see the macOS authentication recovery steps in troubleshooting."
            )
        elif self.os_status == -25308:
            self.code = "keychain_interaction_unavailable"
            message = "macOS Keychain interaction is unavailable (OSStatus -25308)"
            self.next_action = (
                "Unlock the login Keychain and run `agent-autofill doctor` from an "
                "interactive logged-in macOS session where a permission prompt can appear."
            )
        elif self.os_status == -128:
            self.code = "keychain_request_cancelled"
            message = "The macOS Keychain request was cancelled (OSStatus -128)"
            self.next_action = "Retry when ready to respond to the macOS Keychain prompt."
        super().__init__(message if message == self.next_action else f"{message}. {self.next_action}")


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

    @contextmanager
    def _vault_lock(self):
        """Serialize vault read-modify-write operations across local processes."""

        lock_name = hashlib.sha256(self.service.encode("utf-8")).hexdigest()[:24]
        lock_path = Path(tempfile.gettempdir()) / f"agent-autofill-keyring-{lock_name}.lock"
        with lock_path.open("a+") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

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
            raise RuntimeError(f"Keychain vault contains an invalid {label}")  # noqa: TRY004 - vault corruption boundary
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

    def _load_vault(self, *, force: bool = False) -> dict:
        if self._vault is not None and not force:
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
            raise CredentialStoreError(exc) from exc

    def _persist_vault(self, vault: dict) -> None:
        serialized = json.dumps(vault, separators=(",", ":"), sort_keys=True)
        try:
            self._keyring().set_password(
                self.service, self._VAULT_ACCOUNT, serialized
            )
        except Exception as exc:
            raise CredentialStoreError(exc) from exc
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
            raise CredentialStoreError(exc) from exc

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
            raise CredentialStoreError(exc) from exc

    def load_existing(self, name: str) -> bytes:
        """Read a database key without creating or migrating any credentials."""
        vault = self._load_vault(force=True)
        encoded = vault["database_keys"].get(name)
        raw = self._decode(encoded, "database key") if encoded else self._legacy_get(name)
        if not raw or len(raw) < 16:
            raise RuntimeError("existing database key unavailable")
        return raw

    def load_or_create(self, name: str) -> bytes:
        try:
            with self._vault_lock():
                vault = self._load_vault(force=True)
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
            raise CredentialStoreError(exc) from exc

    def get_secret(self, name: str) -> bytes | None:
        vault = self._load_vault()
        encoded = vault["secrets"].get(name)
        if encoded:
            return self._decode(encoded, "connector secret")
        with self._vault_lock():
            vault = self._load_vault(force=True)
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
        with self._vault_lock():
            vault = self._load_vault(force=True)
            updated = self._copy_vault(vault)
            updated["secrets"][name] = self._encode(value)
            self._persist_vault(updated)
            self._legacy_delete(name)

    def delete_secret(self, name: str) -> None:
        with self._vault_lock():
            vault = self._load_vault(force=True)
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

    def count(self, namespace: str) -> int:
        """Count saved records without decrypting their values."""
        return self.db.execute(
            "select count(*) from kv where namespace=?", (namespace,)
        ).fetchone()[0]

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
        *,
        expected: Iterable[tuple[str, str, Any]] = (),
        cascade_field_references: bool = False,
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
            self.db.execute("begin immediate")
            for namespace, key, value in expected:
                if self.get(namespace, key) != value:
                    raise ValueError("record changed; review the current revision")
            if cascade_field_references:
                forgotten = {key for namespace, key in delete_keys if namespace == "profile"}
                dependents = []
                for namespace in ("context", "context-history", "form-mapping"):
                    for row in self.db.execute("select key,value from kv where namespace=?", (namespace,)):
                        dependents.append((namespace, row["key"], self._open(row["value"])))
                removed_bundles = {item["id"] for namespace, _, item in dependents
                                   if namespace == "context" and item.get("kind") == "bundle"
                                   and forgotten.intersection(item.get("field_ids", []))}
                for namespace, key, item in dependents:
                    if (forgotten.intersection(item.get("field_ids", []))
                            or removed_bundles.intersection(item.get("bundle_versions", {}))):
                        delete_keys.append((namespace, key))
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

    def update_if(
        self,
        namespace: str,
        key: str,
        predicate,
        update,
    ) -> bool:
        """Update one sealed value while holding SQLite's write lock."""

        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "select value from kv where namespace=? and key=?",
                (namespace, key),
            ).fetchone()
            if row is None:
                self.db.rollback()
                return False
            current = self._open(row["value"])
            if not predicate(current):
                self.db.rollback()
                return False
            updated = update(current)
            self.db.execute(
                "update kv set value=? where namespace=? and key=?",
                (self._seal(updated), namespace, key),
            )
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            raise

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
