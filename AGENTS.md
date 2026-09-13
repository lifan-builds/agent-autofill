# StopAskingMe development guidance

- Keep the product local-first and information-only unless the scope is
  explicitly expanded.
- Never expose passwords, mailbox tokens, Messages database copies, or OTPs in
  MCP responses, logs, tests, screenshots, or documentation.
- Keep connector credentials in the operating-system credential store.
- Preserve loopback-only networking for local review surfaces.
- Use `apply_patch` for source edits and run the focused test suite plus Ruff
  before handing work back.
- Do not add browser automation, outbound message sending, or form submission
  to the broker without a new explicit product decision.
