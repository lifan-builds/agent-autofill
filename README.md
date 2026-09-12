# Palisade

Palisade is a local-first personal information broker for AI agents. It gives
an authenticated MCP client one controlled surface for user-owned profile
fields, read-only email, read-only macOS Messages, and origin-bound credential
use without turning the agent into a password database reader.

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
./scripts/palisade --db ~/.palisade.sqlite3 init
./scripts/palisade --db ~/.palisade.sqlite3 setup
./scripts/palisade --db ~/.palisade.sqlite3 inbox
```

Run the MCP server with the token printed by `agent-create` in its environment:

```bash
PALISADE_AGENT_TOKEN="<token>" \
  ./scripts/palisade --db ~/.palisade.sqlite3 serve
```

The complete setup and Codex configuration are in [docs/palisade.md](docs/palisade.md).
The market and architecture research is in [RESEARCH.md](RESEARCH.md).

Palisade is information-only. It does not submit forms, send or delete
messages, control a browser, or expose a `get_password` tool. A credential-use
request returns a short-lived origin-bound handle; a trusted local helper may
consume that handle once to perform a fill without putting the password in
agent context.

## Development

```bash
PYTHONPATH=. pytest -q
ruff check palisade
```

The repository requires Python 3.11 or newer.
