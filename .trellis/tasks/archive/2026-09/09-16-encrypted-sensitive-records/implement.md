# Implementation Plan

## Ordered Checklist

1. Update `AGENTS.md` and backend quality/logging/database/error guidance to
   permit direct website-credential and financial reads only in successful
   authenticated, scoped responses. Preserve the absolute prohibition for
   connector secrets and diagnostic/audit surfaces.
2. Add atomic encrypted batch-write and deletion primitives to `Store` without
   changing the base table schema.
3. Normalize/validate record ids, origins, purposes, field ids, sensitivities,
   and forbidden transient-secret fields in `Broker`.
4. Extend credential records compatibly and add financial record put/list/read
   behavior with broker-derived safe metadata hints.
5. Implement `Broker.write_records` with full-batch validation,
   type-specific grants, `confirmed: true`, atomic commit, metadata-only result,
   and redacted audit.
6. Implement field-selective `request_credential_fields` and
   `request_financial_fields` with no per-use approval branch.
7. Expose the new MCP tools and safe metadata listings; preserve the existing
   credential-handle tool for compatibility.
8. Add local record deletion and exact-id grant revocation.
9. Update operational docs with placeholder-only examples and the explicit
   information-layer/browser-action boundary.

## Focused Tests

- Encryption-at-rest and persistence across a new `Store`/`Broker` process.
- Legacy credential compatibility and unchanged credential-handle behavior.
- Metadata listing redaction for password, full account/card number, security
  code, routing number, and arbitrary financial values.
- Batch create/update/no-op, duplicate ids, size bounds, type validation,
  transient-secret rejection, missing confirmation, missing/wrong/expired/
  revoked grant, and atomic rollback on any invalid entry.
- Direct read success plus unauthenticated, wrong-resource, wrong-origin,
  wrong-purpose, expired, revoked, missing-field, and deleted-record refusals.
- Explicit proof that read tools have no approval parameter and return only
  selected fields with provenance.
- Audit/error/raw-SQLite checks for plaintext secret absence.

## Validation

```bash
PYTHONPATH=. pytest -q tests/agent_autofill/test_storage.py \
  tests/agent_autofill/test_service.py tests/agent_autofill/test_transport.py \
  tests/agent_autofill/test_cli.py tests/agent_autofill/test_inbox.py
PYTHONPATH=. pytest -q
ruff check agent_autofill tests
python3 -m compileall -q agent_autofill tests
python3 ./.trellis/scripts/task.py validate \
  .trellis/tasks/09-16-encrypted-sensitive-records
```

## Risk and Rollback

- Batch transaction mistakes could cause partial saves; require an injected
  mid-batch failure test that proves rollback.
- Metadata hints can become a disclosure channel; keep generation centralized
  and assert exact allowed keys.
- Broad wildcard grants are compatible with the current grant model but less
  narrow than record-id grants. The skill should prefer exact ids and short
  expiry; changing grant semantics is deferred.
- Never delete existing records as part of code rollback.

