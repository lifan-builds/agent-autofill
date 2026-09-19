# Agent Autofill

Agent Autofill is a local-first personal information broker for AI agents. It gives
an authenticated MCP client one controlled surface for user-owned profile
fields, read-only email, read-only macOS Messages, and scoped website
credential/financial records without turning the agent into an unscoped
password database reader.

The first implementation is intentionally small and runnable:

- encrypted SQLite values with an operating-system credential-store key;
- short-lived grants and per-agent tokens;
- generic IMAP/OAuth2 email adapter;
- read-only macOS Messages adapter;
- Chrome autofill and login-metadata cold start, plus explicit user-exported
  password CSV import;
- MCP JSON-RPC over stdio and a local Agent Inbox review page.

## Quick start

```bash
python3 -m pip install -r requirements.txt
./scripts/agent-autofill --db ~/.agent-autofill.sqlite3 init
./scripts/agent-autofill --db ~/.agent-autofill.sqlite3 setup
./scripts/agent-autofill --db ~/.agent-autofill.sqlite3 inbox
```

Run the MCP server with the token printed by `agent-create` in its environment:

```bash
AGENT_AUTOFILL_AGENT_TOKEN="<token>" \
  ./scripts/agent-autofill --db ~/.agent-autofill.sqlite3 serve
```

The complete setup and Codex configuration are in [docs/agent-autofill.md](docs/agent-autofill.md).
The market and architecture research is in [RESEARCH.md](RESEARCH.md).

Agent Autofill is information-only. It does not submit forms, send or delete
messages, control a browser, or expose an unscoped `get_password` tool. New
field-selective credential and financial reads return only values covered by
an authenticated, short-lived grant and matching record scope; the consuming
agent owns any separate browser approval.

## Development

```bash
PYTHONPATH=. pytest -q
ruff check agent_autofill
```

The repository requires Python 3.11 or newer.
