# Implementation plan

1. Read the backend guidelines and inspect existing CLI, Inbox, extension, and
   metadata tests.
2. Add safe broker status helpers and additive `doctor`/`quickstart` CLI output.
3. Add interactive profile add/edit input with metadata-only confirmation.
4. Rework Inbox first view into overview/attention cards with advanced details.
5. Improve extension local preferences, bridge/origin status, and recovery text.
6. Add focused tests for redaction, status output, profile input handling, Inbox
   rendering, and extension behavior where the repository test style supports it.
7. Update user documentation and run focused tests, full pytest, Ruff,
   compileall, and `git diff --check`.

Do not modify unrelated existing worktree changes or commit/push automatically.

## Approved continuation: implement credential delivery choices

- Add `Broker.request_credential` and advertise it through the authenticated MCP
  dispatcher, requiring an explicit direct/extension choice and reusing existing
  authorization paths.
- Add `grant-site` interactive/flag-based delivery selection and tests; keep
  explicit legacy operation arguments compatible.
- Surface the choice in Inbox and update consumers/docs to use the executable
  entry points. Extension setup remains optional.
- Test actual MCP direct vs extension results and local bridge consumption,
  missing/invalid choices, no cross-mode fallback, redaction, auth, origin,
  expiry, revocation, and replay rejection with synthetic data only.
