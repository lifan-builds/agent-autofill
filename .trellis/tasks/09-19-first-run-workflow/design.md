# First-run authorized workflow design

Status: approved by the user on 2026-09-19.

## Architecture and contracts

Keep orchestration in `agent_autofill/cli.py`, reusing saved metadata and `Broker.grant`. After import/connector prompts, offer optional authorization only when an agent identity exists. Select exact resources, validate the full proposal, show a value-free preview, and persist only after affirmative confirmation. No generalized permission UI or new grant model.

Independent selections map to `profile.read` for each exact field, `email.search` for one account with null origin/purpose, and `email.otp` for that account with explicit origin/purpose. All wizard grants last 600 seconds. Reject unknown IDs, wildcard selections, and missing required scopes before writing. Empty selections create nothing. Do not rotate tokens; explain a revoked identity rather than creating unusable grants or silently reactivating it. The standalone grant command remains unchanged.

The preview states that search is account-wide. Do not print profile values, mailbox credentials, or imported passwords. Keep the existing once-only new-agent token output, without repeating it in summaries.

## Integration test flow

Use temporary file-backed encrypted SQLite, synthetic Chrome SQLite fixtures, a fake key provider shared by CLI/server construction, and a fake IMAP adapter for source checks/search/recent messages. Drive `cli.main` with explicit fixture paths and mocked input/getpass; then drive `server.main` with JSON-lines stdin and capture stdout. Reconstruct the Broker and connector from persisted state, rather than retaining the setup Broker. Never patch `_call`, authorization, grants, or Store persistence.

Parse JSON-RPC responses and nested tool-result JSON. Capture the issued token only inside the test and authenticate via the existing mechanism. Search a benign message; request the separate synthetic OTP via `request_auth_code`. Inspect audit rows independently of permitted response output. Reopen storage for replay denial. Reuse current service tests for the broader security matrix and add missing boundary cases only.

## Compatibility and migration

No schema migration, dependency, public tool signature change, or default-path change. Setup gains optional prompts; update tests and docs for their order. No live data migration or credential-store operation. In-process entry-point tests avoid production test-only switches and do not claim subprocess/installation coverage.

## Documentation reconciliation

Correct unsupported roadmap promises about time-window search, newest-code selection, confidence, and returned expiry. Describe freshness and per-agent/account/message consumption accurately. Replace blanket claims that credentials cannot be returned through MCP with the existing authenticated origin/purpose-scoped read contract; connector-secret export stays prohibited. Mark Gmail OAuth delivered; Outlook and other deferred work remain outside this slice.

## Operational considerations

Skipping preserves existing grants and prior setup/import effects. Validate all user input before the first grant write; do not add transactional infrastructure without a demonstrated need. Rollback concerns this task's CLI/tests/docs only, never user data. Surface security discoveries outside this scope separately instead of silently expanding the task.
