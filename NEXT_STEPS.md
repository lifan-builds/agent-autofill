# Agent Autofill handoff and next steps

This document is the continuation brief for the next agent. It captures the
current product boundary, the verified repository state, the recommended next
slice of implementation, and the checks required before expanding scope.

## Current state

- Repository: `/Users/lfan/Project/agent-autofill`
- Product name: `Agent Autofill`
- Branch: `main`
- Git remote: `https://github.com/lifan-builds/agent-autofill.git`
- Working tree state: inspect with `git status`
- Python package/import: `agent_autofill`
- CLI: `agent-autofill`
- MCP executable: `agent-autofill-server`
- Installed CLI: `agent-autofill`
- Checkout fallback: `scripts/agent-autofill`
- MCP token environment variable: `AGENT_AUTOFILL_AGENT_TOKEN`

The initial prototype is local-first and information-only. It currently
contains encrypted SQLite values, Keychain-backed secrets, per-agent tokens,
scoped grants, a profile vault, Chrome cold-start import, a read-only IMAP
adapter, a read-only macOS Messages adapter, a JSON-lines MCP transport, a
CLI/setup wizard, and a local Agent Inbox page.

The current usability slice is implemented and covered by focused tests:

- exact-host `match_credentials` and metadata-only `list_profile_groups` MCP
  tools;
- Chrome Password Manager preview/sync with add/update/unchanged/stale counts;
  stale deletion is opt-in through `--remove-stale`;
- `grant-site`, which previews usernames and scopes before creating a
  short-lived site grant;
- a loopback-only `fill-server` and unpacked Chrome extension that consumes one
  origin-bound handle and fills fields without navigating or submitting;
- the Agent Inbox review page continues to expose sources, agents, grants, and
  recent metadata without values or secrets.

Gmail OAuth persistence and authenticated, scoped credential/financial field
reads are delivered. They remain outside the optional first-run grant step:
that step proposes only the exact profile, email-search, and email-OTP grants
described below.

The completed rename changed package imports, console scripts, database
defaults, Keychain service names, environment variables, docs, tests, and
Chrome snapshot prefixes. Do not reintroduce compatibility aliases unless a
published consumer creates a concrete need.

## Verified baseline

Run these from the repository root before and after each substantive change:

```bash
PYTHONPATH=. pytest -q
ruff check agent_autofill
python3 -m compileall -q agent_autofill tests/agent_autofill
```

The wheel builds with a clean environment that has the declared `cryptography`,
`keyring`, and build dependencies. CLI help, CLI database initialization, MCP
`initialize`, and MCP server advertisement have been smoke-tested. A host
interpreter without `keyring` cannot start the real encrypted CLI/server;
install the project with `uv tool install .` or `pipx install .` rather than
weakening the Keychain requirement.

## Completed milestone: first-run authorized workflow

The setup wizard offers an optional authorization step after the identity,
Chrome import, and connector prompts. It is available only when an agent
identity is present. The step proposes, independently:

- `profile.read` for exact saved profile field IDs;
- `email.search` for one exact configured email account (account-scoped,
  without origin/purpose); and
- `email.otp` for that account, with a nonempty target origin and purpose.

Profile and OTP selections require nonempty origin and purpose. The wizard
does not create wildcard, write, credential, financial, Messages, or
`email.read` grants. It shows a value-free preview containing the exact
operations, resources, scopes, and fixed 600-second lifetime, and persists
no proposed grants until the operator explicitly confirms. Skipping, cancelling, leaving
the identity blank, selecting an invalid or incomplete scope, or declining the
preview creates no new grants. Existing tokens and grants remain intact, and a
revoked identity is not silently reactivated. Earlier imports and connector
configuration are not rolled back when this step is skipped.

The verified fixture path is:

1. Start with an empty temporary encrypted database.
2. Run the setup entry point with synthetic Chrome profile/login metadata and
   a fake persisted IMAP source.
3. Reopen the database through the server entry point and issue authenticated
   JSON-lines profile and benign account-scoped email-search requests.
4. Request one recent origin-bound OTP. The broker accepts exactly one recent
   candidate and then checks its target-host binding; it does not select the
   newest candidate or return confidence/expiry fields. Consumption is keyed
   by agent, account, and message, so the same agent cannot reuse the message.
5. Verify that audits and setup output remain value-free while the successful
   OTP response alone contains the synthetic code and provenance.

The existing `grant` CLI command remains the renewal path after a wizard grant
expires. Keep live provider credentials and real mailbox content out of tests.
Fixture entry-point coverage proves persistence, connector reconstruction, and
JSON-lines behavior with fakes; it does not establish installation, live
Keychain, live IMAP, or live Gmail/provider acceptance. Reserve provider setup
for an explicit manual smoke procedure.

### Acceptance checks for the first-run workflow

- An ungranted profile field is denied.
- A grant for one origin cannot be reused for another origin.
- An expired grant is denied.
- An ungranted email account is denied before the provider is read.
- A code older than `max_age_seconds` is rejected.
- Ambiguous or conflicting codes are rejected rather than guessed.
- A successful code request returns one recent, origin-bound candidate with
  account/message/timestamp provenance and is consumed once per
  agent/account/message; no confidence or expiry field is returned.
- OTPs, passwords, mailbox tokens, and full sensitive profile values never
  appear in audit rows, exceptions, debug output, screenshots, or embeddings.
- Successful email/OTP results identify their source account/message and
  timestamp where the operation provides that provenance, without disclosing
  connector secrets.
- Revoking an agent blocks its next request immediately.

## Agent-led Chrome intake

The repository-local `agent-autofill-chrome-intake` skill provides instructions
and tested local recipes for a host agent to curate structured Chrome names,
contact details, and addresses. It requires final approval for every saved
record, including clear matches, and holds unresolved items. It is separate
from the eager `setup`/`chrome import` paths; neither is called by the skill.

There is no embedded model, new broker API, or staging database. The host
needs local execution access. Candidate values enter its conversation context;
the broker stores only finally approved entries. Locked/unsupported schemas
stop safely. Local administrative writes commit individually, and the skill
does not claim cryptographic proof of approval or atomic batch rollback.

See [.agents/skills/agent-autofill-chrome-intake/SKILL.md](.agents/skills/agent-autofill-chrome-intake/SKILL.md)
and the current task's verification report for tested boundaries. Do not
interpret synthetic recipe/agent tests as live-provider acceptance.

## Deferred implementation areas

### 1. Packaging and installation

- Add CI for supported Python versions (currently 3.11+).
- Exercise wheel installation in a clean virtual environment.
- Document `pipx`/virtual-environment installation and macOS Keychain setup.
- Add a minimal `launchd` recipe for a loopback-only local process.
- Add `LICENSE`, `SECURITY.md`, and a changelog once the public repository is
  ready.

### 2. Raw Chrome importer (separate from agentic intake)

- The agentic skill supplies review and ambiguity resolution for structured
  identity/contact/address intake. It does not change the raw importer's
  history-only extraction or field-name collision behavior.
- Any future raw-importer changes need their own scope: consistent snapshots,
  structured-table support, and duplicate/update rules for autocomplete fields.
- Normalize origins before attaching grants.
- Keep password CSV import explicitly opt-in, encrypt immediately, and avoid
  printing or copying the CSV. The macOS Chrome Password Manager connector now
  supports explicitly approved v10/v11 `Login Data` imports through the Chrome
  Safe Storage Keychain item; unsupported encryption remains fail-closed.
- The trusted local fill helper is implemented in `fill_server.py` and
  `browser_extension/`. Keep it separate from field-selective reads: it may
  consume only an origin-bound, one-time capability and must never become an
  unscoped password export, unrelated agent context, or form submission path.

### 3. Email adapters

- Gmail OAuth setup and persisted refresh-state reconstruction are delivered;
  keep the read-only IMAP boundary and account-scoped grant checks intact.
- Add provider-specific OAuth setup for Outlook behind the existing adapter
  interface when separately approved.
- Keep IMAP app-password/password mode as an explicit fallback.
- Add connection health and clear failure reporting.
- Add MIME parsing and attachment metadata without automatic document
  ingestion.
- Preserve account-level search and OTP grant scopes. Email search currently
  has no time-window or origin filter; OTP freshness is request-scoped through
  `max_age_seconds` and its target-origin binding is a separate check.

### 4. iMessage bridge

- Finish the macOS permission/setup flow for `~/Library/Messages/chat.db`.
- Normalize participants, conversations, timestamps, and message IDs.
- Enforce conversation scopes in every search and retrieval path.
- Add a fixture database and tests for unavailable permissions, malformed rows,
  and out-of-scope conversations.
- Keep sending, deletion, and mutation out of v1.

### 5. Advanced Agent Inbox controls

- The loopback dashboard now shows connected sources, agent identities, grants,
  expiry/revocation metadata, profile groups, credential/financial metadata,
  and recent audit metadata. Remaining work is policy controls beyond review.
- Make sensitivity policy configurable for profile fields, OTPs, email, and
  messages.
- Auto-approve only low-risk reads covered by an existing policy.
- Keep approval focused on data disclosure; the broker does not submit forms,
  control a browser, send messages, purchase, or delete.

### 6. Security and release gate

- Threat-model the agent, local transport, connector, Keychain, Messages
  bridge, and browser fill helper as separate trust boundaries.
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
- Setup and Codex MCP configuration: `docs/agent-autofill.md`
- Market and architecture research: `RESEARCH.md`
- Policy and storage core: `agent_autofill/service.py`, `agent_autofill/storage.py`
- MCP transport and tool schemas: `agent_autofill/server.py`
- CLI and setup wizard: `agent_autofill/cli.py`
- Chrome importer: `agent_autofill/chrome.py`
- Provider contracts: `agent_autofill/adapters.py`, `agent_autofill/imap_adapter.py`,
  `agent_autofill/messages_adapter.py`
- Existing acceptance coverage: `tests/agent_autofill/`

The next agent should begin by reading this file and `AGENTS.md`, then inspect
the existing service and transport tests before changing implementation.
