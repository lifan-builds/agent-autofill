"""Minimal JSON-lines MCP-style transport for local clients."""

import argparse
import json
import os
import sys
from pathlib import Path
from .service import Broker, BrokerError
from .storage import Store, KeyringProvider
from .imap_adapter import IMAPEmailAdapter
from .messages_adapter import MacMessagesAdapter

TOOLS = [
    {
        "name": "list_sources",
        "description": "List configured information sources.",
        "inputSchema": {
            "type": "object",
            "properties": {"agent_id": {"type": "string"}},
        },
    },
    {
        "name": "list_profile_fields",
        "description": "List profile fields without values.",
        "inputSchema": {
            "type": "object",
            "properties": {"agent_id": {"type": "string"}},
        },
    },
    {
        "name": "list_credentials",
        "description": "List imported credential metadata without secrets.",
        "inputSchema": {
            "type": "object",
            "properties": {"agent_id": {"type": "string"}},
        },
    },
    {
        "name": "request_profile_fields",
        "description": "Read explicitly granted profile values for a target form.",
        "inputSchema": {
            "type": "object",
            "required": ["field_ids", "target_origin", "purpose"],
            "properties": {
                "agent_id": {"type": "string"},
                "field_ids": {"type": "array", "items": {"type": "string"}},
                "target_origin": {"type": "string"},
                "purpose": {"type": "string"},
            },
        },
    },
    {
        "name": "search_email",
        "description": "Search an explicitly granted email account.",
        "inputSchema": {
            "type": "object",
            "required": ["account_id", "query"],
            "properties": {
                "agent_id": {"type": "string"},
                "account_id": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "get_email_message",
        "description": "Read one message from an explicitly granted email account.",
        "inputSchema": {
            "type": "object",
            "required": ["account_id", "message_id"],
            "properties": {
                "agent_id": {"type": "string"},
                "account_id": {"type": "string"},
                "message_id": {"type": "string"},
            },
        },
    },
    {
        "name": "request_auth_code",
        "description": "Retrieve one recent target-bound authentication code.",
        "inputSchema": {
            "type": "object",
            "required": ["account_id", "target_origin", "purpose"],
            "properties": {
                "agent_id": {"type": "string"},
                "account_id": {"type": "string"},
                "target_origin": {"type": "string"},
                "purpose": {"type": "string"},
                "max_age_seconds": {"type": "integer"},
            },
        },
    },
    {
        "name": "list_conversations",
        "description": "List available read-only message conversations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "source_id": {"type": "string"},
            },
        },
    },
    {
        "name": "search_messages",
        "description": "Search explicitly granted read-only messages.",
        "inputSchema": {
            "type": "object",
            "required": ["conversation_scope", "query"],
            "properties": {
                "agent_id": {"type": "string"},
                "conversation_scope": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "get_message",
        "description": "Read one explicitly granted message.",
        "inputSchema": {
            "type": "object",
            "required": ["message_id"],
            "properties": {
                "agent_id": {"type": "string"},
                "message_id": {"type": "string"},
            },
        },
    },
    {
        "name": "request_credential_use",
        "description": "Request a short-lived origin-bound handle; the password is never returned.",
        "inputSchema": {
            "type": "object",
            "required": ["credential_id", "target_origin", "purpose"],
            "properties": {
                "agent_id": {"type": "string"},
                "credential_id": {"type": "string"},
                "target_origin": {"type": "string"},
                "purpose": {"type": "string"},
                "expires_in": {"type": "integer"},
            },
        },
    },
]


def _call(broker: Broker, name: str, params: dict):
    agent = params.pop("agent_id", "local-agent")
    token = params.pop("agent_token", None) or os.environ.get("STOPASKINGME_AGENT_TOKEN")
    if name not in {
        "list_sources",
        "list_profile_fields",
    } and not broker.authenticate_agent(agent, token):
        raise BrokerError("agent authentication failed")
    dispatch = {
        "list_sources": lambda: broker.list_sources(),
        "list_profile_fields": lambda: broker.list_profile_fields(),
        "list_credentials": lambda: broker.list_credentials(),
        "request_profile_fields": lambda: broker.request_profile_fields(
            agent, **params
        ),
        "search_email": lambda: broker.search_email(agent, **params),
        "get_email_message": lambda: broker.get_email_message(agent, **params),
        "request_auth_code": lambda: broker.request_auth_code(agent, **params),
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
        if config["kind"] == "imap":
            secret = keyring.get_secret(config["secret_name"])
            if secret is None:
                continue
            if config.get("auth_kind") == "oauth2":
                adapter = IMAPEmailAdapter(
                    config["source_id"],
                    config["host"],
                    config["username"],
                    None,
                    config.get("port", 993),
                    config.get("mailbox", "INBOX"),
                    secret.decode(),
                )
            else:
                adapter = IMAPEmailAdapter(
                    config["source_id"],
                    config["host"],
                    config["username"],
                    secret.decode(),
                    config.get("port", 993),
                    config.get("mailbox", "INBOX"),
                )
            broker.register_email_adapter(
                config["source_id"], adapter, config.get("label")
            )
        elif config["kind"] == "messages":
            broker.register_message_adapter(
                config["source_id"],
                MacMessagesAdapter(config.get("database")),
                config.get("label"),
            )


def main(argv=None):
    parser = argparse.ArgumentParser(prog="stopaskingme-server")
    parser.add_argument("--db", default="stopaskingme.sqlite3")
    parser.add_argument("--key-name", default=None)
    args = parser.parse_args(argv)
    keyring = KeyringProvider()
    key_name = args.key_name or Path(args.db).expanduser().absolute().as_posix()
    broker = Broker(Store(args.db, key_provider=keyring, key_name=key_name))
    _register_connections(broker, keyring)
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
                    "serverInfo": {"name": "stopaskingme", "version": "0.1.0"},
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
                            "text": json.dumps(_call(broker, tool_name, arguments)),
                        }
                    ],
                    "isError": False,
                }
            else:
                result = _call(broker, name, p)
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


if __name__ == "__main__":
    main()
