# First-run workflow verification

Implementation and acceptance checks completed on 2026-09-19. Changes remain
uncommitted as specified by the approved implementation plan; no push or live
provider operation was performed.

## Evidence

- AC1: `test_cli.py` verifies exact selection, preview confirmation, skip and
  cancellation, blank identity through `cli.main`, existing-token preservation
  through `cli.main`, revoked identities, missing scope, multiple/unknown/
  wildcard email selections, empty selection, and independent permissions.
- AC2/AC3/AC5: `test_first_run.py` drives real CLI setup and the server
  JSON-lines loop across a reopened encrypted database. The test asserts
  reconstructed adapter use, successful profile/search/OTP responses,
  provenance, persistent replay denial, and secret-free setup/errors/audits.
- AC4: eight parameterized first-run authorization cases plus expiry,
  revocation, stale-code and ambiguity cases cover the refused paths. Existing
  service tests retain exact hostname-binding and cross-broker consumption
  coverage.
- AC6: `NEXT_STEPS.md` and `docs/agent-autofill.md` reflect current behavior,
  renewal commands, and the fixture/live boundary. The backend quality spec
  records the new setup contract.
- AC7: full pytest (95 tests), Ruff for source/tests, mypy for all 13 source
  modules, compileall, `git diff --check`, and task context validation passed.
- Independent full-scope review found and fixed one coverage gap by asserting
  exact persisted grant resources/scopes and the full 600-second lifetime in
  the integration path. No unresolved findings remained; the final 13-case
  first-run suite also passed after that change.

## Limits

The test replaces only external key/provider I/O, not persistence, broker
authorization, connector reconstruction, or server dispatch. It runs entry
points in-process and does not establish installation/subprocess, live
Keychain, or live IMAP/Gmail acceptance. The real migrated database, user
browser data, and operating-system credentials were not accessed.

## Handoff

Product acceptance is complete. Trellis archive/journal wrap-up awaits a
separately authorized commit; its finish-work skill refuses archival while
current-task code is uncommitted. No product implementation is deferred.
