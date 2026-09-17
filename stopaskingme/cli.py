"""Command-line setup and administration for the local broker."""

import argparse
import getpass
import hashlib
import json
import secrets
from pathlib import Path
from .chrome import cold_start, discover_profiles
from .imap_adapter import (
    IMAPAdapterError,
    IMAPEmailAdapter,
)
from .google_oauth import (
    GoogleOAuthAuthorizationError,
    GoogleOAuthClient,
    GoogleOAuthConfigurationError,
    GoogleOAuthError,
    GoogleOAuthTokenError,
    GoogleOAuthTokenProvider,
    authorize_google_desktop,
)
from .service import Broker
from .storage import KeyringProvider, Store


def _store(path: str) -> Store:
    return Store(
        path,
        key_provider=KeyringProvider(),
        key_name=Path(path).expanduser().absolute().as_posix(),
    )


def _broker(path: str) -> Broker:
    return Broker(_store(path))


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

    keyring = keyring or KeyringProvider()
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
        raise SystemExit("Google OAuth provider cannot save refresh state")
    try:
        value = serializer()
    except Exception as exc:
        raise SystemExit("Google OAuth provider cannot save refresh state") from exc
    if not isinstance(value, bytes) or not value:
        raise SystemExit("Google OAuth provider returned invalid refresh state")
    try:
        # Parse and re-serialize through the canonical provider boundary.  This
        # rejects access tokens or unknown fields even when a test/custom
        # provider supplies its own serializer.
        canonical = GoogleOAuthTokenProvider.from_keychain_secret(value)
        return canonical.to_keychain_secret()
    except GoogleOAuthError as exc:
        raise SystemExit("Google OAuth provider returned invalid refresh state") from exc


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

    keyring = keyring or KeyringProvider()
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
        if auth_kind == "google_oauth":
            provider = GoogleOAuthTokenProvider.from_keychain_secret(secret)
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


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stopaskingme", description="Local personal information broker"
    )
    p.add_argument(
        "--db", default="stopaskingme.sqlite3", help="encrypted SQLite database path"
    )
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create/open the encrypted local database")
    profile = sub.add_parser("profile", help="manage local profile fields")
    ps = profile.add_subparsers(dest="profile_command", required=True)
    setp = ps.add_parser("set")
    setp.add_argument("field_id")
    setp.add_argument("display_name")
    setp.add_argument("value")
    setp.add_argument("--category", default="custom")
    setp.add_argument("--sensitivity", default="normal")
    setp.add_argument("--origin", action="append", default=[])
    setp.add_argument("--purpose", action="append", default=[])
    ps.add_parser("list")
    grant = sub.add_parser("grant", help="create an agent capability grant")
    grant.add_argument("agent_id")
    grant.add_argument("operation")
    grant.add_argument("resource_scope")
    grant.add_argument("--origin")
    grant.add_argument("--purpose")
    grant.add_argument("--expires-in", type=int, default=600)
    revoke = sub.add_parser("revoke-agent")
    revoke.add_argument("agent_id")
    agent = sub.add_parser(
        "agent-create", help="create an authenticated MCP agent identity"
    )
    agent.add_argument("agent_id")
    sub.add_parser("agents", help="list agent identities without tokens")
    creds = sub.add_parser("credentials")
    creds.add_argument("action", choices=["list"])
    financial = sub.add_parser(
        "financial-records",
        aliases=["financial"],
        help="list approved payment and financial record metadata",
    )
    financial.add_argument("action", choices=["list"], nargs="?", default="list")
    record_delete = sub.add_parser(
        "record-delete", help="delete one local profile, credential, or financial record"
    )
    record_delete.add_argument(
        "record_type", choices=["profile", "credential", "financial"]
    )
    record_delete.add_argument("record_id")
    email = sub.add_parser("email-add", help="configure a read-only IMAP account")
    email.add_argument("account_id")
    email.add_argument("host")
    email.add_argument("username")
    email.add_argument("--label")
    email.add_argument("--port", type=int, default=993)
    email.add_argument("--mailbox", default="INBOX")
    email.add_argument(
        "--oauth2",
        action="store_true",
        help="store an OAuth2 bearer token instead of an IMAP password",
    )
    gmail = sub.add_parser(
        "email-add-gmail", help="configure Gmail through a Google Desktop OAuth client"
    )
    gmail.add_argument("account_id")
    gmail.add_argument("username")
    gmail.add_argument(
        "--client-secrets",
        required=True,
        help="path to a user-supplied Google Cloud Desktop client JSON",
    )
    gmail.add_argument("--label")
    gmail.add_argument("--mailbox", default="INBOX")
    email_test = sub.add_parser(
        "email-test", help="test a saved read-only IMAP account"
    )
    email_test.add_argument("account_id")
    messages = sub.add_parser(
        "messages-add", help="configure the local macOS Messages database"
    )
    messages.add_argument("--source-id", default="messages")
    messages.add_argument("--database")
    messages.add_argument("--label", default="Mac Messages")
    sub.add_parser("sources", help="list configured sources")
    source_remove = sub.add_parser(
        "source-remove", help="disconnect a source and remove its local secret"
    )
    source_remove.add_argument("source_id")
    chrome = sub.add_parser("chrome", help="Chrome cold-start import")
    cs = chrome.add_subparsers(dest="chrome_command", required=True)
    cp = cs.add_parser("profiles")
    cp.add_argument("--root")
    ci = cs.add_parser("import")
    ci.add_argument("--profile-dir", required=True)
    ci.add_argument("--password-csv")
    ci.add_argument(
        "--allow-password-import",
        action="store_true",
        help="required acknowledgement for importing plaintext CSV secrets",
    )
    setup = sub.add_parser("setup", help="interactive first-run wizard")
    setup.add_argument("--profile-dir")
    setup.add_argument("--password-csv")
    sub.add_parser("audit", help="show redacted audit metadata")
    sub.add_parser("grants", help="show active and expired grants")
    sub.add_parser("inbox", help="show the local access review summary")
    web = sub.add_parser("inbox-web", help="serve the local access review page")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    sub.add_parser("serve", help="run the local JSON-lines MCP transport")
    return p


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "serve":
        from .server import main as serve_main

        serve_main(
            [
                "--db",
                args.db,
                "--key-name",
                Path(args.db).expanduser().absolute().as_posix(),
            ]
        )
        return 0
    if args.command == "chrome" and args.chrome_command == "profiles":
        for path in discover_profiles(args.root):
            print(path)
        return 0
    broker = _broker(args.db)
    if args.command == "init":
        print(f"Initialized encrypted broker database at {Path(args.db).absolute()}")
    elif args.command == "profile":
        if args.profile_command == "set":
            broker.put_profile(
                args.field_id,
                args.category,
                args.display_name,
                args.value,
                args.sensitivity,
                args.origin,
                args.purpose,
            )
            print(f"Stored profile field {args.field_id}")
        else:
            print(json.dumps(broker.list_profile_fields(), indent=2))
    elif args.command == "grant":
        g = broker.grant(
            args.agent_id,
            args.operation,
            args.resource_scope,
            args.origin,
            args.purpose,
            args.expires_in,
        )
        print(json.dumps(g.__dict__, indent=2))
    elif args.command == "revoke-agent":
        broker.revoke_agent(args.agent_id)
        print(f"Revoked grants for {args.agent_id}")
    elif args.command == "agent-create":
        token = broker.create_agent(args.agent_id)
        print(
            f"Agent {args.agent_id} created. Save this token; it will not be shown again:\n{token}"
        )
    elif args.command == "agents":
        print(json.dumps(broker.list_agents(), indent=2))
    elif args.command == "credentials":
        print(json.dumps(broker.list_credentials(), indent=2))
    elif args.command in {"financial-records", "financial"}:
        print(json.dumps(broker.list_financial_records(), indent=2))
    elif args.command == "record-delete":
        broker.delete_record(args.record_type, args.record_id)
        print(f"Deleted {args.record_type} record {args.record_id}")
    elif args.command == "sources":
        print(json.dumps(broker.list_sources(), indent=2))
    elif args.command == "source-remove":
        config = broker.store.get("connection", args.source_id)
        if config and config.get("secret_name"):
            try:
                KeyringProvider().delete_secret(config["secret_name"])
            except Exception as exc:
                raise SystemExit(
                    "Could not remove Keychain secret; source remains configured"
                ) from exc
        broker.remove_source(args.source_id)
        print(f"Removed source {args.source_id}")
    elif args.command == "email-add":
        keyring = KeyringProvider()
        secret = getpass.getpass(
            f"IMAP {'OAuth2 token' if args.oauth2 else 'password'} for {args.username}@{args.host} (stored in Keychain): "
        )
        config = _configure_imap(
            broker,
            args.db,
            args.account_id,
            args.host,
            args.username,
            args.port,
            args.mailbox,
            secret,
            args.oauth2,
            args.label,
            keyring,
        )
        print(
            "Configured read-only IMAP source "
            f"{config['source_id']} ({config['host']}:{config['port']}/"
            f"{config['mailbox']}, {config['username']}, {config['auth_kind']})"
        )
    elif args.command == "email-add-gmail":
        config = _configure_gmail(
            broker,
            args.db,
            args.account_id,
            args.username,
            args.client_secrets,
            args.mailbox,
            args.label,
        )
        print(
            "Configured read-only Gmail source "
            f"{config['source_id']} ({config['host']}:{config['port']}/"
            f"{config['mailbox']}, {config['username']}, {config['auth_kind']})"
        )
    elif args.command == "email-test":
        config = _test_saved_imap(broker, args.account_id, KeyringProvider())
        print(
            "Read-only IMAP source is reachable "
            f"{config['source_id']} ({config['host']}:{config.get('port', 993)}/"
            f"{config.get('mailbox', 'INBOX')}, {config['username']}, "
            f"{config.get('auth_kind', 'password')})"
        )
    elif args.command == "messages-add":
        broker.store.put(
            "connection",
            args.source_id,
            {
                "source_id": args.source_id,
                "kind": "messages",
                "label": args.label,
                "database": args.database,
            },
        )
        broker.add_source(args.source_id, "messages", args.label)
        print(f"Configured read-only Messages source {args.source_id}")
    elif args.command == "audit":
        print(json.dumps(broker.store.audits(), indent=2))
    elif args.command == "grants":
        print(json.dumps(broker.list_grants(), indent=2))
    elif args.command == "inbox":
        print(
            json.dumps(
                {
                    "sources": broker.list_sources(),
                    "profile_fields": broker.list_profile_fields(),
                    "credentials": broker.list_credentials(),
                    "financial_records": broker.list_financial_records(),
                    "grants": broker.list_grants(),
                    "recent_audit": broker.store.audits()[-20:],
                },
                indent=2,
            )
        )
    elif args.command == "inbox-web":
        from .inbox import serve

        if args.host not in {"127.0.0.1", "localhost", "::1"}:
            raise SystemExit("inbox-web only permits loopback hosts")
        print(f"Agent Inbox available at http://{args.host}:{args.port}/")
        serve(broker, args.host, args.port)
    elif args.command == "chrome":
        if args.password_csv and not args.allow_password_import:
            raise SystemExit(
                "Refusing password import: re-run with --allow-password-import after confirming the CSV is user-exported"
            )
        result = cold_start(args.profile_dir, broker, args.password_csv)
        print(json.dumps(result, indent=2))
    elif args.command == "setup":
        agent_id = input(
            "Agent identity to authorize (blank to configure later): "
        ).strip()
        if agent_id and broker.store.get("agent", agent_id) is None:
            token = broker.create_agent(agent_id)
            print(
                f"Agent {agent_id} token (save once; it will not be shown again):\n{token}"
            )
        profile_dir = args.profile_dir or (
            str(discover_profiles()[0])
            if discover_profiles()
            else input("Chrome profile directory (blank to skip): ").strip()
        )
        result = {"autofill_fields": 0, "login_metadata": 0, "passwords": 0}
        if profile_dir:
            csv_path = (
                args.password_csv
                or input("Path to Chrome password CSV (blank to skip): ").strip()
                or None
            )
            if csv_path:
                answer = input(
                    "Import passwords into encrypted local vault? type IMPORT to continue: "
                )
                if answer != "IMPORT":
                    raise SystemExit("Password import cancelled")
            result = cold_start(profile_dir, broker, csv_path)
        if (
            input("Configure a read-only IMAP mailbox now? [y/N] ").strip().lower()
            == "y"
        ):
            account_id = input("Account id: ").strip()
            host = input("IMAP host: ").strip()
            username = input("Username: ").strip()
            password = getpass.getpass("IMAP password (stored in Keychain): ")
            _configure_imap(
                broker,
                args.db,
                account_id,
                host,
                username,
                993,
                "INBOX",
                password,
            )
        if input("Configure local Mac Messages access? [y/N] ").strip().lower() == "y":
            database = (
                input("Messages chat.db path (blank for default): ").strip() or None
            )
            broker.store.put(
                "connection",
                "messages",
                {
                    "source_id": "messages",
                    "kind": "messages",
                    "label": "Mac Messages",
                    "database": database,
                },
            )
            broker.add_source("messages", "messages", "Mac Messages")
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
