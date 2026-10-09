"""Minimal JSON-lines MCP-style transport for local clients."""

import argparse
import json
import os
import sys
from .catalog import TOOLS
from .service import Broker, BrokerError
from .storage import Store, KeyringProvider
from .imap_adapter import IMAPEmailAdapter
from .google_oauth import GoogleOAuthError, oauth_provider_from_keychain_secret
from .messages_adapter import MacMessagesAdapter
from .local import LocalConnection, connect_service, default_database_path



def _call(
    broker: Broker,
    name: str,
    params: dict,
    connection: LocalConnection | None = None,
):
    requested_agent = params.pop("agent_id", None)
    agent: str
    token: str | None
    if connection is not None:
        # A bound service cannot impersonate another local identity through
        # request arguments.  Its token never enters JSON-RPC or the process
        # environment.
        agent = connection.agent_id
        token = connection.token
    else:
        agent = requested_agent or "local-agent"
        token = params.pop("agent_token", None) or os.environ.get(
            "AGENT_AUTOFILL_AGENT_TOKEN"
        )
    if name not in {
        "list_sources",
        "list_profile_fields",
        "list_profile_groups",
        "match_email_sources",
    } and not broker.authenticate_agent(agent, token):
        raise BrokerError("agent authentication failed")
    dispatch = {
        "list_sources": lambda: broker.list_sources(**params),
        "list_profile_fields": lambda: broker.list_profile_fields(**params),
        "list_profile_groups": lambda: broker.list_profile_groups(),
        "list_credentials": lambda: broker.list_credentials(**params),
        "match_credentials": lambda: broker.match_credentials(**params),
        "list_financial_records": lambda: broker.list_financial_records(**params),
        "match_financial_records": lambda: broker.match_financial_records(**params),
        "write_records": lambda: broker.write_records(agent, **params),
        "request_credential_fields": lambda: broker.request_credential_fields(
            agent, **params
        ),
        "request_credential": lambda: broker.request_credential(agent, **params),
        "request_financial_fields": lambda: broker.request_financial_fields(
            agent, **params
        ),
        "request_profile_fields": lambda: broker.request_profile_fields(
            agent, **params
        ),
        "search_email": lambda: broker.search_email(agent, **params),
        "get_email_message": lambda: broker.get_email_message(agent, **params),
        "request_auth_code": lambda: broker.request_auth_code(agent, **params),
        "request_auth_code_for_recipient": lambda: broker.request_auth_code_for_recipient(
            agent, **params
        ),
        "match_email_sources": lambda: broker.match_email_sources(**params),
        "request_verification_link_for_recipient": lambda: broker.request_verification_link_for_recipient(
            agent, **params
        ),
        "search_messages": lambda: broker.search_messages(agent, **params),
        "list_conversations": lambda: broker.list_conversations(agent, **params),
        "get_message": lambda: broker.get_message(agent, **params),
        "request_credential_use": lambda: broker.request_credential_use(
            agent, **params
        ),
    }
    if name not in dispatch:
        raise BrokerError(f"unknown method: {name}")
    return dispatch[name]()


def _register_connections(broker: Broker, keyring: KeyringProvider) -> None:
    for row in broker.store.db.execute(
        "select key from kv where namespace='connection'"
    ):
        config = broker.store.get("connection", row["key"])
        if not config:
            continue
        if config.get("kind") == "imap":
            source_id = config.get("source_id", row["key"])
            if source_id not in broker.sources:
                broker.add_source(
                    source_id, "email", config.get("label") or source_id
                )
            secret_name = config.get("secret_name")
            if not secret_name:
                broker.mark_email_unavailable(source_id)
                continue
            try:
                secret = keyring.get_secret(secret_name)
            except Exception:
                broker.mark_email_unavailable(source_id)
                continue
            if not secret:
                broker.mark_email_unavailable(source_id)
                continue
            try:
                auth_kind = config.get("auth_kind")
                if auth_kind in {"google_oauth", "microsoft_oauth"}:
                    def save_refreshed_secret(value: bytes, name: str = secret_name) -> None:
                        keyring.set_secret(name, value)

                    provider = oauth_provider_from_keychain_secret(
                        secret,
                        on_refresh=save_refreshed_secret,
                    )
                    adapter = IMAPEmailAdapter(
                        source_id,
                        config["host"],
                        config["username"],
                        None,
                        config.get("port", 993),
                        config.get("mailbox", "INBOX"),
                        None,
                        provider,
                    )
                else:
                    value = secret.decode()
                    if auth_kind == "oauth2":
                        adapter = IMAPEmailAdapter(
                            source_id,
                            config["host"],
                            config["username"],
                            None,
                            config.get("port", 993),
                            config.get("mailbox", "INBOX"),
                            value,
                        )
                    else:
                        adapter = IMAPEmailAdapter(
                            source_id,
                            config["host"],
                            config["username"],
                            value,
                            config.get("port", 993),
                            config.get("mailbox", "INBOX"),
                        )
            except (
                GoogleOAuthError,
                KeyError,
                UnicodeDecodeError,
                TypeError,
                ValueError,
            ):
                broker.mark_email_unavailable(source_id)
                continue
            broker.register_email_adapter(
                source_id, adapter, config.get("label")
            )
        elif config.get("kind") == "messages":
            broker.register_message_adapter(
                config["source_id"],
                MacMessagesAdapter(config.get("database")),
                config.get("label"),
            )


def main(argv=None):
    parser = argparse.ArgumentParser(prog="agent-autofill-server")
    parser.add_argument("--db", default=None)
    parser.add_argument("--key-name", default=None)
    parser.add_argument(
        "--service",
        help="bind this process to a stable local service identity and recover its token from Keychain",
    )
    args = parser.parse_args(argv)
    connection = None
    try:
        if args.service:
            connection = connect_service(args.service, args.db)
            keyring = connection.keyring
            broker = connection.broker
        else:
            keyring = KeyringProvider()
            db_path = default_database_path(args.db)
            key_name = args.key_name or db_path.expanduser().absolute().as_posix()
            broker = Broker(Store(db_path, key_provider=keyring, key_name=key_name))
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    _register_connections(broker, keyring)
    try:
        for line in sys.stdin:
            req = {}
            try:
                req = json.loads(line)
                name = req.get("method")
                p = dict(req.get("params", {}))
                req_id = req.get("id")
                if name == "notifications/initialized":
                    continue
                if name == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "agent-autofill", "version": "0.1.0"},
                    }
                elif name == "tools/list":
                    result = {"tools": TOOLS}
                elif name == "tools/call":
                    tool_name = p.get("name")
                    arguments = dict(p.get("arguments") or {})
                    result = {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    _call(broker, tool_name, arguments, connection)
                                ),
                            }
                        ],
                        "isError": False,
                    }
                else:
                    result = _call(broker, name, p, connection)
                if req_id is not None:
                    print(
                        json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}),
                        flush=True,
                    )
            except Exception as e:
                print(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": req.get("id"),
                            "error": {
                                "code": -32000,
                                "type": type(e).__name__,
                                "message": str(e),
                            },
                        }
                    ),
                    flush=True,
                )
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    main()
