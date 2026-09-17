# Logging Guidelines

> Audit and output conventions in the current implementation.

## Overview

The project does not use Python's `logging` module and has no log-level or
structured application-log framework. It has three distinct output channels:

1. encrypted, queryable security audit records in `Store.audit`;
2. JSON-lines responses/errors on MCP server stdout;
3. human-readable CLI output for the local operator.

Do not introduce ambient debug logging as a substitute for one of these
channels, especially around private data.

## Security Audit Records

Audit authorized information access at the service layer after the operation
has succeeded. Current records contain:

- `agent_id`, operation, resource, and UTC ISO timestamp;
- minimal encrypted metadata needed for review, such as result count,
  `message_id`, `target_origin`, or purpose;
- a SHA-256 `query_hash` instead of raw email/message search text.

Approved record writes and sensitive field reads audit only record type/id,
selected field ids, target origin/purpose, status, and provenance metadata.
The raw credential/password or financial `values` map is never sent to
`Store.audit`.

Examples are in `Broker.request_profile_fields`, `search_email`,
`request_auth_code`, `search_messages`, `request_credential_use`,
`write_records`, `request_credential_fields`, and
`request_financial_fields`.
`Store.audit` defensively drops metadata keys named `body`, `code`, `password`,
or `token` before encrypting the remaining dictionary, including nested
occurrences.

## MCP Transport Output

- Stdout is the JSON-lines protocol stream. Emit one JSON object per response
  and use `flush=True`; do not write commentary or debug prints there.
- Error responses contain the request id, code `-32000`, exception type, and a
  safe message. They must not include request parameters, tracebacks, or
  decrypted source content.
- `notifications/initialized` produces no response.

## CLI and Local Review Output

- CLI output is either a short operator-facing sentence or indented JSON for
  metadata collections.
- A newly generated agent token is printed once because the explicit
  `agent-create`/setup command exists to hand it to the local operator. Do not
  repeat it in later listings, audits, tests, screenshots, or documentation.
- `inbox.build_html` HTML-escapes an indented JSON summary and only uses broker
  listing methods that omit stored secrets.

## What Must Never Be Recorded

- passwords, credential database copies, connector or mailbox tokens, agent
  tokens, Fernet keys, or one-time credential handles;
- full email/message bodies outside the narrowly authorized response that
  requested the content;
- Real OTP values in audit, diagnostics, errors, screenshots, or
  documentation. Deterministic synthetic OTPs may appear only in focused
  response/redaction tests. The one response exception is an authenticated,
  explicitly scoped `email.otp` request; that response is not a logging
  surface.
- raw search text, which can itself contain private information;
- plaintext encrypted-store values or user-exported password CSV rows.
- Google OAuth authorization codes, refresh tokens, access tokens, client
  secrets, and token-endpoint response bodies.

Website credential and financial values are allowed only in the successful
response of an authenticated, field-selective request whose grant and
record-level origin/purpose scopes pass. That response exception does not
permit copying values into audits, listings, errors, diagnostics, fixtures,
screenshots, or documentation.

The prohibition applies to audit records, diagnostic stdout/stderr, exceptions,
tests containing real user data, fixtures, screenshots, and documentation. A
one-time credential handle is returned only by its explicit authorized MCP
operation and must not be copied into audit or error output. Likewise,
authorized message bodies are response data, not diagnostic logging.

The `request_auth_code` tool intentionally returns one recent target-bound OTP
after transport authentication and an active `email.otp` grant succeed. This
is a narrow information response, not permission to include OTPs in audits,
errors, listings, or unrelated tools.

## Current Level/Format Policy

There are no `debug`, `info`, `warning`, or `error` logging levels today. If a
future requirement needs diagnostics, define the consumer and redaction rules
before adding a logging dependency. Preserve stdout exclusively for the MCP
protocol when the server is running.

## Representative Tests

- `tests/stopaskingme/test_service.py::test_otp_is_separate_and_not_logged`
  verifies that the code value is absent from audit output.
- `tests/stopaskingme/test_service.py::test_audit_storage_does_not_expose_search_terms`
  verifies raw search text is absent from persisted audit metadata.
- `tests/stopaskingme/test_inbox.py` verifies the local review HTML contains
  credential metadata without a password field.
