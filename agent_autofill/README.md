# Package guide

The root [README](../README.md) is the user-facing guide. This file is for
contributors working inside the Python package.

## Module map

| Module | Responsibility |
| --- | --- |
| `service.py` | Broker policy, authorization, metadata matching, and scoped reads |
| `storage.py` | Encrypted SQLite and operating-system credential-store access |
| `server.py` | Authenticated JSON-lines/MCP transport and dispatch |
| `catalog.py` | MCP tool names, schemas, and capability descriptions |
| `status.py` | Value-free readiness projections used by `quickstart`, `doctor`, and the CLI |
| `cli.py` | Argument parsing and reviewed local administration flows |
| `cli_connectors.py` | Reviewed IMAP, Gmail OAuth, and Thunderbird connector setup |
| `imap_adapter.py` | Read-only IMAP access |
| `google_oauth.py` | Desktop OAuth and in-memory access-token handling |
| `messages_adapter.py` | Read-only macOS Messages access |
| `chrome.py` | Read-only Chrome discovery and reviewed credential import |
| `fill_server.py` | Loopback-only one-time credential-handle bridge |
| `local.py` | Keychain-paired local service connections |
| `thunderbird.py` | Read-only Thunderbird OAuth discovery and verification |

The package exports the core broker and local connection types:

```python
from agent_autofill import Broker, LocalConnection, connect_service
```

## Compatibility boundaries

Keep `agent_autofill.cli.main` as the executable entry point and
`agent_autofill.server` as the transport boundary. Keep authorization and
redaction decisions in `Broker`; the CLI, dashboard, extension, and transport
must not recreate those rules independently.

Agent Autofill is information-only. New code must not add browser navigation,
form submission, outbound messaging, automatic site verification, unscoped
password retrieval, or connector-secret logging.

Credential delivery is deliberately explicit:

- `request_credential(delivery="direct")` returns only approved field IDs.
- `request_credential(delivery="extension")` returns a short-lived handle for
  the local extension, keeping the password out of agent input.

## Checks while editing

Run focused tests for the module you touched, then the complete gate:

```bash
PYTHONPATH=. pytest -q tests/agent_autofill/test_service.py
PYTHONPATH=. pytest -q
ruff check agent_autofill tests/agent_autofill
mypy agent_autofill
```

Use synthetic values in tests. Never place real passwords, mailbox tokens,
OTPs, payment values, or one-time handles in fixtures, docs, logs, or review
artifacts. See the [operator guide](../docs/agent-autofill.md) for setup and
the [backend specs](../.trellis/spec/backend/index.md) for implementation
contracts.
