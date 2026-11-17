## Project guidance

- Keep Agent Autofill local-first and information-only: the broker never drives a browser, sends outbound messages, or submits external forms.
- Never expose passwords, payment numbers, security codes, mailbox tokens, Messages DB copies, or real OTPs in logs, audits, diagnostics, screenshots, or docs (synthetic OTPs are allowed only in focused redaction/response tests).
- Store connector secrets in the OS credential store and bind local HTTP review/fill surfaces strictly to loopback (`127.0.0.1` / `localhost` / `::1`).
- Keep `.agents/skills/agent-autofill*` synced with `~/.agents/skills/agent-autofill*`.
- Verify changes with `python3 -m pytest -q tests`, `node --test tests/agent_autofill/*.test.cjs`, and `ruff check agent_autofill tests`.
