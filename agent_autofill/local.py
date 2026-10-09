"""Local service onboarding for installed Agent Autofill clients.

The local connector deliberately keeps the service token out of command lines,
environment variables, SQLite, and JSON-RPC arguments.  A service identifies
itself by a stable name; the token used by the broker is recovered from the
existing OS credential-store vault.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re

from .service import Broker
from .storage import KeyringProvider, Store


_SERVICE_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)*$")


def normalize_service_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("service name must be a string")
    name = value.strip().lower()
    if not name or len(name) > 96 or not _SERVICE_NAME_RE.fullmatch(name):
        raise ValueError("service name must use lowercase letters, digits, dots, dashes, or underscores")
    return name


def default_database_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Find the installed broker database without requiring client config.

    Existing databases win so an installed service can be launched from any
    working directory.  A new installation uses the hidden home-directory
    path, which keeps state out of a source checkout.
    """

    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser().absolute()
    home = Path.home()
    candidates = (
        home / ".agent-autofill.sqlite3",
        home / "agent-autofill.sqlite3",
        Path.cwd() / ".agent-autofill.sqlite3",
        Path.cwd() / "agent-autofill.sqlite3",
        Path(__file__).resolve().parents[1] / "agent-autofill.sqlite3",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.absolute()
    return (home / ".agent-autofill.sqlite3").absolute()


def database_key_name(path: Path) -> str:
    return path.expanduser().absolute().as_posix()


def _token_secret_name(path: Path, service_name: str) -> str:
    identity = f"{database_key_name(path)}\0{service_name}".encode()
    fingerprint = hashlib.sha256(identity).hexdigest()[:24]
    return f"local-agent:v1:{fingerprint}"


@dataclass
class LocalConnection:
    service_name: str
    agent_id: str
    token: str
    database_path: Path
    broker: Broker
    keyring: KeyringProvider

    def close(self) -> None:
        self.broker.store.db.close()


def connect_service(
    service_name: str,
    database_path: str | os.PathLike[str] | None = None,
    keyring: KeyringProvider | None = None,
) -> LocalConnection:
    """Pair a local service with the installed broker and return its session.

    Pairing is idempotent.  A service identity recovered from the OS
    credential store is trusted for read-only broker data automatically, so an
    installed skill does not need a second grant or connect step.  Later calls
    recover the same identity and token from Keychain automatically.  Writes
    remain confirmation- and grant-protected.
    """

    service = normalize_service_name(service_name)
    path = default_database_path(database_path)
    provider = keyring or KeyringProvider()
    broker = Broker(
        Store(path, key_provider=provider, key_name=database_key_name(path))
    )
    agent_id = f"service:{service}"
    secret_name = _token_secret_name(path, service)
    raw = provider.get_secret(secret_name)
    token = raw.decode("utf-8") if raw else ""
    identity = broker.store.get("agent", agent_id)
    if token and broker.authenticate_agent(agent_id, token):
        broker.enable_trusted_local(agent_id)
        return LocalConnection(service, agent_id, token, path, broker, provider)
    if identity is not None:
        broker.store.db.close()
        if identity.get("revoked", False):
            raise RuntimeError("local service identity is revoked")
        raise RuntimeError("local service pairing is missing from the OS credential store")
    if not token:
        token = broker.create_agent(agent_id)
        broker.enable_trusted_local(agent_id)
        try:
            provider.set_secret(secret_name, token.encode("utf-8"))
        except Exception:
            # Do not leave an unusable identity in SQLite when Keychain pairing
            # fails halfway through the first-run handshake.
            broker.store.delete("agent", agent_id)
            broker.store.db.close()
            raise
    else:
        broker.store.db.close()
        raise RuntimeError("local service pairing is invalid")
    return LocalConnection(service, agent_id, token, path, broker, provider)
