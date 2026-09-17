import json
import os
import secrets
import sqlite3
import base64
from cryptography.fernet import Fernet, InvalidToken
from pathlib import Path
from typing import Any


class KeyProvider:
    def load_or_create(self, name: str) -> bytes:
        raise NotImplementedError


class KeyringProvider(KeyProvider):
    """Use the OS credential store (Keychain on macOS) for the DB key."""

    def __init__(self, service: str = "stopaskingme"):
        self.service = service

    def load_or_create(self, name: str) -> bytes:
        try:
            import keyring

            value = keyring.get_password(self.service, name)
            if value:
                return base64.urlsafe_b64decode(value.encode())
            raw = secrets.token_bytes(32)
            keyring.set_password(
                self.service, name, base64.urlsafe_b64encode(raw).decode()
            )
            return raw
        except Exception as exc:
            raise RuntimeError(
                "OS credential store unavailable. Install requirements.txt and unlock Keychain; test code may pass an explicit key"
            ) from exc

    def get_secret(self, name: str) -> bytes | None:
        try:
            import keyring

            value = keyring.get_password(self.service, name)
            return base64.urlsafe_b64decode(value.encode()) if value else None
        except Exception as exc:
            raise RuntimeError("OS credential store unavailable") from exc

    def set_secret(self, name: str, value: bytes) -> None:
        try:
            import keyring

            keyring.set_password(
                self.service, name, base64.urlsafe_b64encode(value).decode()
            )
        except Exception as exc:
            raise RuntimeError("OS credential store unavailable") from exc

    def delete_secret(self, name: str) -> None:
        try:
            import keyring

            keyring.delete_password(self.service, name)
        except Exception as exc:
            raise RuntimeError("OS credential store unavailable") from exc


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

    def put_many(self, records: list[tuple[str, str, Any]]) -> None:
        """Persist a small group of values atomically.

        Connector replacement writes the connection and its source metadata as
        one logical operation.  Keeping this transaction primitive here avoids
        callers having to own SQLite encryption details or leave half-written
        configurations behind when a write fails.
        """

        sealed = [
            (namespace, key, self._seal(value))
            for namespace, key, value in records
        ]
        try:
            self.db.executemany("insert or replace into kv values (?,?,?)", sealed)
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
        safe = {
            k: v
            for k, v in metadata.items()
            if k not in {"body", "code", "password", "token"}
        }
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
