# Implementation plan

The user approved the latest final summary on 2026-09-19; the task is in progress.

## Ordered checklist

1. Load `trellis-before-dev` and applicable backend/shared guides. Confirm approved PRD and working-tree ownership.
2. Add the minimal optional selection/preview/confirmation step to setup. Reuse `Broker.grant`. Cover skip/cancel/invalid selections, scope, exact grants, existing tokens, and revoked identities in `test_cli.py`.
3. Add `tests/agent_autofill/test_first_run.py`, reusing fixture patterns in `test_chrome.py`, `test_cli.py`, and `test_server.py`. Exercise setup, reopened database, connector reconstruction, JSON-lines requests, persisted OTP consumption, and audit/output redaction.
4. Map existing service/transport tests to AC4; add missing coverage without changing contracts to fit stale prose.
5. Reconcile `NEXT_STEPS.md` and `docs/agent-autofill.md`; change README only if needed for setup instructions. State fixture/live acceptance limitations.
6. Run `trellis-check`, tests, Ruff, and diff review. Use `trellis-finish-work` after completion. Do not auto-commit or push.

## Validation commands

Use the existing dependency-capable Python interpreter from the repository root:

```sh
PYTHONPATH=. python -m pytest -q tests/agent_autofill/test_cli.py tests/agent_autofill/test_first_run.py tests/agent_autofill/test_transport.py tests/agent_autofill/test_service.py
PYTHONPATH=. python -m pytest -q
ruff check agent_autofill tests/agent_autofill
python -m compileall -q agent_autofill tests/agent_autofill
git diff --check
```

Planning validation: `python3 .trellis/scripts/task.py validate .trellis/tasks/09-19-first-run-workflow`, followed by a complete final PRD read. No live-provider smoke test is required or authorized.

## Ownership and risky files

Primary production file: `agent_autofill/cli.py`. Service/storage/server should remain unchanged unless the integration path reveals a concrete defect requiring a minimal fix. After approval, separable tests/docs may be delegated with independent ownership to the specified Luna Max workers. If that model is unavailable, work directly instead of substituting an unapproved model. Curated context manifests support future dispatch.

Never touch live databases, Keychain credentials, unrelated user edits, or Git history. If the UX or security scope changes materially during implementation, return for revised planning review.
