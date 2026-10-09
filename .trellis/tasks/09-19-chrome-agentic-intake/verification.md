# Chrome intake verification

Development uses synthetic records only. No live Chrome source, personal
Agent Autofill database, or OS credential store was accessed for these tests.
No production Python module was changed for this intake task; existing
uncommitted first-run changes remain separate and preserved.

## Independent consuming-agent exercise

The evaluator received the skill, a synthetic selected Chrome profile, an
absent destination, and a natural cold-start request. The fixture has two
address groups for the same person, another person's group, instruction-like
source text, and out-of-scope history/payment tables. Synthetic keys are
explicit; no Keychain lookup is permitted.

- Initial review rejected the source-embedded instruction and wrote nothing,
  but offered unresolved ownership/currentness as eligible adds. Corrected the
  skill to require held status and questions before final save approval.
- A separate independent replay asked about the two addresses and other
  person, held the embedded instruction, and wrote nothing. It initially
  demanded an existing destination; corrected the first-run instruction.
  Replay then recognized an absent destination as valid and deferred creation
  until approved writes.
- The original evaluator accepted ownership/currentness clarification and
  displayed all seven eligible fields, exact values, source grouping,
  sensitivity, and explicit empty scope lists. Excluded groups stayed held.
- Cancellation produced no writes. The lead independently verified that the
  destination file still did not exist after review and cancellation.
- Fresh extraction using the corrected documented recipe returned 22 fields
  in four source groups. The evaluator displayed all seven eligible fields
  using stable source-derived IDs, plus the excluded groups.
- A mixed response approved only the email row and edited the name. The
  evaluator executed the recipe and saved exactly the unchanged approved
  email, holding the edited name. The lead independently inspected only this
  synthetic encrypted fixture with its explicit test key: one stored record,
  correct approved value, sensitivity, scope lists, and safe provenance.
- Repeat intake produced identical source IDs, found the email metadata,
  and held its comparison because no authorized value-read grant was present.
  It did not guess a no-op, create grants, invent alternate IDs, or overwrite.
  The lead verified that the destination still contained exactly one record.
- Independent instruction/document review resulted in clarification of the
  form-scoped MCP write path versus local intake, explicit cancellation and
  sensitivity handling, source-read consent, and synchronization of the
  approval-change contract. Summaries point to the skill rather than repeat
  its entire table contract.

These are observed agent behaviors, not a runtime guarantee of future model
compliance. The save helper receives agent-selected IDs, not proof of human
consent. Local administrative writes remain individually committed, and
metadata rechecks are not atomic compare-and-swap.

## Deterministic recipe coverage

The tests execute the exact Python fence from `references/local-intake.md`.
They cover integer field mappings (separate full street and line1), source
grouping and profile identity, allowlisting, stable IDs, source nonmutation,
unsupported/malformed/conflicting source data, locks, committed uncheckpointed
WAL reads, no approval/cancellation, held/no-op exclusion, required approved
fields, provenance/scopes, candidate-to-target mapping, stale updates,
duplicate approvals/candidates/targets, canonical target IDs, unavailable
metadata, and per-record partial failure with value-free diagnostics.

## Verification status

- Full regression suite: 114 passed (95 existing plus 19 recipe cases).
- Ruff: production/tests and the extracted recipe fence passed.
- Mypy: 13 source modules passed.
- Python compilation, skill validation, task-context validation, and diff
  whitespace checks passed.

No live Chrome acceptance is claimed. Support is limited to the documented
integer structured-address schema; a locked source must be closed by the user
before retrying. No embedded model, browser automation, or new broker API was
added. Existing raw import commands retain their separate behavior.

The completed forward-test fixture directory was moved to macOS Trash for
recoverable cleanup. It contained only synthetic data and an explicit test key.

Implementation remains uncommitted; no commit/push was authorized for this
task. Trellis finish-work stops before archive/journal auto-commits when
current-task changes are uncommitted. Prior first-run work is preserved.
