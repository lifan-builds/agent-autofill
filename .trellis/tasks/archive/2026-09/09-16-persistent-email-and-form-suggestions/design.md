# Integration Design

## Architecture and Boundaries

The feature stays inside the existing local information-broker shape:

```text
user / consuming agent
        |
        | authenticated MCP calls with scoped grants
        v
server.py -> Broker authorization and validation -> encrypted Store
        |                                           |
        | OTP only                                  | connector secrets
        v                                           v
read-only IMAP adapter                         operating-system Keychain
```

Agent Autofill returns authorized information. It does not inspect a browser,
type values, submit forms, send messages, or decide whether the consuming
agent needs approval for those external actions.

The parent coordinates three deliverables:

1. Generic IMAP connection verification, saved reconnection, and one-time OTP
   retrieval.
2. Encrypted profile/credential/financial record contracts, an approved-batch
   write path, and direct scoped reads.
3. A repository-local skill that semantically maps form context to those
   record contracts and proposes reviewable changes.

The sensitive-record contract must land before the skill calls its new tools.
The IMAP work is independent except for the shared authentication, grant,
redaction, and policy language.

## Shared Authorization Contract

Every data-returning or mutating MCP tool authenticates the agent token at the
transport boundary. `Broker` then enforces operation, resource id, target
origin, purpose, expiry, and revocation.

| Capability | Grant operation | Resource |
| --- | --- | --- |
| OTP retrieval | `email.otp` | account id |
| Profile read | `profile.read` | field id |
| Credential read | `credential.read` | credential id |
| Financial read | `financial.read` | financial-record id |
| Approved record write | `<type>.write` | record id or reviewed wildcard |

Successful reads do not require a second approval token, confirmation flag,
or per-use prompt. Writes require `confirmed: true` as an accidental-mutation
guard, but the skill remains responsible for obtaining explicit chat approval
of the exact displayed diff. The broker cannot inspect the chat transcript.

## Shared Data Flows

### OTP

`email-add` tests an in-memory read-only IMAP connection, then stores the
secret in Keychain and non-secret connection metadata in encrypted SQLite. A
fresh server reconstructs an adapter for each configured source. An authorized
`request_auth_code` call fetches recent messages, requires one target-bound
candidate, records a non-secret consumption marker, audits only provenance,
and returns the code once.

### Reusable records

The consuming agent supplies current form context to the skill. The skill
lists metadata and, when already authorized, retrieves only the saved fields
needed for comparison. It produces add/update/no-op proposals. After approval,
one `write_records` call validates and atomically stores only the approved
entries. Later field-selective reads return values directly under active
scoped grants.

## Compatibility

- Existing profile, credential, connection, source, agent, grant, and audit
  records remain readable.
- The current short-lived credential-handle API remains available for existing
  callers; the new skill uses direct `credential.read` instead.
- Existing `email-add`, `source-remove`, and `request_auth_code` entry points
  are extended rather than renamed.
- Generic caller-supplied bearer-token support does not itself imply a refresh
  lifecycle. Gmail is the explicit provider-specific exception: its user-owned
  Desktop OAuth flow stores refresh/client material in Keychain and keeps
  refreshed access tokens in process memory only.

## Security and Redaction

- Mailbox connector passwords and bearer tokens never leave Keychain-backed
  connector setup.
- OTPs are returned only by the successful OTP response and are not stored in
  keys, values, audit metadata, errors, diagnostics, docs, or screenshots.
- Saved website credentials and financial values live only in encrypted record
  values and successful scoped read responses.
- Metadata listings and audits contain identifiers, safe matching hints,
  origin/purpose, action, and provenance only.
- Test data is synthetic.

## Rollout and Rollback

Each child is independently testable and revertible. Storage uses new logical
namespaces and backward-compatible fields rather than destructive schema
migrations. Removing the new MCP tools and skill leaves prior records intact;
local record/source deletion remains available for user-controlled cleanup.
