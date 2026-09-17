# Error Handling

> Current error propagation and client-boundary behavior.

## Error Types

The service layer exposes a small exception hierarchy:

```python
class BrokerError(Exception): ...
class AuthorizationError(BrokerError): ...
class CredentialUseError(BrokerError): ...
```

- Raise `BrokerError` for invalid broker input, failed authentication, missing
  broker resources, ambiguous OTPs, and unknown operations.
- Raise `AuthorizationError` when a valid request lacks an active grant or
  violates origin/purpose restrictions.
- Raise `CredentialUseError` for invalid credential-use requests and consumed,
  expired, or mismatched handles.
- Provider modules use provider-local errors where the broker hierarchy is not
  appropriate: `ChromeImportError`, `MessagesUnavailable`, `KeyError` for an
  adapter lookup miss, `GoogleOAuthError` for OAuth setup/exchange failures,
  and `RuntimeError` for unavailable credential stores or adapter
  configuration.

## Validation and Propagation

- Validate at the layer that owns the rule. `Broker` validates limits,
  required origin/purpose values, authorization grants, and one-time-use
  state; adapters validate source availability and provider responses.
- Use clear, non-secret error text. Examples include `limit must be an
  integer`, `message not found`, and `target origin does not match credential
  origin`.
- Preserve the original cause with `raise ... from exc` when translating a
  dependency failure, as `KeyringProvider` and `Store._open` do.
- Catch narrow exceptions when they represent an expected branch. Examples:
  `Broker.get_message` catches `KeyError` to try the next adapter, and
  `_bounded_limit` catches only `TypeError` and `ValueError`.
- A broad catch is used only at cleanup, lossy parsing, or the outer transport
  boundary. Do not use it to make authorization or storage failures look like
  success.

## Transport and CLI Boundaries

- `server.main` catches request failures at the JSON-lines boundary and emits a
  JSON-RPC error with code `-32000`, exception type, and message. Successful
  tool calls use the MCP `content` list shape.
- The CLI uses `SystemExit` for actionable operator-facing refusals, such as a
  non-loopback inbox host, an unacknowledged password import, or failure to
  remove a Keychain secret safely.
- Do not emit partial success when a security-sensitive prerequisite fails.
  For example, `source-remove` leaves the source configured if its Keychain
  secret could not be removed.

## Resource Cleanup

- Close database and network handles deterministically. The adapters use
  context managers or `finally` blocks for this purpose.
- Chrome snapshot cleanup must run even when schema inspection or decoding
  fails; the current `finally` block closes the database and deletes the file.
- Read-only adapters may return an empty result for a missing optional data
  source, but a specifically requested missing record raises an error.

## Common Mistakes to Avoid

- Returning passwords, tokens, message bodies, OTPs, or raw request payloads in
  error messages. An OTP is permitted only in the successful response of the
  separately scoped `email.otp` use case.
- Catching `Exception` around broker authorization and continuing with data
  access.
- Moving business validation into only `cli.py` or `server.py`, which would
  leave direct `Broker` callers unprotected.
- Treating adapter `KeyError` as a server failure before other adapters and the
  in-memory fallback have been checked.
- Assuming every parse failure can be ignored. The few existing fallback
  parses (`imap_adapter._date`, message dates) are intentionally lossy provider
  normalization, not a general convention.
- Including a Google authorization code, refresh token, access token, client
  secret, or provider response body in an exception. OAuth failures must use a
  fixed operator-facing message and preserve the dependency exception only as
  a private cause.
- Treating a non-exception IMAP `NO` or `BAD` return as success. Both password
  login and XOAUTH2 authenticate calls must explicitly require `OK` before
  selecting a mailbox.

## Representative Tests

- `tests/stopaskingme/test_service.py` covers rejected grants, OTP ambiguity,
  origin binding, revocation, and one-time handles.
- `tests/stopaskingme/test_transport.py` covers authentication failure at the
  transport dispatcher.
- `tests/stopaskingme/test_chrome.py` covers absent optional Chrome databases
  as a supported empty-source case.
