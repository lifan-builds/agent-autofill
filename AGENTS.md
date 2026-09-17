# StopAskingMe development guidance

- Keep the product local-first and information-only unless the scope is
  explicitly expanded.
- Never expose passwords, mailbox tokens, Messages database copies, or real
  OTPs in logs, screenshots, or documentation. A recent OTP may appear only
  in the successful response to an authenticated, explicitly scoped
  `email.otp` request; it must never be persisted or copied to diagnostics.
  Deterministic synthetic OTPs are allowed in focused response/redaction tests.
- Keep connector credentials in the operating-system credential store.
- Preserve loopback-only networking for local review surfaces.
- Use `apply_patch` for source edits and run the focused test suite plus Ruff
  before handing work back.
- Do not add browser automation, outbound message sending, or form submission
  to the broker without a new explicit product decision.
