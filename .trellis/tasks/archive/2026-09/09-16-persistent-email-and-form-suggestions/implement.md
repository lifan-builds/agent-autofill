# Integration Execution Plan

## Ordered Delivery

1. Implement and check `09-16-persistent-email-otp`.
2. Implement and check `09-16-encrypted-sensitive-records`; this owns the
   shared `write_records` and direct sensitive-read contracts.
3. Implement and check `09-16-suggest-form-records-skill` against the landed
   broker/tool schemas.
4. Run a parent integration review across the final tool catalog, grant names,
   record schemas, skill instructions, policy/spec language, and docs.

The parent is a coordination task, not a separate code implementation target.
Start and archive the children individually; keep the parent in planning until
all three child checks and the cross-child review are complete.

## Integration Review Checklist

- Confirm `AGENTS.md` and backend specs distinguish mailbox connector secrets
  from authorized OTP/website-credential/financial response data.
- Confirm no browser control, browser helper, outbound messaging, or form
  submission entered the broker or skill.
- Confirm the skill references the exact MCP tool names, record types, grant
  operations, and response shapes implemented by the broker.
- Confirm retrieval has no approval flag or user-confirmation branch beyond
  transport authentication and active scoped grants.
- Confirm only `write_records` requires `confirmed: true` and that the skill
  obtains approval before invoking it.
- Confirm metadata and audit paths cannot expose OTPs, connector secrets,
  passwords, full financial values, or transient tokens.

## Final Validation

```bash
PYTHONPATH=. pytest -q
ruff check stopaskingme tests
python3 -m compileall -q stopaskingme tests
python3 /Users/lfan/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  .agents/skills/stopaskingme-form-memory
python3 ./.trellis/scripts/task.py validate \
  .trellis/tasks/09-16-persistent-email-otp
python3 ./.trellis/scripts/task.py validate \
  .trellis/tasks/09-16-encrypted-sensitive-records
python3 ./.trellis/scripts/task.py validate \
  .trellis/tasks/09-16-suggest-form-records-skill
```

Perform a manual live IMAP smoke only with a user-owned test account and
operator-entered secret. Never capture its terminal output in fixtures,
screenshots, or task artifacts. If no live account is supplied, report the
automated fake-provider path as verified and live reconnection as unverified.

## Rollback Points

- Revert each child independently if its focused tests fail.
- Keep old credential-handle behavior until direct-read callers are verified.
- Do not delete existing encrypted user records during rollback.

