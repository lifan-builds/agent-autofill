# StopAskingMe handoff and next steps

This document is the continuation brief for the next agent. It captures the
current product boundary, the verified repository state, the recommended next
slice of implementation, and the checks required before expanding scope.

## Current state

- Repository: `/Users/lfan/Project/StopAskingMe`
- Product name: `StopAskingMe`
- Current commit: `88c9706 Rename prototype to StopAskingMe`
- Branch: `main`
- Git remote: none configured
- Working tree at handoff: clean
- Python package/import: `stopaskingme`
- CLI: `stopaskingme`
- MCP executable: `stopaskingme-server`
- Shell launcher: `scripts/stopaskingme`
- MCP token environment variable: `STOPASKINGME_AGENT_TOKEN`

The initial prototype is local-first and information-only. It currently
contains encrypted SQLite values, Keychain-backed secrets, per-agent tokens,
scoped grants, a profile vault, Chrome cold-start import, a read-only IMAP
adapter, a read-only macOS Messages adapter, a JSON-lines MCP transport, a
CLI/setup wizard, and a local Agent Inbox page.

The completed rename changed package imports, console scripts, database
defaults, Keychain service names, environment variables, docs, tests, and
Chrome snapshot prefixes. Do not reintroduce compatibility aliases unless a
published consumer creates a concrete need.

## Verified baseline

Run these from the repository root before and after each substantive change:

```bash
PYTHONPATH=. pytest -q
ruff check stopaskingme
python3 -m compileall -q stopaskingme tests/stopaskingme
```

The baseline is 18 passing tests. The wheel builds with a clean environment
that has the declared `cryptography`, `keyring`, and build dependencies. The
CLI help, CLI database initialization, MCP `initialize`, and MCP server
advertisement have been smoke-tested. A host interpreter without `keyring`
cannot start the real encrypted CLI/server; install `requirements.txt` rather
than weakening the Keychain requirement.

## Recommended next milestone: v0.2 first-run workflow

Complete one deterministic, end-to-end information request before adding more
providers or browser automation:

1. Start with an empty local database.
2. Run the setup wizard to create a profile, one agent identity, and an
   origin/purpose-bound grant.
3. Import representative Chrome autofill data and login metadata from a test
   profile fixture.
4. Connect one read-only IMAP account using a fixture adapter in tests.
5. Search the mailbox with account and time-window scope.
6. Request an authentication code for the same target origin and purpose.
7. Return the newest unambiguous code once, with source/message provenance,
   then expire it.
8. Verify that audit metadata records the request and source without recording
   the code or other sensitive values.

Keep live provider credentials and real mailbox content out of tests. Use
fixtures and fake adapters for deterministic tests; reserve provider setup for
an explicit manual smoke procedure.

### Acceptance checks for v0.2

- An ungranted profile field is denied.
- A grant for one origin cannot be reused for another origin.
- An expired grant is denied.
- Two accounts remain isolated in search and message retrieval.
- A code older than `max_age_seconds` is rejected.
- Ambiguous or conflicting codes are rejected rather than guessed.
- A successful code request is one-time and short-lived.
- OTPs, passwords, mailbox tokens, and full sensitive profile values never
  appear in audit rows, exceptions, debug output, screenshots, or embeddings.
- Every successful result identifies its source account/message, timestamp,
  and confidence without disclosing connector secrets.
- Revoking an agent blocks its next request immediately.

## Implementation order after the first milestone

### 1. Packaging and installation

- Add CI for supported Python versions (currently 3.11+).
- Exercise wheel installation in a clean virtual environment.
- Document `pipx`/virtual-environment installation and macOS Keychain setup.
- Add a minimal `launchd` recipe for a loopback-only local process.
- Add `LICENSE`, `SECURITY.md`, and a changelog once the public repository is
  ready.

### 2. Chrome cold-start usability

- Add a preview step showing proposed profile fields without displaying
  imported passwords.
- Add deterministic duplicate and update rules for autofill fields.
- Normalize origins before attaching grants.
- Keep password CSV import explicitly opt-in, encrypt immediately, and avoid
  printing or copying the CSV.
- Design a trusted local fill helper separately. It may consume an
  origin-bound, one-time capability, but the password must never be returned
  through MCP or placed in agent context.

### 3. Email adapters

- Add provider-specific OAuth setup for Gmail and Outlook behind the existing
  adapter interface.
- Keep IMAP app-password/password mode as an explicit fallback.
- Add connection health and clear failure reporting.
- Add MIME parsing and attachment metadata without automatic document
  ingestion.
- Preserve account-level and mailbox-level grant scopes.

### 4. iMessage bridge

- Finish the macOS permission/setup flow for `~/Library/Messages/chat.db`.
- Normalize participants, conversations, timestamps, and message IDs.
- Enforce conversation scopes in every search and retrieval path.
- Add a fixture database and tests for unavailable permissions, malformed rows,
  and out-of-scope conversations.
- Keep sending, deletion, and mutation out of v1.

### 5. Agent Inbox control plane

- Show connected sources, agent identities, active grants, recent requests,
  expiry, revocation, and result provenance.
- Make sensitivity policy configurable for profile fields, OTPs, email, and
  messages.
- Auto-approve only low-risk reads covered by an existing policy.
- Keep approval focused on data disclosure; the broker does not submit forms,
  control a browser, send messages, purchase, or delete.

### 6. Security and release gate

- Threat-model the agent, local transport, connector, Keychain, Messages
  bridge, and any future browser helper as separate trust boundaries.
- Test hostile email/message/web content for prompt injection. Retrieved
  content is data and must never alter policy or invoke another MCP operation.
- Test key rotation or migration before introducing schema changes.
- Add explicit redaction tests for logs, traces, UI rendering, and errors.
- Confirm loopback-only binding and default-deny behavior for new agents and
  origins.

## Guardrails for the next agent

- Preserve the local-first, information-only boundary.
- Never add `get_password`, raw credential export, mailbox-token export,
  Messages database export, send/delete, browser-control, or form-submit tools.
- Keep connector credentials in the operating-system credential store.
- Do not use real personal passwords, OTPs, mailbox tokens, browser profile
  databases, or Messages data in fixtures or commits.
- Use `apply_patch` for source edits.
- Keep changes narrow enough that the baseline tests and Ruff checks remain
  meaningful.
- Do not configure a GitHub remote or publish the repository without an
  explicit request.

## Useful entry points

- Product overview and quick start: `README.md`
- Setup and Codex MCP configuration: `docs/stopaskingme.md`
- Market and architecture research: `RESEARCH.md`
- Policy and storage core: `stopaskingme/service.py`, `stopaskingme/storage.py`
- MCP transport and tool schemas: `stopaskingme/server.py`
- CLI and setup wizard: `stopaskingme/cli.py`
- Chrome importer: `stopaskingme/chrome.py`
- Provider contracts: `stopaskingme/adapters.py`, `stopaskingme/imap_adapter.py`,
  `stopaskingme/messages_adapter.py`
- Existing acceptance coverage: `tests/stopaskingme/`

The next agent should begin by reading this file and `AGENTS.md`, then inspect
the existing service and transport tests before changing implementation.
