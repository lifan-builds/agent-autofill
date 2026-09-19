# Implementation Plan

## Ordered Checklist

1. Update `AGENTS.md` and backend quality/logging/database guidance so an OTP
   may appear only in the successful scoped response, while connector secrets
   and all diagnostic/audit surfaces remain protected.
2. Add safe deletion/transaction support needed to purge legacy plaintext
   `otp-used` keys and replace them with non-secret consumption markers.
3. Add `IMAPEmailAdapter.check_connection()` and share read-only
   connect/select/logout behavior without adding mailbox mutations.
4. Change `email-add` to test before saving, use replacement-safe Keychain
   names, and clean up on partial failure.
5. Add `email-test`; keep output non-secret and actionable.
6. Harden fresh-process adapter registration and unavailable-source errors.
7. Add received-at provenance to `request_auth_code`, remove fake expiry
   semantics, and preserve one-time retrieval without storing the code.
8. Document generic IMAP setup, test, reconnect, grant, OTP request, and
   removal using placeholders only.
9. Add a Google installed-app OAuth module using loopback authorization, state
   validation, PKCE, bounded callback waiting, safe token exchange, and
   in-memory access-token caching/refresh.
10. Add `email-add-gmail` using a user-supplied Desktop client JSON; verify
    Gmail IMAP before atomically saving only refresh/client state to Keychain.
11. Extend saved-connection testing and fresh server reconstruction for
    `auth_kind=google_oauth`, while preserving password and legacy static
    bearer-token compatibility.
12. Add deterministic OAuth/CLI/server tests and document the exact Google
    Cloud setup plus an opt-in real Gmail login/reconnect smoke procedure.

## Focused Tests

- Adapter password/XOAUTH2 login, readonly select, logout, and safe failures
  through a fake `imaplib` connection.
- CLI test-before-save, new-process reconnect, replacement rollback, missing
  Keychain entry, `email-test`, and `source-remove` cleanup.
- OTP transport authentication; account/origin/purpose grant matching;
  expired/revoked grants; stale, ambiguous, unbound, and consumed codes;
  received-at provenance; no approval parameter.
- Raw SQLite/audit/error assertions proving no OTP, password, bearer token, or
  message body is persisted or emitted outside the successful response.
- OAuth client JSON validation, authorization URL/state/PKCE, loopback
  callback refusal/timeout, token exchange/refresh, memory-only access-token
  caching, and safe provider errors using deterministic fakes.
- Gmail CLI verification-before-save, Keychain refresh-state persistence,
  fresh-process reconstruction, refreshed XOAUTH2, replacement rollback, and
  source removal without real tokens in fixtures or output.

## Validation

```bash
PYTHONPATH=. pytest -q tests/agent_autofill/test_imap_adapter.py \
  tests/agent_autofill/test_cli.py tests/agent_autofill/test_service.py \
  tests/agent_autofill/test_transport.py tests/agent_autofill/test_storage.py
PYTHONPATH=. pytest -q
ruff check agent_autofill tests
python3 -m compileall -q agent_autofill tests
python3 ./.trellis/scripts/task.py validate \
  .trellis/tasks/09-16-persistent-email-otp
```

## Risk and Rollback

- Replacing a Keychain secret is the main partial-failure risk; never delete
  the old secret until the new database reference commits.
- Purging legacy OTP-consumption rows may make an old code retrievable once,
  but the maximum one-hour age bound limits the window and removes plaintext
  OTP remnants. Run the purge before accepting new requests.
- If generic IMAP works in fakes but no live account is supplied, report live
  provider behavior as unverified rather than weakening TLS or auth handling.
- A user-owned Desktop OAuth client is required for a live Gmail smoke. A
  bundled client would introduce project registration, publishing, and Google
  verification obligations and is intentionally excluded.
