# Contextual memory evaluation

Run the deterministic sequence:

```sh
PYTHONPATH=. uv run python evals/context_memory/run.py
```

After the user explicitly authorizes a snapshot of their own local profile:

```sh
PYTHONPATH=. uv run python evals/context_memory/run.py --real-profile-snapshot --output evals/context_memory/latest.json
```

The snapshot opens SQLite read-only and reads the existing database key through
Keychain without creation or migration. Only encrypted ordinary profile rows
enter an isolated in-memory store. No credentials, connectors, emails, Messages,
financial records or production grants are copied. Evaluation identities and
writes exist only inside that disposable store. The report contains counts and
statuses; no field values, private IDs, source filenames or task text.

## Measured funnel

| Stage | Synthetic task result | What it checks |
| --- | --- | --- |
| Intake | 12 facts, 3 bundles | Atomic facts and context; source groups retained |
| Retrieval | 2 current candidates, 1 historical excluded | No stale destination choice |
| Clarification | Required before the first ambiguous choice | Meaning is not guessed |
| Read | 4/4 correct components, 0 mixed bundles | Complete requested address |
| One-time feedback | 5 repetitions, 0 defaults created | Frequency does not manufacture preference |
| Confirmed reuse | 3/3 correct choices, 0 repeated questions | Explicit preference improves later tasks |
| Evidence change | Stale bundle and notes withheld | Changed facts require fresh review |
| Forget | 0 remaining dependent records | No resurrection through history |

The comparison is against the existing matcher without a saved mapping on the
same synthetic fields: it remains ambiguous in all three repeat tasks. This
demonstrates the contextual workflow, not algorithm superiority over every
possible legacy mapping. These small deterministic counts are regression gates,
not estimates of real user conversion or LLM task success.

## Real-data replay, October 28, 2026

The authorized snapshot contained **120 ordinary profile fields in 19 source
groups**. Eighteen groups had recognizable semantic components; one needs
classification. Scoped reads preserved all 119 recognized values exactly and
kept their original source membership. All 120 source rows remained unchanged.

Six task replays covered delivery addresses, professional contact and telephone
contact across the available scope combinations. Three asked for context, two
reported missing required components, and one had no authorized contextual
candidate. None silently selected an identity or default. Shadow groups used
`unknown` for ownership, role and currentness; they are evaluation fixtures,
not confirmed user relationships, and were discarded.

This exposes the next onboarding need: review the meanings of existing source
groups once, then explicitly confirm useful conditional defaults. We have not
classified personal addresses as home/work on the user's behalf. These are
real-data no-submit task replays, not live website completions.

## Release checks

```sh
uv run --with pytest pytest -q
uv run --with ruff ruff check agent_autofill tests/agent_autofill evals/context_memory/run.py
```

Focused tests cover exact approvals, atomic rollback, stale revisions across
connections, grant revocation, inaccessible notes, note injection, expiry,
partial addresses, bounded ambiguous results, legacy mapping isolation,
bundle-bound reads, note restoration, evidence invalidation and cascading
forget. Re-run the funnel after changing any retrieval or learning rule; a
silent default, wrong identity, unauthorized disclosure or production mutation
blocks release.
