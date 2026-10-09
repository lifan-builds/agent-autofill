# Quality Guidelines

> Current implementation and review standards for Agent Autofill.

## Core Contract

Agent Autofill is local-first and information-only. Changes must preserve:

- explicit authentication and short-lived, scoped grants for data access;
- origin and purpose binding where the use case requires it;
- metadata-only listings and provenance on returned user data;
- encrypted local values and operating-system credential storage for keys and
  connector credentials;
- loopback-only local review surfaces;
- read-only email, Messages, and browser-source integrations;
- no unscoped password-returning MCP tool, outbound messaging, browser control,
  or form submission without a new explicit product decision. The scoped
  `request_auth_code` operation is the explicit exception for one recent OTP;
  `request_credential_fields` and `request_financial_fields` may return only
  caller-selected values after authentication, grant, record-scope, origin,
  purpose, expiry, and revocation checks succeed.

## Required Patterns

- Put security decisions in `Broker`, not only in a transport wrapper.
- Authenticate non-public MCP tools in `server._call`, then enforce the
  operation/resource/origin/purpose grant inside the broker use case.
- Bound caller-controlled collection/search sizes before querying an adapter.
- Return only the minimum data required. Credential listing omits passwords;
  credential use returns a short-lived, origin-bound handle rather than the
  password; profile and financial listings omit values. The direct credential
  and financial field-read tools return only the requested values.
- Record provenance for returned profile fields, email/messages, OTPs, and
  credential-use handles.
- Keep secrets out of audit metadata and error text. Hash search terms rather
  than recording them verbatim.
- Type public and boundary-facing functions where the surrounding module does;
  use standard-library types and frozen dataclasses for simple records.
- Use `Path` for filesystem paths and timezone-aware UTC ISO strings for time.
- Use `apply_patch` for source edits, preserve unrelated edits, and keep new
  dependencies out unless a reachable requirement needs them.

## Forbidden Patterns

- MCP tools named or behaving like an unscoped `get_password`, `send_message`,
  or `submit_form` under the current product scope. Website credential and
  financial reads are permitted only through the field-selective, authenticated
  and grant-scoped operations described above.
- Logging, fixture-writing, or documenting real passwords, mailbox tokens,
  agent tokens, OTPs, Messages database copies, or credential handles. The
  explicit credential-use response may return its short-lived handle to the
  authenticated requester, but must not duplicate it elsewhere. Successful
  field-selective credential/financial responses may contain the requested raw
  values, but those values must not appear in listings, audits, errors,
  fixtures, screenshots, or documentation. Synthetic OTP fixtures are
  acceptable when they never leave the successful response assertion.
- Binding the Inbox review server to a non-loopback host.
- Writing to provider-owned mailbox, Messages, or Chrome databases.
- Storing connector credentials in the encrypted SQLite database instead of
  the operating-system credential store.
- Skipping `Broker._authorized` because a caller or UI is assumed trusted.
- Broad exception handling that converts security or persistence failures into
  successful empty results.

## Scoped OTP Contract

`request_auth_code` may return one recent target-bound OTP only after transport
authentication and an active `email.otp` grant for the account, origin, and
purpose succeed. The value remains transient and must not be persisted,
audited, or copied to diagnostics. This OTP exception does not expand the
information-only surface into browser control or form submission. Website
passwords and financial values have a separate field-selective response path
that requires their own authenticated read grant and record scope.

## Scenario: Generic IMAP Connection and Scoped OTP Retrieval

### 1. Scope / Trigger

Apply this contract when changing generic IMAP setup, saved-connection
reconstruction, email-source availability, or `request_auth_code`. The flow
crosses CLI, Keychain, encrypted SQLite, adapter, broker, and MCP boundaries;
a local-only check is not sufficient.

### 2. Signatures

```text
agent-autofill --db DB email-add ACCOUNT_ID HOST USERNAME
  [--label LABEL] [--port PORT] [--mailbox MAILBOX] [--oauth2]
agent-autofill --db DB email-add-gmail ACCOUNT_ID USERNAME
  --client-secrets DESKTOP_CLIENT_JSON [--label LABEL] [--mailbox MAILBOX]
agent-autofill --db DB email-test ACCOUNT_ID

request_auth_code(
  account_id: str,
  target_origin: str,
  purpose: str,
  max_age_seconds: int = 300,
) -> {code, provenance: {source, account_id, message_id, received_at}}
```

SQLite uses encrypted `connection` and `source` values plus an
`otp-consumed` marker keyed by a SHA-256 digest of agent/account/message
identifiers. The password or caller-supplied bearer token is stored only under
the connection's opaque, versioned Keychain lookup name.

### 3. Contracts

- `email-add` must authenticate, select the mailbox with `readonly=True`, and
  log out before saving. Replacement writes a fresh Keychain secret, commits
  connection/source metadata together, and only then removes the prior secret.
- IMAP `login` and `authenticate` must return `OK`; a non-exception `NO` or
  `BAD` status is still an authentication failure and must not proceed to
  mailbox selection.
- `email-test` must reuse saved state without prompting and return only
  non-secret connection metadata.
- `email-add-gmail` must use a user-supplied Google Desktop client JSON,
  request the full `https://mail.google.com/` scope with offline access, bind
  a temporary callback to `127.0.0.1` on an OS-selected port, validate state
  and PKCE, and verify Gmail XOAUTH2 read-only access before saving anything.
- Gmail Keychain state contains only client/refresh material; access tokens
  remain memory-only and are refreshed before a new IMAP connection as needed.
- Parse and canonicalize any OAuth payload before writing it to Keychain. Its
  keys must be exactly `version`, `provider`, `client_id`, `refresh_token`, and
  optional `client_secret`; reject `access_token` and every unknown field.
- A fresh server process reconstructs the adapter from encrypted metadata plus
  the Keychain secret. Missing or invalid saved state keeps the source visible
  but marks it unavailable for reads.
- `request_auth_code` requires transport authentication and an active
  account/origin/purpose-scoped `email.otp` grant. It returns one target-bound
  code without a second approval parameter.
- OTP target binding matches the normalized hostname as a complete token in
  sender/subject/body text. URL and email delimiters are valid boundaries;
  prefixes and suffixes such as `notexample.com` and `example.com.evil` are
  not matches for `example.com`.
- Removing a source always records an in-process email tombstone before
  deleting persisted metadata, even if startup could not reconstruct its
  adapter. This prevents stale memory fixtures from becoming reachable through
  a later wildcard grant.
- The successful response may contain the code. SQLite values/keys, audits,
  errors, listings, documentation, and diagnostics must not.

### 4. Validation & Error Matrix

| Condition | Required result |
| --- | --- |
| Connection, authentication, or mailbox selection fails | Safe operator error; no new Keychain or SQLite state |
| IMAP login/XOAUTH2 returns `NO` or `BAD` without raising | Safe authentication failure and logout |
| OAuth provider raises an exception containing response data | Fixed boundary message; provider exception retained only as a cause |
| Keychain OAuth payload contains an access token or unknown key | Reject as unsupported saved state |
| New secret saves but metadata transaction fails | Delete the new secret; preserve prior configuration |
| Saved secret/configuration is missing or invalid | `email-test` fails safely; server marks source unavailable |
| Agent authentication or scoped grant fails | Return no OTP |
| `max_age_seconds` is outside 1 through 3600 | Bounded-input error |
| Candidate is absent, stale, ambiguous, or not target-bound | Return no OTP |
| Candidate mentions a prefixed or suffixed lookalike hostname | Binding refusal |
| Consumption marker already exists | `authentication code already consumed` |
| Concurrent brokers claim the same marker | Exactly one `put_if_absent` succeeds |

### 5. Good / Base / Bad Cases

- Good: add a mailbox once, restart the server, retrieve one recent scoped OTP,
  and observe only message provenance in the audit.
- Base: a configured source whose Keychain entry is unavailable remains
  listable and directs the operator to `email-test`; it never falls back to an
  unrelated in-memory mailbox.
- Bad: storing the code in an `otp-used` key, serializing a live socket,
  logging provider exception text, or writing connection metadata before the
  read-only login check succeeds.

### 6. Tests Required

- Fake IMAP tests assert password and XOAUTH2 authentication, readonly select,
  explicit `OK` statuses, rejection of `NO`/`BAD`, logout, safe exceptions,
  and secret-free adapter representations.
- CLI tests assert test-before-save, versioned Keychain names, replacement
  ordering, rollback cleanup, strict OAuth payload canonicalization, and
  saved-secret reconnect.
- Service/storage tests assert scope failures, stale/ambiguous/unbound cases,
  received-at provenance, cross-process atomic consumption, legacy
  `otp-used` cleanup, exact hostname boundaries, removed-source tombstones,
  and absence of the synthetic code from raw SQLite and audits.
- Full pytest, Ruff, and compile checks must pass. A live provider smoke is
  reported separately and is never implied by fake-provider tests.

### 7. Wrong vs Correct

Wrong:

```python
store.put("otp-used", f"{agent_id}:{account_id}:{message_id}:{code}", marker)
```

Correct:

```python
marker_id = sha256(f"{agent_id}\0{account_id}\0{message_id}".encode()).hexdigest()
if not store.put_if_absent("otp-consumed", marker_id, marker):
    raise BrokerError("authentication code already consumed")
```

## Scenario: Approved Sensitive Records and Scoped Field Reads

### 1. Scope / Trigger

Apply this contract when changing reusable profile writes, website credentials,
payment/financial records, metadata listings, record deletion, or their MCP
tools. The flow crosses transport authentication, grants, record scopes,
encrypted persistence, audit redaction, CLI administration, and the Inbox.

### 2. Signatures

```text
write_records(agent_id, target_origin, purpose, confirmed=true, entries)
request_credential_fields(agent_id, credential_id, field_ids, target_origin, purpose)
request_financial_fields(agent_id, record_id, field_ids, target_origin, purpose)
agent-autofill --db DB record-delete profile|credential|financial RECORD_ID
```

Record namespaces are `profile`, `credential`, and `financial`; no plaintext
table or schema migration is introduced. Financial kinds are `payment_card`,
`bank_account`, `financial_account`, and `custom_financial`.

### 3. Contracts

- `write_records` requires transport authentication, literal `confirmed: true`,
  1 through 100 entries, and the matching `<type>.write` grant for every exact
  record id. Validate the whole batch before one atomic encrypted transaction.
- An update is a complete record replacement, not a field patch. Omitted
  optional credential fields take their defaults and omitted financial values
  are removed. A consuming agent must submit a complete user-approved entry;
  it must not copy scoped-read secrets back into a write merely to preserve
  fields the user did not provide or verify.
- Profile records cannot carry credential, payment, token, cookie, OTP,
  session, or handle categories. Passwords stay in `credential`; financial
  value maps stay in `financial`.
- Credential and financial listings expose ids, labels, field ids, scopes,
  timestamps, sensitivity, provenance, and safe matching hints only. They never
  expose `password` or the encrypted `values` map.
- Field reads require transport authentication plus an active exact/wildcard
  read grant and matching record origin/purpose scope. Return only requested
  fields plus safe provenance; do not add a second approval parameter.
- Normalize both URL and scheme-less origins before extracting hostnames. The
  compatibility credential-handle path must treat `example.com` and
  `https://example.com` as the same host while still rejecting a different
  hostname.
- Deletion removes the exact encrypted record and credential handles and
  revokes exact-id read/write/use grants in the same transaction. Wildcard
  grants remain capabilities but cannot read an absent record.
- Audits contain ids, selected field ids, origin, purpose, and status only.
  Recursive redaction is a defensive second boundary, not permission to send
  raw entries to the audit layer.

### 4. Validation & Error Matrix

| Condition | Required result |
| --- | --- |
| Authentication missing/invalid | Transport refusal before broker access |
| `confirmed` missing or not literal true | Safe error; no record is written |
| Duplicate ids, unsupported type/kind, batch over 100 | Safe error; entire batch unchanged |
| OTP/token/cookie/session/handle field or category | Reject as transient material |
| Missing, wrong-resource, wrong-origin, wrong-purpose, expired, or revoked grant | Return no raw value and write nothing |
| Record origin/purpose scope mismatch | Authorization refusal after grant check |
| Requested field missing | Safe error naming only the field, never a submitted value |
| Proposed update omits an existing credential/financial field | Consumer holds it for review; do not write a partial replacement |
| Storage or audit insert fails | Roll back puts, deletes, grant changes, and audit together |
| Exact record deleted | Record and credential handles disappear; exact grants are revoked |

### 5. Good / Base / Bad Cases

- Good: an authenticated agent writes an exactly approved card record, lists
  only `last4` metadata, and later reads only `card_number` under a matching
  short-lived grant.
- Base: an older credential without scope/timestamp fields receives safe
  read-time defaults and continues to work through both field reads and the
  one-time handle compatibility path.
- Bad: placing a password in a profile field, accepting `confirmed: false`,
  returning the whole financial map, logging the request entry, or committing
  the first item before a later entry fails validation.

### 6. Tests Required

- Storage tests prove encrypted cross-process persistence, nested audit
  redaction, and rollback after an injected mid-transaction failure.
- Service tests cover create/update/no-op, duplicate/size/type/transient
  validation, full-batch refusal, legacy credentials, metadata redaction,
  selective reads, missing fields, every grant failure mode, record scopes,
  deletion, handle invalidation, and exact-grant revocation.
- Skill/consumer forward tests prove incomplete replacements remain held for
  review and partial approval writes only complete selected proposal rows.
- Transport tests assert authentication for every new sensitive tool and the
  absence of a read-confirmation parameter. CLI and Inbox tests exercise exact
  deletion and ensure full values never appear in review HTML.
- Full pytest, Ruff, mypy, compileall, diff checks, and Trellis task validation
  must pass before commit.

### 7. Wrong vs Correct

Wrong:

```python
for entry in entries:
    store.put(entry["record_type"], entry["record_id"], entry)
```

Correct:

```python
prepared = validate_every_entry_and_grant(entries, target_origin, purpose)
store.apply_changes(encrypted_records(prepared), [], audits=[metadata_only_audit])
```

## Scenario: Explicit First-Run Setup Grants

### 1. Scope / Trigger

Applies to the optional authorization step in setup and its fixture-backed
CLI-to-server acceptance path.

### 2. Signatures

`agent-autofill --db DB setup [--profile-dir CHROME_PROFILE]` reuses
`Broker.grant(agent_id, operation, resource_scope, target_origin, purpose,
expires_in=600)`. It does not change any MCP tool schema.

### 3. Contracts

- Select exact saved profile field IDs and at most one configured email
  account. Select search and OTP independently; never infer a wildcard grant.
- Profile/OTP grants require origin and purpose. Search is account-scoped
  with null origin/purpose; explain this distinction before confirmation.
- Validate the entire proposal, display operations/resources/scopes and the
  600-second lifetime without stored values, then ask for explicit confirmation.
- Skipping creates no grants and does not roll back earlier setup effects.
  Existing identities retain their tokens; revoked agents are not reactivated.

### 4. Validation & Error Matrix

| Condition | Required result |
| --- | --- |
| Blank agent, skipped step, empty selection, declined confirmation | No new grants |
| Unknown/wildcard resource or missing profile/OTP scope | Safe refusal before any proposed grant is written |
| Revoked identity | Explain refusal; no new grants or token rotation |
| Confirmed valid selection | Only selected read grants, each lasting 600 seconds |

### 5. Good / Base / Bad Cases

Good: a new identity imports fixture data, confirms exact grants, and reads via
a freshly constructed authenticated server. Base: skip authorization and use
the existing grant command later. Bad: silently grant all imported resources
or describe account-wide search as restricted to one website.

### 6. Tests Required

`test_cli.py` covers selection, cancellation, preservation, and preview
redaction. `test_first_run.py` drives CLI setup and the server JSON-lines loop
using a temporary encrypted database, fake Keychain, and fake IMAP provider;
it must retain real persistence, connector reconstruction, and authorization.
Check persisted OTP replay denial, scope/authentication refusals, expiry,
revocation, and secret-free audits/errors. This is not live-provider or
installation acceptance.

### 7. Wrong vs Correct

Wrong: grant `email.search` with an origin and imply searches are origin-bound.
Correct: grant the exact account with null origin/purpose after clearly
previewing account-wide access; bind the separate `email.otp` grant to the
explicit origin/purpose.

## Chrome Intake Skill Boundary

`agent-autofill-chrome-intake` is a host-agent instruction workflow, not a new
broker API. Its documented local recipes read only supported structured
Chrome address fields in a read-only SQLite transaction. Locked/unsupported
sources stop; immutable reads and uncoordinated raw database copies are not
supported intake fallbacks. The eager `cold_start`/setup/`chrome import` paths
are separate.

The agent resolves ambiguities and presents all proposed records, including
clear matches, before final approval. Only approved add/update entries use
the existing local administrative `Broker.put_profile` API with explicit
scopes and safe provenance. Normal MCP grants remain unchanged; never invent
a target website or grant privileges to make onboarding fit a form API.
Per-record persistence can partially succeed; report saved/pending IDs and
do not claim atomic rollback. Approval is an agent instruction, not a new
runtime authentication guarantee.

Verify exact executable recipes with synthetic databases, source locks,
unsupported schemas, grouping, provenance, and failure cases. Independently
forward-test the skill on realistic synthetic conversations for no writes
before approval, ambiguous ownership, changed/partial approval, cancellation,
repeat intake, and source-embedded instructions. Static phrase/heading checks
and YAML validation alone do not establish those behaviors. Keep all real
Chrome values out of fixtures and task artifacts.

## Testing Requirements

- Add or update focused pytest coverage for changed behavior under
  `tests/agent_autofill/`. Tests use temporary paths, in-memory SQLite, explicit
  deterministic 32-byte keys, and small local fakes rather than real accounts.
- Cover both the allowed path and the relevant refusal path for authorization,
  scope, one-time use, redaction, and loopback restrictions.
- Assert absence of secrets as well as presence of expected metadata. Existing
  examples check that plaintext is absent from raw SQLite, OTPs/search terms
  are absent from audits, and passwords are absent from listings and MCP
  output.
- When the public MCP surface changes, update the catalog test in
  `tests/agent_autofill/test_transport.py` and verify the information-only
  negative set remains absent.
- Run the focused tests for the edited behavior, then the project checks before
  handoff:

  ```bash
  PYTHONPATH=. pytest -q
  ruff check agent_autofill
  ```

## Code Review Checklist

1. Does the change stay within the authorized information-only scope?
2. Can any secret or private source content reach a response, audit, exception,
   test artifact, screenshot, or documentation?
3. Are authentication, grant, resource, origin, purpose, expiry, and revocation
   checks still enforced at the service boundary?
4. Are provider databases and mailboxes still read-only, and are local review
   endpoints loopback-only?
5. Are limits bounded and one-time resources consumed atomically enough for
   the current single-process design?
6. Do tests prove the changed positive path and meaningful refusal/redaction
   paths without relying on live credentials or user data?
7. Do the focused tests and Ruff pass, with any unverified end-to-end behavior
   reported plainly?

## Representative Examples

- `tests/agent_autofill/test_transport.py` protects the information-only MCP
  contract and agent-token authentication.
- `tests/agent_autofill/test_service.py` exercises grants, origin binding,
  revocation, audit redaction, and one-time credentials/OTPs.
- `tests/agent_autofill/test_storage.py` verifies encrypted persistence without
  exposing plaintext in SQLite.
