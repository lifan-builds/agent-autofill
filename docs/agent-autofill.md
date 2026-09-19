# Agent Autofill

This package is a local MCP information service. It keeps profile values,
connector configuration, credentials imported during cold start, grants, and
audit metadata in an encrypted application store. Connector secrets are held
separately in macOS Keychain through Python `keyring`.

## Setup

Install the two small runtime dependencies first:

```bash
python3 -m pip install -r /Users/lfan/Project/agent-autofill/requirements.txt
```

```bash
PYTHONPATH=/Users/lfan/Project/agent-autofill python -m agent_autofill.cli \
  --db /Users/lfan/.agent-autofill.sqlite3 init
PYTHONPATH=/Users/lfan/Project/agent-autofill python -m agent_autofill.cli \
  --db /Users/lfan/.agent-autofill.sqlite3 setup
PYTHONPATH=/Users/lfan/Project/agent-autofill python -m agent_autofill.cli \
  --db /Users/lfan/.agent-autofill.sqlite3 agent-create codex
PYTHONPATH=/Users/lfan/Project/agent-autofill python -m agent_autofill.cli \
  --db /Users/lfan/.agent-autofill.sqlite3 grant codex profile.read name \
  --origin example.com --purpose form
PYTHONPATH=/Users/lfan/Project/agent-autofill python -m agent_autofill.cli \
  --db /Users/lfan/.agent-autofill.sqlite3 grant codex credential.use <credential-id> \
  --origin https://example.com --purpose form
PYTHONPATH=/Users/lfan/Project/agent-autofill python -m agent_autofill.cli \
  --db /Users/lfan/.agent-autofill.sqlite3 grant codex credential.read <credential-id> \
  --origin https://example.com --purpose form
PYTHONPATH=/Users/lfan/Project/agent-autofill python -m agent_autofill.cli \
  --db /Users/lfan/.agent-autofill.sqlite3 grant codex financial.read <financial-record-id> \
  --origin https://example.com --purpose form
PYTHONPATH=/Users/lfan/Project/agent-autofill python -m agent_autofill.cli \
  --db /Users/lfan/.agent-autofill.sqlite3 grant codex email.otp personal-mail \
  --origin https://example.test --purpose login
```

The wizard can import Chrome autofill data and login metadata from a selected
profile, optionally import a user-exported Chrome password CSV after typing
`IMPORT`, and configure read-only IMAP and local Messages sources. The broker
never attempts to decrypt Chrome's `Login Data` database and never prints an
imported password.

For a generic Thunderbird-style IMAP source, use `email-add`. The command
checks the login and read-only mailbox selection before saving the password or
OAuth2 bearer token in Keychain. Encrypted SQLite stores only connection
metadata and an opaque Keychain lookup name:

```bash
PYTHONPATH=/Users/lfan/Project/agent-autofill python -m agent_autofill.cli \
  --db /Users/lfan/.agent-autofill.sqlite3 email-add personal-mail \
  imap.example.test user@example.test --mailbox INBOX
PYTHONPATH=/Users/lfan/Project/agent-autofill python -m agent_autofill.cli \
  --db /Users/lfan/.agent-autofill.sqlite3 email-test personal-mail
```

`email-test` reconnects with the saved Keychain secret and prints only source
metadata. Each broker/server process opens a fresh TLS connection, selects the
mailbox read-only, performs its operation, and logs out; a live IMAP socket is
never serialized. Replace a source by running `email-add` again. Disconnect it
with `source-remove`, which deletes the current Keychain secret before
removing local metadata.

For Gmail, use a Google Cloud Desktop OAuth client that you own. In Google
Cloud, enable the Gmail API, configure the OAuth consent screen (choose
External for a personal Gmail account and add that account as a test user),
add `https://mail.google.com/` under Data Access, then create an OAuth client
with application type Desktop app and download its JSON. Agent Autofill does not
bundle or publish a shared client:

```bash
PYTHONPATH=/Users/lfan/Project/agent-autofill python -m agent_autofill.cli \
  --db /Users/lfan/.agent-autofill.sqlite3 email-add-gmail personal-gmail \
  user@gmail.com --client-secrets /private/path/client-secret.json
PYTHONPATH=/Users/lfan/Project/agent-autofill python -m agent_autofill.cli \
  --db /Users/lfan/.agent-autofill.sqlite3 email-test personal-gmail
```

`email-add-gmail` opens Google Sign-In in the system browser, receives the
authorization code only on a temporary `127.0.0.1` listener, validates state
and PKCE, and checks Gmail XOAUTH2 access by selecting the requested mailbox
read-only before saving. The Keychain stores only the client id, optional
client secret, and refresh token; encrypted SQLite stores non-secret source
metadata and the opaque Keychain name. Access tokens remain in memory and are
refreshed when a later process needs a new IMAP connection. Gmail exposes the
full `https://mail.google.com/` scope for IMAP even though this adapter never
writes mail or sends messages.

Google marks External consent clients in Testing as having refresh tokens that
expire after seven days for this Gmail scope. If that happens, `email-test`
fails safely and the operator must run `email-add-gmail` again. For an opt-in
live smoke test, use only a mailbox and Desktop client you explicitly own,
run `email-add-gmail` followed by `email-test`, and remove the source with
`source-remove`; never put live addresses, messages, OAuth tokens, or client
JSON in repository files or diagnostics.

Automated coverage uses a deterministic fake IMAP provider. A live smoke test
is opt-in: use only a mailbox you explicitly provide, run `email-add` followed
by `email-test`, and remove it afterward. Do not copy live messages, addresses,
passwords, bearer tokens, or OTPs into repository files or diagnostics.

macOS may require granting the terminal or Python process Full Disk Access to
read `~/Library/Messages/chat.db`; the adapter reports a clear unavailable
error when that permission is absent.

To run the local MCP process for a client:

```bash
PYTHONPATH=/Users/lfan/Project/agent-autofill python -m agent_autofill.cli \
  --db /Users/lfan/.agent-autofill.sqlite3 serve
```

For a human review page, run `inbox-web` and open the printed loopback URL:

```bash
PYTHONPATH=/Users/lfan/Project/agent-autofill python -m agent_autofill.cli \
  --db /Users/lfan/.agent-autofill.sqlite3 inbox-web
```

The process uses MCP `initialize`, `tools/list`, and `tools/call` JSON-RPC
messages over stdin/stdout. A client should launch it as a local stdio server
and set the one-time token from `agent-create` as `AGENT_AUTOFILL_AGENT_TOKEN` in the
MCP process environment. The stable identity is passed as `agent_id` in tool
arguments. Create grants with the CLI; grants are short-lived and default-deny.

A Codex MCP entry follows this shape (keep the token in the process environment
or a secret manager, not in source control):

```toml
[mcp_servers.agent-autofill]
command = "/Users/lfan/Project/agent-autofill/scripts/agent-autofill"
args = ["--db", "/Users/lfan/.agent-autofill.sqlite3", "serve"]
```

The information surface is intentionally local and information-only. It does
not expose unscoped password retrieval, message send/delete operations,
browser control, or form submit. For imported credentials, the compatibility
`request_credential_use` operation returns an origin-bound, short-lived handle
plus the username. New callers can request only selected website-credential or
financial fields through `request_credential_fields` or
`request_financial_fields`; these tools require an authenticated agent,
matching short-lived grant, record scope, target origin, and purpose. A valid
read grant is sufficient and does not create a second per-use approval prompt.
Raw values appear only in that successful scoped response and are never put in
listings, audits, errors, or documentation. The consuming agent remains
responsible for any separate approval before browser typing or submission.

After the consuming skill has obtained exact user approval for a proposal, it
may submit the approved entries in one authenticated `write_records` call with
`confirmed: true`. The broker validates every entry and returns metadata-only
created/updated/unchanged statuses. Passwords and payment/financial maps must
use their dedicated `credential` and `financial` record types rather than
profile fields; transient OTPs, tokens, cookies, sessions, and handles are
rejected.

For local administration, list safe financial metadata or delete one record:

```bash
PYTHONPATH=/Users/lfan/Project/agent-autofill python -m agent_autofill.cli \
  --db /Users/lfan/.agent-autofill.sqlite3 financial-records list
PYTHONPATH=/Users/lfan/Project/agent-autofill python -m agent_autofill.cli \
  --db /Users/lfan/.agent-autofill.sqlite3 record-delete financial <financial-record-id>
```

Deletion removes the encrypted record, invalidates any compatibility handle
for that credential, and revokes grants that name the exact record. Wildcard
category grants remain capabilities but cannot read a deleted record.

To request an email OTP, the authenticated agent needs an active
`email.otp` grant scoped to the account, target origin, and purpose. A
successful request returns the one-time code plus email/message/received-time
provenance; the code is never stored, audited, or included in an error. For
example, a client calls `request_auth_code` with an argument shape like:

```json
{
  "account_id": "personal-mail",
  "target_origin": "https://example.test",
  "purpose": "login",
  "max_age_seconds": 300
}
```

The broker refuses missing or expired grants, wrong account/origin/purpose,
stale or ambiguous candidates, and reuse of a returned message. It only reads
mail; browser typing and form submission remain outside this project.

Disconnect a mailbox or Messages source with `source-remove`; this removes its
local connection record, deletes its Keychain secret when present, and revokes
grants scoped to that source:

```bash
PYTHONPATH=/Users/lfan/Project/agent-autofill python -m agent_autofill.cli \
  --db /Users/lfan/.agent-autofill.sqlite3 source-remove personal-mail
```
