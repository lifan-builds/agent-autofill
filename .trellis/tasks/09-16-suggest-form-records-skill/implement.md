# Implementation Plan

## Dependency

Implement after `09-16-encrypted-sensitive-records` so the skill is written and
tested against the actual `write_records`, direct-read, metadata, record, and
grant contracts rather than a speculative interface.

## Ordered Checklist

1. Confirm the landed MCP catalog and copy its exact schemas into one concise
   `references/record-contracts.md`.
2. Create `.agents/skills/stopaskingme-form-memory/SKILL.md` with a precise
   implicit-discovery description and the workflow in `design.md`.
3. Teach the skill to use metadata first, field-selective reads only when
   needed, semantic matching with confidence/ambiguity, and proactive stable
   record suggestions.
4. Encode the exact proposal/approval boundary, sensitive previews, transient
   exclusions, dedicated record routing, and no-extra-approval retrieval rule.
5. Keep browser interaction and browser approval policy explicitly outside the
   skill/MCP layer.
6. Add focused broker/transport tests for the profile portion of
   `write_records` if they are not already owned by the sensitive-record child.
7. Validate the skill and run independent realistic forward tests in a
   temporary workspace; revise only for observed routing or behavior defects.

## Validation

```bash
python3 /Users/lfan/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  .agents/skills/stopaskingme-form-memory
PYTHONPATH=. pytest -q tests/stopaskingme/test_service.py \
  tests/stopaskingme/test_transport.py
PYTHONPATH=. pytest -q
ruff check stopaskingme tests
python3 -m compileall -q stopaskingme tests
python3 ./.trellis/scripts/task.py validate \
  .trellis/tasks/09-16-suggest-form-records-skill
```

## Forward-Test Acceptance

- Correct implicit invocation for form-memory work and no invocation for
  unrelated email/search prompts.
- Exact add/update/no-op classification when the necessary read grants exist.
- Visible ambiguity when several records match or a saved comparison is not
  authorized.
- No write before explicit approval; partial approval writes only the selected
  rows.
- Sensitive proposal previews stay masked; OTPs/tokens/cookies/handles are
  never proposed or copied into files.
- Retrieving a granted value does not trigger a second approval request, while
  browser typing/submission remains the consuming agent's decision.

## Risk and Rollback

- An overly broad description could invoke on unrelated tasks; narrow the
  frontmatter based on forward-test evidence.
- A stale reference could diverge from MCP schemas; compare exact tool names
  and required fields during the parent integration review.
- Rollback removes only the new repository skill files and any skill-specific
  tests; it must not delete saved user records.

