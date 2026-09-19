# Current Repository Evidence

Inspected 2026-09-16 from the working tree at `/Users/lfan/Project/agent-autofill`.

- `agent_autofill/storage.py`: `KeyringProvider` stores/retrieves opaque byte
  secrets through Python `keyring`; `Store` seals JSON values with Fernet in
  SQLite `kv` and commits each write.
- `agent_autofill/cli.py`: `email-add` prompts for an IMAP password or OAuth2
  bearer token and stores it in Keychain; `setup` configures a password-based
  IMAP source and local Messages source.
- `agent_autofill/server.py`: `_register_connections` recreates an
  `IMAPEmailAdapter` from saved connection metadata plus the Keychain secret
  on each server start; there is no provider login or refresh lifecycle.
- `agent_autofill/imap_adapter.py`: each operation opens `IMAP4_SSL`, authenticates,
  selects the mailbox readonly, then logs out in `finally`.
- `agent_autofill/service.py`: `request_auth_code` enforces an
  `email.otp` grant, 1..3600 second age, unique code candidate, target-domain
  binding, and one-time use via `otp-used`; it returns the code, 60-second
  expiry, and account/message provenance, while audit metadata omits the code.
- `agent_autofill/service.py`: profile records contain `field_id`, `category`,
  `display_name`, `value`, `sensitivity`, `allowed_origins`,
  `allowed_purposes`, and `updated_at`; `list_profile_fields` omits `value`.
- `tests/agent_autofill/`: 18 tests currently cover storage encryption, scoped
  service reads, OTP ambiguity/reuse/origin checks, transport authentication,
  Chrome import, Messages scope, and inbox redaction. There is no end-to-end
  CLI login/session test or skill test.
- `AGENTS.md` requires local-first, information-only behavior, OS credential
  storage, loopback-only review surfaces, `apply_patch`, focused tests, and
  Ruff; it forbids browser automation, outbound message sending, and form
  submission without a new product decision.
