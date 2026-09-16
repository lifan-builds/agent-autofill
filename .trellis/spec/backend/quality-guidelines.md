# Quality Guidelines

> Current implementation and review standards for StopAskingMe.

## Core Contract

StopAskingMe is local-first and information-only. Changes must preserve:

- explicit authentication and short-lived, scoped grants for data access;
- origin and purpose binding where the use case requires it;
- metadata-only listings and provenance on returned user data;
- encrypted local values and operating-system credential storage for keys and
  connector credentials;
- loopback-only local review surfaces;
- read-only email, Messages, and browser-source integrations;
- no password-returning MCP tool, outbound messaging, browser control, or form
  submission without a new explicit product decision.

## Required Patterns

- Put security decisions in `Broker`, not only in a transport wrapper.
- Authenticate non-public MCP tools in `server._call`, then enforce the
  operation/resource/origin/purpose grant inside the broker use case.
- Bound caller-controlled collection/search sizes before querying an adapter.
- Return only the minimum data required. Credential listing omits passwords;
  credential use returns a short-lived, origin-bound handle rather than the
  password; profile listing omits values.
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

- MCP tools named or behaving like `get_password`, `send_message`, or
  `submit_form` under the current product scope.
- Logging, fixture-writing, or documenting real passwords, mailbox tokens,
  agent tokens, OTPs, Messages database copies, or credential handles. The
  explicit credential-use response may return its short-lived handle to the
  authenticated requester, but must not duplicate it elsewhere.
- Binding the Inbox review server to a non-loopback host.
- Writing to provider-owned mailbox, Messages, or Chrome databases.
- Storing connector credentials in the encrypted SQLite database instead of
  the operating-system credential store.
- Skipping `Broker._authorized` because a caller or UI is assumed trusted.
- Broad exception handling that converts security or persistence failures into
  successful empty results.

## Known Contract Mismatch

Project guidance says OTPs must not appear in MCP responses. The current
`request_auth_code` MCP tool returns the code, and its unit tests use synthetic
OTP values. Treat that as an existing mismatch requiring a separate explicit
product decision; do not use it as precedent for new secret-returning tools or
silently change the established behavior during unrelated work.

## Testing Requirements

- Add or update focused pytest coverage for changed behavior under
  `tests/stopaskingme/`. Tests use temporary paths, in-memory SQLite, explicit
  deterministic 32-byte keys, and small local fakes rather than real accounts.
- Cover both the allowed path and the relevant refusal path for authorization,
  scope, one-time use, redaction, and loopback restrictions.
- Assert absence of secrets as well as presence of expected metadata. Existing
  examples check that plaintext is absent from raw SQLite, OTPs/search terms
  are absent from audits, and passwords are absent from listings and MCP
  output.
- When the public MCP surface changes, update the catalog test in
  `tests/stopaskingme/test_transport.py` and verify the information-only
  negative set remains absent.
- Run the focused tests for the edited behavior, then the project checks before
  handoff:

  ```bash
  PYTHONPATH=. pytest -q
  ruff check stopaskingme
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

- `tests/stopaskingme/test_transport.py` protects the information-only MCP
  contract and agent-token authentication.
- `tests/stopaskingme/test_service.py` exercises grants, origin binding,
  revocation, audit redaction, and one-time credentials/OTPs.
- `tests/stopaskingme/test_storage.py` verifies encrypted persistence without
  exposing plaintext in SQLite.
