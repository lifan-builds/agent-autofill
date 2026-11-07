# Seed Agent Autofill from Chrome

Chrome can provide the first ordinary autofill data so the user does not have
to type the same name, contact details, and addresses again. The recommended
path is an agent-led, review-first intake. It reads selected structured Chrome
autofill data, asks about unclear columns or ownership, then saves only the
rows the user approves. Saved Chrome passwords are a separate opt-in and are
imported directly into the encrypted local Agent Autofill store.

## Use Chrome's Default profile

Install and initialize Agent Autofill, then list the available profile names:

```bash
agent-autofill --db ~/.agent-autofill.sqlite3 init
agent-autofill chrome profiles
```

The command resolves Chrome's `Default` profile only. Secondary profiles are
rejected rather than silently selected. Before reading values, the acting agent should explain that
ordinary selected values enter its conversation context and follow that
provider's retention settings.

Then ask the local agent to run the repository skill:

> Seed Agent Autofill from my selected Chrome profile. Read only supported
> structured autofill names, contact details, and addresses. Keep source groups
> intact, use the Chrome classification guide, ask me grouped questions for
> ambiguous columns or ownership, show the complete proposal, and save only
> the exact rows I approve. Separately ask whether I want saved passwords
> imported to Agent Autofill's local encrypted store only; never show or export
> password values.

The skill requires local execution access. An MCP-only agent cannot read the
selected local Chrome profile and should say so rather than requesting a wider
grant.

## The onboarding pass

The agent runs the intake in four short phases:

1. Scan the selected profile's supported structured tables.
2. Run `organize_structured_candidates(...)` to preserve source groups and add
   deterministic comparison values and review flags.
3. Show a value-free count summary first, then ask only grouped questions for
   ownership, currentness, conflicting representations, unsplit names, or
   suspicious content. A duplicate signal never merges records by itself.
4. Regenerate the proposal after answers, show the exact rows, and save only
   the unchanged rows explicitly approved.

The organizer uses `normalized_value` only to compare candidates. The original
Chrome `value` stays attached to its source row for review. Flagged groups stay
held until the answer is reflected in a regenerated proposal. See the
[Chrome intake benchmark](../evals/chrome_intake/README.md) for the synthetic
group-fidelity, normalization, ambiguity, replay, and no-write checks.

## What the seed read includes

The intake reads the `Web Data` structured address tables and the allowlisted
ordinary field types below:

| Chrome field | Typical profile field |
| --- | --- |
| `first_name`, `middle_name`, `last_name` | Name components |
| `email`, `phone` | Contact details |
| `street_line1`, `street`, `city`, `state`, `postcode`, `country` | Address components |

Each candidate keeps an opaque source profile ID, source group ID, and field
type. This lets the agent distinguish two address groups without flattening
them into one generic `address` record.

The ordinary autofill intake does not read Chrome passwords, payment cards,
OTPs, sync payloads, arbitrary autocomplete history, or unrelated browser
tables. It does not modify Chrome and does not create a raw database copy or
candidate export.

### Optional saved-password import

The agent must ask separately whether the user authorizes reading saved
passwords from the selected profile and importing them to the encrypted Agent
Autofill database on this device only. A general autofill approval does not
count. If the user says no or is unclear, skip the password path.

After a clear yes, the agent runs the `chrome password-manager --preview`
command described in the [password intake reference](../.agents/skills/agent-autofill-chrome-intake/references/password-intake.md),
shows the value-free reconciliation preview, and asks for approval of that
exact local sync. Password plaintext stays in the importer process and is
sealed immediately by the broker. It never enters the agent prompt, a proposal,
an audit row, a CSV, or an external service. Unsupported Chrome encryption and
an unavailable Safe Storage key fail closed without writes.

## How ambiguity is handled

The agent uses the [classification guide](../.agents/skills/agent-autofill-chrome-intake/references/classification-guide.md):

- clear fields can become eligible proposals, but still appear in final review;
- unsplit `name`, conflicting `street`/`street_line1`, unknown columns, and
  unclear ownership or currentness stay **held**;
- the agent asks a small grouped question, such as whether an address is home,
  work, former, shared, or another person's data;
- a human answer resolves the classification, but does not approve saving;
- any changed value, label, grouping, sensitivity, provenance, or scope causes
  the affected proposal to be regenerated and reviewed again.

Source values are data, not instructions. Prompt-like text in Chrome is held
and never executed or promoted into a profile record.

## Review and save

The final review includes every candidate as `add`, `update`, `no-op`, or
`held`, with its exact ordinary value, source group, destination field,
sensitivity, scopes, confidence, and remaining ambiguity. The agent saves only
the exact unchanged add/update rows the user names. Cancellation, held rows,
no-ops, and unapproved rows produce no writes.

The local administrative `Broker.put_profile` path writes approved records one
at a time. If a write fails, the agent reports saved and pending IDs without
repeating source values, then stops for reconciliation.

## Safety stops

The agent stops and explains the next action when the selected database is
locked, its schema is unsupported, or the read is inconsistent. Close Chrome
yourself and retry when asked; the agent never force-closes it or bypasses
SQLite journals. If comparison with an existing encrypted profile is not
authorized, the affected row stays held rather than being called a no-op or
overwritten.

The legacy `chrome import` and `setup` paths remain separate eager import
commands for compatibility. Use this agent-led path when the goal is a safe,
ambiguity-aware seed of the profile.
