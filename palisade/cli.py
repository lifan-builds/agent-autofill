"""Command-line setup and administration for the local broker."""

import argparse
import getpass
import json
from pathlib import Path
from .chrome import cold_start, discover_profiles
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


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="palisade", description="Local personal information broker"
    )
    p.add_argument(
        "--db", default="palisade.sqlite3", help="encrypted SQLite database path"
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
    elif args.command == "sources":
        print(json.dumps(broker.list_sources(), indent=2))
    elif args.command == "source-remove":
        config = broker.store.get("connection", args.source_id)
        if config and config.get("secret_name"):
            try:
                KeyringProvider().delete_secret(config["secret_name"])
            except Exception as exc:
                raise SystemExit(
                    f"Could not remove Keychain secret; source remains configured: {exc}"
                )
        broker.remove_source(args.source_id)
        print(f"Removed source {args.source_id}")
    elif args.command == "email-add":
        keyring = KeyringProvider()
        secret_name = (
            f"imap:{Path(args.db).expanduser().absolute().as_posix()}:{args.account_id}"
        )
        password = getpass.getpass(
            f"IMAP {'OAuth2 token' if args.oauth2 else 'password'} for {args.username}@{args.host} (stored in Keychain): "
        )
        keyring.set_secret(secret_name, password.encode())
        broker.store.put(
            "connection",
            args.account_id,
            {
                "source_id": args.account_id,
                "kind": "imap",
                "auth_kind": "oauth2" if args.oauth2 else "password",
                "label": args.label or args.account_id,
                "host": args.host,
                "username": args.username,
                "port": args.port,
                "mailbox": args.mailbox,
                "secret_name": secret_name,
            },
        )
        broker.add_source(args.account_id, "email", args.label or args.account_id)
        print(f"Configured read-only IMAP source {args.account_id}")
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
            keyring = KeyringProvider()
            secret_name = (
                f"imap:{Path(args.db).expanduser().absolute().as_posix()}:{account_id}"
            )
            keyring.set_secret(secret_name, password.encode())
            broker.store.put(
                "connection",
                account_id,
                {
                    "source_id": account_id,
                    "kind": "imap",
                    "label": account_id,
                    "host": host,
                    "username": username,
                    "port": 993,
                    "mailbox": "INBOX",
                    "secret_name": secret_name,
                },
            )
            broker.add_source(account_id, "email", account_id)
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
