# StopAskingMe

This package is a local MCP information service. It keeps profile values,
connector configuration, credentials imported during cold start, grants, and
audit metadata in an encrypted application store. Connector secrets are held
separately in macOS Keychain through Python `keyring`.

## Setup

Install the two small runtime dependencies first:

```bash
python3 -m pip install -r /Users/lfan/Project/StopAskingMe/requirements.txt
```

```bash
PYTHONPATH=/Users/lfan/Project/StopAskingMe python -m stopaskingme.cli \
  --db /Users/lfan/.stopaskingme.sqlite3 init
PYTHONPATH=/Users/lfan/Project/StopAskingMe python -m stopaskingme.cli \
  --db /Users/lfan/.stopaskingme.sqlite3 setup
PYTHONPATH=/Users/lfan/Project/StopAskingMe python -m stopaskingme.cli \
  --db /Users/lfan/.stopaskingme.sqlite3 agent-create codex
PYTHONPATH=/Users/lfan/Project/StopAskingMe python -m stopaskingme.cli \
  --db /Users/lfan/.stopaskingme.sqlite3 grant codex profile.read name \
  --origin example.com --purpose form
PYTHONPATH=/Users/lfan/Project/StopAskingMe python -m stopaskingme.cli \
  --db /Users/lfan/.stopaskingme.sqlite3 grant codex credential.use <credential-id> \
  --origin https://example.com --purpose form
```

The wizard can import Chrome autofill data and login metadata from a selected
profile, optionally import a user-exported Chrome password CSV after typing
`IMPORT`, and configure read-only IMAP and local Messages sources. The broker
never attempts to decrypt Chrome's `Login Data` database and never prints an
imported password.

macOS may require granting the terminal or Python process Full Disk Access to
read `~/Library/Messages/chat.db`; the adapter reports a clear unavailable
error when that permission is absent.

To run the local MCP process for a client:

```bash
PYTHONPATH=/Users/lfan/Project/StopAskingMe python -m stopaskingme.cli \
  --db /Users/lfan/.stopaskingme.sqlite3 serve
```

For a human review page, run `inbox-web` and open the printed loopback URL:

```bash
PYTHONPATH=/Users/lfan/Project/StopAskingMe python -m stopaskingme.cli \
  --db /Users/lfan/.stopaskingme.sqlite3 inbox-web
```

The process uses MCP `initialize`, `tools/list`, and `tools/call` JSON-RPC
messages over stdin/stdout. A client should launch it as a local stdio server
and set the one-time token from `agent-create` as `STOPASKINGME_AGENT_TOKEN` in the
MCP process environment. The stable identity is passed as `agent_id` in tool
arguments. Create grants with the CLI; grants are short-lived and default-deny.

A Codex MCP entry follows this shape (keep the token in the process environment
or a secret manager, not in source control):

```toml
[mcp_servers.stopaskingme]
command = "/Users/lfan/Project/StopAskingMe/scripts/stopaskingme"
args = ["--db", "/Users/lfan/.stopaskingme.sqlite3", "serve"]
```

The information surface is intentionally read-only. It does not expose
password retrieval, send/delete operations, browser control, or form submit.
For imported credentials, `request_credential_use` returns an origin-bound,
short-lived handle plus the username; a trusted local browser helper may
consume that handle exactly once to perform a fill. The MCP response never
contains the password.

Disconnect a mailbox or Messages source with `source-remove`; this removes its
local connection record, deletes its Keychain secret when present, and revokes
grants scoped to that source:

```bash
PYTHONPATH=/Users/lfan/Project/StopAskingMe python -m stopaskingme.cli \
  --db /Users/lfan/.stopaskingme.sqlite3 source-remove personal-mail
```
