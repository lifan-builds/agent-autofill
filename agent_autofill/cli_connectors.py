"""Reviewed connector setup flows used by the local CLI."""

import hashlib
import json
import secrets
from pathlib import Path
from typing import Any

from .google_oauth import (
    GoogleOAuthAuthorizationError,
    GoogleOAuthClient,
    GoogleOAuthConfigurationError,
    GoogleOAuthError,
    GoogleOAuthTokenError,
    authorize_google_desktop,
    oauth_provider_from_keychain_secret,
)
from .imap_adapter import IMAPAdapterError, IMAPEmailAdapter
from .service import Broker
from .storage import KeyringProvider
from .thunderbird import verify_profile

def _imap_secret_name(db_path: str, account_id: str) -> str:
    """Return a fresh opaque Keychain lookup name for an account replacement."""

    identity = (
        f"{Path(db_path).expanduser().absolute().as_posix()}\0{account_id}"
    ).encode()
    fingerprint = hashlib.sha256(identity).hexdigest()[:16]
    return f"imap:v1:{fingerprint}:{secrets.token_hex(16)}"


def _imap_adapter(
    account_id: str,
    host: str,
    username: str,
    port: int,
    mailbox: str,
    secret: str,
    oauth2: bool,
) -> IMAPEmailAdapter:
    if oauth2:
        return IMAPEmailAdapter(
            account_id,
            host,
            username,
            None,
            port,
            mailbox,
            secret,
        )
    return IMAPEmailAdapter(
        account_id,
        host,
        username,
        secret,
        port,
        mailbox,
    )


def _save_imap_configuration(
    broker: Broker,
    db_path: str,
    account_id: str,
    config: dict,
    source: dict,
    secret: bytes,
    keyring: KeyringProvider,
    *,
    provider_name: str = "IMAP",
) -> dict:
    """Save connector state after verification, preserving replacements safely."""

    previous = broker.store.get("connection", account_id)
    secret_name = config["secret_name"]
    try:
        keyring.set_secret(secret_name, secret)
    except Exception as exc:
        try:
            keyring.delete_secret(secret_name)
        except Exception:
            pass
        raise SystemExit(
            f"Could not save {provider_name} connector secret"
        ) from exc

    try:
        broker.store.put_many(
            [("connection", account_id, config), ("source", account_id, source)]
        )
    except Exception as exc:
        try:
            keyring.delete_secret(secret_name)
        except Exception as cleanup_exc:
            raise SystemExit(
                f"Could not save {provider_name} configuration or clean up its temporary connector secret"
            ) from cleanup_exc
        raise SystemExit(f"Could not save {provider_name} configuration") from exc

    broker.sources[account_id] = source
    old_secret_name = (previous or {}).get("secret_name")
    if old_secret_name and old_secret_name != secret_name:
        try:
            keyring.delete_secret(old_secret_name)
        except Exception as exc:
            raise SystemExit(
                f"Saved the new {provider_name} configuration but could not remove the previous connector secret"
            ) from exc
    return config


def _configure_imap(
    broker: Broker,
    db_path: str,
    account_id: str,
    host: str,
    username: str,
    port: int,
    mailbox: str,
    secret: str,
    oauth2: bool = False,
    label: str | None = None,
    keyring: KeyringProvider | None = None,
) -> dict:
    """Verify and atomically save a generic IMAP source.

    The connector is tested in memory before its secret reaches Keychain.  A
    new opaque name is written for every replacement, and the old name is
    removed only after both encrypted metadata records commit.
    """

    adapter = _imap_adapter(
        account_id, host, username, port, mailbox, secret, oauth2
    )
    try:
        adapter.check_connection()
    except IMAPAdapterError as exc:
        raise SystemExit(str(exc)) from exc
    except Exception as exc:
        raise SystemExit("IMAP connection check failed") from exc

    if keyring is None:
        keyring = KeyringProvider()
    secret_name = _imap_secret_name(db_path, account_id)
    auth_kind = "oauth2" if oauth2 else "password"
    source_label = label or account_id
    config = {
        "source_id": account_id,
        "kind": "imap",
        "auth_kind": auth_kind,
        "label": source_label,
        "host": host,
        "username": username,
        "port": port,
        "mailbox": mailbox,
        "secret_name": secret_name,
    }
    source = {"source_id": account_id, "kind": "email", "label": source_label}
    config["secret_name"] = secret_name
    return _save_imap_configuration(
        broker,
        db_path,
        account_id,
        config,
        source,
        secret.encode(),
        keyring,
    )


def _oauth_keychain_secret(provider: object) -> bytes:
    """Serialize only the provider's refresh/client state for Keychain."""

    serializer = getattr(provider, "to_keychain_secret", None)
    if serializer is None:
        serializer = getattr(provider, "to_secret", None)
    if serializer is None or not callable(serializer):
        raise SystemExit("OAuth provider cannot save refresh state")
    try:
        value = serializer()
    except Exception as exc:
        raise SystemExit("OAuth provider cannot save refresh state") from exc
    if not isinstance(value, bytes) or not value:
        raise SystemExit("OAuth provider returned invalid refresh state")
    try:
        # Parse and re-serialize through the canonical provider boundary.  This
        # rejects access tokens or unknown fields even when a test/custom
        # provider supplies its own serializer.
        canonical = oauth_provider_from_keychain_secret(value)
        return canonical.to_keychain_secret()
    except GoogleOAuthError as exc:
        raise SystemExit("OAuth provider returned invalid refresh state") from exc


def _configure_gmail(
    broker: Broker,
    db_path: str,
    account_id: str,
    username: str,
    client_secrets: str | Path,
    mailbox: str = "INBOX",
    label: str | None = None,
    keyring: KeyringProvider | None = None,
    *,
    oauth_provider: object | None = None,
    authorize_fn=None,
) -> dict:
    """Authorize, verify, and persist a read-only Gmail IMAP source."""

    if oauth_provider is None:
        try:
            client = GoogleOAuthClient.from_file(client_secrets)
            oauth_provider = (authorize_fn or authorize_google_desktop)(client)
        except GoogleOAuthConfigurationError as exc:
            raise SystemExit("Google OAuth client configuration is invalid") from exc
        except GoogleOAuthAuthorizationError as exc:
            raise SystemExit("Google authorization failed") from exc
        except GoogleOAuthTokenError as exc:
            raise SystemExit("Google OAuth token exchange failed") from exc
        except GoogleOAuthError as exc:
            raise SystemExit("Google OAuth setup failed") from exc
        except (OSError, TypeError, ValueError) as exc:
            raise SystemExit("Google OAuth setup failed") from exc
        except Exception as exc:
            raise SystemExit("Google OAuth setup failed") from exc

    adapter = IMAPEmailAdapter(
        account_id,
        "imap.gmail.com",
        username,
        None,
        993,
        mailbox,
        None,
        oauth_provider,
    )
    try:
        adapter.check_connection()
    except IMAPAdapterError as exc:
        raise SystemExit(str(exc)) from exc
    except Exception as exc:
        raise SystemExit("Gmail IMAP connection check failed") from exc

    if keyring is None:
        keyring = KeyringProvider()
    source_label = label or account_id
    config = {
        "source_id": account_id,
        "kind": "imap",
        "auth_kind": "google_oauth",
        "label": source_label,
        "host": "imap.gmail.com",
        "username": username,
        "port": 993,
        "mailbox": mailbox,
        "secret_name": _imap_secret_name(db_path, account_id),
    }
    source = {"source_id": account_id, "kind": "email", "label": source_label}
    return _save_imap_configuration(
        broker,
        db_path,
        account_id,
        config,
        source,
        _oauth_keychain_secret(oauth_provider),
        keyring,
        provider_name="Gmail OAuth",
    )


def _test_saved_imap(broker: Broker, account_id: str, keyring: KeyringProvider) -> dict:
    config = broker.store.get("connection", account_id)
    if not config or config.get("kind") != "imap":
        raise SystemExit(f"No saved IMAP source named {account_id}")
    secret_name = config.get("secret_name")
    if not secret_name:
        raise SystemExit("Saved IMAP connector secret is unavailable")
    try:
        secret = keyring.get_secret(secret_name)
    except Exception as exc:
        raise SystemExit("Saved IMAP connector secret is unavailable") from exc
    if not secret:
        raise SystemExit("Saved IMAP connector secret is unavailable")
    try:
        source_id = config.get("source_id", account_id)
        host = config["host"]
        username = config["username"]
        port = config.get("port", 993)
        mailbox = config.get("mailbox", "INBOX")
        auth_kind = config.get("auth_kind")
        if auth_kind in {"google_oauth", "microsoft_oauth"}:
            provider = oauth_provider_from_keychain_secret(
                secret, on_refresh=lambda value: keyring.set_secret(secret_name, value)
            )
            adapter = IMAPEmailAdapter(
                source_id,
                host,
                username,
                None,
                port,
                mailbox,
                None,
                provider,
            )
        else:
            value = secret.decode()
            adapter = _imap_adapter(
                source_id,
                host,
                username,
                port,
                mailbox,
                value,
                auth_kind == "oauth2",
            )
    except GoogleOAuthConfigurationError as exc:
        raise SystemExit("Saved Google OAuth state is invalid") from exc
    except GoogleOAuthTokenError as exc:
        raise SystemExit("Saved Google OAuth token refresh failed") from exc
    except GoogleOAuthError as exc:
        raise SystemExit("Saved Google OAuth state is invalid") from exc
    except (KeyError, UnicodeDecodeError, TypeError, ValueError) as exc:
        raise SystemExit("Saved IMAP connector configuration is invalid") from exc
    try:
        adapter.check_connection()
    except IMAPAdapterError as exc:
        raise SystemExit(str(exc)) from exc
    except Exception as exc:
        raise SystemExit("IMAP connection check failed") from exc
    return config


def _import_thunderbird(
    broker: Broker,
    db_path: str,
    profile: str | Path,
    account_ids: list[str] | None,
    keyring: KeyringProvider,
    *,
    confirm_import: bool = False,
    nss_factory=None,
    adapter_factory=None,
) -> list[dict]:
    """Verify and optionally persist Thunderbird OAuth connectors.

    The verification pass completes before any Keychain or SQLite mutation.
    Only the provider's canonical refresh/client payload crosses into the
    existing Keychain-backed connector writer.
    """

    verified: dict[str, tuple[Any, Any]] = {}
    verify_kwargs = {
        "profile": profile,
        "account_ids": account_ids,
        "verified_out": verified,
    }
    if nss_factory is not None:
        verify_kwargs["nss_factory"] = nss_factory
    if adapter_factory is not None:
        verify_kwargs["adapter_factory"] = adapter_factory
    run = verify_profile(**verify_kwargs)  # type: ignore[arg-type]
    failures = [item for item in run.results if item.status != "PASS"]
    if failures:
        raise SystemExit(
            "Refusing Thunderbird import because one or more selected accounts failed verification"
        )
    proposal: list[dict[str, Any]] = [
        {
            "account_id": item.account_id,
            "username": item.masked_username,
            "provider": getattr(provider, "KEYCHAIN_PROVIDER", "unknown"),
            "source_id": item.account_id.replace("account", "thunderbird-account", 1),
        }
        for item, provider in verified.values()
    ]
    if not confirm_import:
        print("Proposed Thunderbird Keychain imports (tokens omitted):")
        print(json.dumps({"accounts": proposal}, indent=2))
        raise SystemExit(
            "Refusing Thunderbird import: re-run with --confirm-import after reviewing the proposal"
        )

    imported: list[dict] = []
    for account, provider in verified.values():
        provider_kind = getattr(provider, "KEYCHAIN_PROVIDER", None)
        if provider_kind not in {"google", "microsoft"}:
            raise SystemExit("Unsupported Thunderbird OAuth provider")
        source_id = account.source_id
        auth_kind = f"{provider_kind}_oauth"
        config = {
            "source_id": source_id,
            "kind": "imap",
            "auth_kind": auth_kind,
            "label": f"Thunderbird {account.masked_username}",
            "host": account.host,
            "username": account.username,
            "port": account.port,
            "mailbox": account.mailbox,
            "secret_name": _imap_secret_name(db_path, source_id),
        }
        source = {
            "source_id": source_id,
            "kind": "email",
            "label": f"Thunderbird {account.masked_username}",
        }
        saved = _save_imap_configuration(
            broker,
            db_path,
            source_id,
            config,
            source,
            _oauth_keychain_secret(provider),
            keyring,
            provider_name="Thunderbird OAuth",
        )
        imported.append(
            {
                "source_id": saved["source_id"],
                "username": account.masked_username,
                "provider": provider_kind,
                "status": "IMPORTED",
            }
        )
    verified.clear()
    return imported
