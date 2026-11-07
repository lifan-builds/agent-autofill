# Chrome intake benchmarks

These benchmarks use synthetic structured Chrome candidates. They measure the
agent-facing organization step, not live Chrome compatibility or model
obedience. The executable assertions live in
`tests/agent_autofill/test_chrome_intake_recipes.py`.

| Benchmark | Before the organizer | Target and current result |
| --- | --- | --- |
| Group fidelity | A flat list left the host agent to infer ownership groups. | Preserve 100% of source groups and fields; pass: 5/5 groups, 7/7 fields |
| Canonical matching | Formatting differences were visible but had no stable comparison value. | Normalize email case/space and comparable whitespace without changing source values; pass |
| Ambiguity holds | The host agent had to spot street duplicates and prompt-like values manually. | Hold street/street_line1 overlap, cross-group duplicates, unsplit names, and instruction-like text; pass |
| Replay stability | Repeated scans had no organized output to compare. | Same synthetic input produces byte-for-byte equivalent groups and flags; pass |
| Write safety | Organization was not itself a proof of approval. | Organizer performs no writes; existing final-review/save tests remain required |

## Iteration rule

Improve the organizer only when a benchmark exposes a concrete miss. A new
normalizer must preserve the original `value`, never merge source groups, and
add a focused synthetic case before changing classification guidance. Do not
optimize for a higher eligible count by weakening holds.

## Current workflow

1. Read the selected profile's supported structured tables in the documented
   read-only transaction.
2. Organize candidates into source groups and inspect normalized comparisons
   plus review flags.
3. Ask grouped ownership/currentness questions for flagged groups.
4. Generate the complete add/update/no-op/held review and save only the exact
   approved rows.

The organizer does not read Chrome history, passwords, payment data, OTPs, or
sync payloads, and it does not create a raw export or staging database.
