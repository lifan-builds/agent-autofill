# Chrome agentic intake execution plan

Final planning summary approved on 2026-09-19; implementation and synthetic
verification are complete. See `verification.md`. Changes remain uncommitted.

1. Load `trellis-before-dev`, applicable backend read/write safety specs, and
   `skill-creator`. Preserve all prior uncommitted first-run work.
2. Author the focused repository-local intake skill and its local recipe
   reference. Keep the host agent responsible for reasoning; do not modify
   production Python modules or add a model runtime/tool service.
3. Validate the exact extraction/save recipes with synthetic temporary data.
   Cover consistency/lock/schema handling, field allowlisting, grouping,
   provenance, value-free diagnostics, and per-record save failure reporting.
   Use Python API input rather than shell arguments for approved values. Keep
   tests behavioral; do not add brittle assertions for headings or phrases.
4. Update setup docs and the continuation guide to route agentic cold-start
   requests to this skill and distinguish it from eager raw import commands.
5. Run an independent Luna Max forward-test under `skill-creator` instructions
   using isolated synthetic fixtures and explicit no-live-I/O boundaries. Test
   cancellation, partial approval, changed proposals, ambiguous owners, repeat
   intake, and prompt-like source content. Evaluate observed actions, not a
   reviewer's claim that the text looks safe. Preserve reports without private
   values and correct only demonstrated issues.
6. Run skill validation and relevant regression checks, then `trellis-check`.
   Record recipe results separately from agent-behavior evidence. No automatic
   commit, push, live intake, or prior-task archival.

## Expected files

- New `.agents/skills/agent-autofill-chrome-intake/SKILL.md` and
  `references/local-intake.md`.
- A focused synthetic test module only if needed to execute the exact recipes
  repeatably; no production helper module or packaged extraction script.
- `docs/agent-autofill.md`, `NEXT_STEPS.md`, and a concise relevant backend spec
  note distinguishing instructional approval from runtime enforcement.
- Task-local verification/forward-test reports containing synthetic evidence
  only. Do not create example files containing real Chrome values.

## Checks

```sh
python /Users/lfan/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/agent-autofill-chrome-intake
PYTHONPATH=. python -m pytest -q
ruff check agent_autofill tests/agent_autofill
python -m mypy agent_autofill --ignore-missing-imports
git diff --check
python3 .trellis/scripts/task.py validate .trellis/tasks/09-19-chrome-agentic-intake
```

Independent forward-testing is required in addition to these checks. If a
safe complete path cannot be delivered using instructions and existing local
APIs, report that concrete limitation and return for scope review rather than
adding new broker machinery silently.
