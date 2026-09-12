# Palisade

Palisade is a local-first personal information broker for MCP clients. The package provides the
policy and storage core, a JSON-lines local transport, Chrome cold-start
import, and provider-neutral contracts for real mailbox and Messages bridges.

## First run

```bash
PYTHONPATH=. python -m palisade.cli --db ~/.palisade.sqlite3 init
PYTHONPATH=. python -m palisade.cli --db ~/.palisade.sqlite3 setup
PYTHONPATH=. python -m palisade.cli --db ~/.palisade.sqlite3 agent-create codex
PYTHONPATH=. python -m palisade.cli --db ~/.palisade.sqlite3 grant codex profile.read '*' --origin example.com --purpose form
PYTHONPATH=. python -m palisade.cli --db ~/.palisade.sqlite3 serve
```

The database key is stored in the operating-system credential store (Keychain
on macOS). IMAP passwords are stored there as separate entries. Chrome login
metadata is read from a read-only database snapshot; actual passwords are
accepted only from a user-exported CSV after an explicit acknowledgement and
are encrypted immediately. The broker has no password retrieval, send, delete,
or browser-submit operation.

For a password cold start, use Chrome Password Manager's Export passwords
action, keep the CSV local, run `chrome import --allow-password-import`, and
remove the CSV yourself when you have confirmed the import. The importer does
not attempt to decrypt Chrome's internal `Login Data` blobs.

The local transport accepts one JSON object per line with `method`, `id`, and
`params`. The requesting agent identifies itself with `agent_id`; data calls
also require the token created by `agent-create`, supplied as
`PALISADE_AGENT_TOKEN` to the server process.

Use `inbox-web` for the local review page, `grants`/`agents` to inspect access,
and `source-remove` to disconnect a provider and revoke its scoped grants.
