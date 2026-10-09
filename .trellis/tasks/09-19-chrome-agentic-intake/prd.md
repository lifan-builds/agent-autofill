# Chrome agentic intake

## Goal

Bootstrap Agent Autofill from Chrome through an agent-led conversation: read source data without importing it, organize and deduplicate candidates, resolve ambiguity with the user, present the complete proposed changes, then save only explicitly approved records.

## Confirmed user decisions

- This is agentic intake, not a bulk import or a wizard requiring manual review of every raw entry.
- Ask the user to resolve ambiguous ownership, conflicting values, and current-versus-old information; do not guess.
- Every proposed saved record, including clear matches, must appear in the final approval.
- Nothing is saved before final approval; unresolved items remain excluded.
- The existing consuming agent may interpret selected ordinary profile values in its normal model/conversation context. Agent Autofill provides instructions, not an embedded model, on-device inference service, or separate agent runtime.
- Task creation and planning were approved after recommending a first slice of structured names, contact details, and addresses rather than autocomplete history.

## Repository evidence

- `agent_autofill/chrome.py:62`: existing extraction reads the `autofill` history table, not structured address tables.
- `agent_autofill/chrome.py:47`: the current snapshot helper copies the database file without a transaction-consistency guarantee.
- `agent_autofill/chrome.py:127`: history import derives a record ID from field name alone; multiple values collide.
- `agent_autofill/service.py:1016`: `write_records` already validates confirmed, scoped batches and commits them atomically, but requires a target website origin and active record-write grants.
- `.agents/skills/agent-autofill-form-memory/SKILL.md`: existing agent-side proposal/review workflow can inform this feature, but its current trigger is form work, not cold-start onboarding. Its read values must not be silently repersisted.
- `agent_autofill/service.py:549`: the existing local administrative `Broker.put_profile` accepts value, scopes, sensitivity, and provenance without fabricating a website origin. CLI `profile set` uses this path but does not expose provenance and commits records individually.
- Earlier read-only feasibility inspection in this conversation decoded structured records from the locally installed Chrome schema (`addresses` and `address_type_tokens`). It used a diagnostic main-file view under a live database lock, not a production-safe import. No personal source values belong in task artifacts or fixtures.

## Requirements

- R1: Read only a user-selected Chrome profile and supported structured identity/contact/address fields. Keep extraction separate from saved profile mutations.
- R2: Preserve source record grouping and provenance, deduplicate without conflating different people/addresses, and mark unverifiable comparisons as unknown rather than no-op.
- R3: Provide a repository-local intake skill for a consuming agent with local execution access. Let that agent organize candidates and ask concise ambiguity questions. Source text is untrusted data, never instructions. Do not add an embedded model, broker endpoint, schema, packaged extraction service, or automated decision engine.
- R4: Present all proposed adds, updates, no-ops, and held rows before final approval. Changing any reviewed property (value, identity/grouping, category, label, sensitivity, provenance, or scope) invalidates approval for that affected proposal.
- R5: Use the existing local administrative profile-write API only after approval of the exact displayed batch or a named subset. Preserve source provenance and explicit approved scopes. Rejected, unresolved, and no-op entries cause no writes. Never overwrite an existing record silently. Do not manufacture a target website or auto-grant permissions to use MCP; grant requirements for normal agent reads/writes remain unchanged.
- R6: Handle locked, unsupported, or inconsistent Chrome databases safely and explain recovery without closing Chrome automatically or weakening protections.
- R7: Validate executable instruction recipes with synthetic fixtures and independently forward-test the skill on realistic synthetic conversations. Separate deterministic recipe evidence from evidence of agent behavior; static phrase-matching does not establish enforcement.

## Acceptance Criteria

- [x] Extraction and ambiguity resolution leave the Agent Autofill profile unchanged.
- [x] The agent distinguishes duplicate data from multiple owners or addresses; unresolved ownership and values are not saved.
- [x] Clear matches and resolved cases both appear in the final review, with exact proposed values and scopes.
- [x] Partial approval writes only approved complete entries; cancellation writes nothing; changed proposals require renewed approval.
- [x] Repeated intake neither creates duplicates nor silently replaces an existing value.
- [x] Locked/unsupported-source tests fail safely; no raw profile database or personal values enter repository artifacts or diagnostics.
- [x] Synthetic recipe checks, independent skill forward-tests, skill validation, and applicable existing regression checks pass. Results distinguish instruction-following from broker-enforced guarantees.

Acceptance evidence and limitations are recorded in `verification.md`.

## Approved first-slice boundary

- Structured names/contact/addresses only. No arbitrary autocomplete-history mining, password/card/OTP extraction, browser control, or form submission.
- Deliver instructions and a focused reference with validated local recipes; do not change production broker/importer code in this slice. Existing raw import/setup commands remain available but are explicitly not this approval-gated intake workflow.
- Prior first-run workflow changes are uncommitted and must be preserved. No automatic commit, push, or live-data import is authorized by planning consent.

## Decisions and limitations

- Entry point: invoke the proposed `agent-autofill-chrome-intake` skill in the user's existing local agent. MCP-only agents without local execution access are outside this first slice.
- No durable candidate staging or raw database export. Final save approval controls Agent Autofill persistence, not retention by the user's selected agent provider.
- Read-only Chrome extraction stops on a lock or unsupported schema and asks the user to close Chrome or select a supported profile. Do not repeat the diagnostic immutable-read workaround as a supported import path.
- Approved saves reuse local administrative authority explicitly granted by the user; they are not a new unscoped MCP capability. Compare saved values only through authorized existing reads; if comparison is unavailable, hold that update instead of declaring a no-op or overwriting it.
- Local administrative writes commit per record. Stop on a failure, report exactly which approved records were saved, and do not claim an all-or-nothing batch transaction or silently retry.
- Approval and ambiguity handling are skill instructions enforced by the consuming agent, not a new broker-side approval-proof mechanism. Existing direct administrative commands are not made impossible to misuse.
- Browser schema compatibility is checked at runtime against documented supported layouts, not promised for all Chrome versions/platforms. Personal data is excluded from development fixtures and artifacts.

## Planning status

The user approved the final instructions-only scope on 2026-09-19. Implementation is authorized; saving live Chrome records is not part of this development task.
