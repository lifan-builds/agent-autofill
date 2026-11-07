# Try the profile review

## Start in the agent conversation

The preferred trial now runs in the host agent's conversation. The agent
compares the saved groups, proposes useful meanings and asks one consequential
question at a time. The page below is an optional inspection surface.

Ask the agent: **“Help make my saved details useful for deliveries. Compare
what's there and ask only what you can't work out. Use the existing trial.”**

The local development commands are:

```sh
uv run agent-autofill review-agent --sample --task delivery
uv run agent-autofill review-agent --resume /absolute/path/to/trial.sqlite3 --task delivery
```

`--profile-snapshot` creates a new encrypted ordinary-profile trial when one
doesn't exist. Inspection returns ordinary values to the authorized host
agent, so the host's conversation/model retention applies. No new model
provider or network client is added to the broker.

The host reasons about the data and prepares an exact meaning proposal with
`--propose` (JSON on stdin). It can ask a combined question: “Is this your
current home, and may I remember that meaning for this group in the trial?”
One direct affirmative answer suffices. `--confirm PROPOSAL_ID --approved`
saves only that frozen proposal, checking source and meaning revisions again.
The approval flag is the host's assertion, not proof of human consent.
Corrections need a revised proposal; use once or uncertainty saves nothing.

Matching names and addresses are comparison evidence, not proof of identity.
The agent leaves unrelated records unresolved and does not automatically merge
groups or turn a frequently used address into a default. Meaning proposals
are encrypted in the trial. Live records, values, defaults and permissions are
unchanged. The synthetic command is inspect-only across separate CLI calls;
real trial proposals can survive process restarts.

See the [host workflow](../.agents/skills/agent-autofill/references/conversation-onboarding.md).

## Optional page

The review helps you label saved groups without filling out a table of fields.
It is a separate owner-operated surface; the Agent Inbox stays read-only.

From the development checkout:

```sh
uv run agent-autofill review --sample
uv run agent-autofill review --profile-snapshot
```

The first command uses synthetic details. The second explicitly reads only
ordinary profile records from the installed vault into a new encrypted trial.
It does not copy passwords, financial records, email connections or messages.
The original vault is opened read-only. It is never the destination for review
writes. Trial keys stay in the OS credential store.

Open the local URL printed by the command. It contains a temporary browser
capability: keep it private, do not paste it into issue reports. The browser
removes it from the address bar and retains it in port-scoped session storage.
Only the local review server receives it. There are no external resources,
analytics or model calls in the page.

1. Choose delivery, contact details, or all groups.
2. Recognize the saved values and choose a meaning. Quick choices cover your
   current home, current work and historical addresses. Each answer remains
   editable, and “Not sure yet” is valid.
3. Alternatively, expand the batch input and type supported descriptions such
   as “A is my current home; C is my current office; D is my old address.”
   This is a small local parser, not general natural-language understanding.
   Unsupported or contradictory batches produce no draft changes.
4. Select **Review changes**. The confirmation shows the recognizable values,
   each old/new meaning and how many fields belong to that group.
5. Select **Save meanings to trial** once. All selected meanings commit
   together. No fact values or preferences are changed. Reopen reviewed groups
   to edit a meaning again. Leave the rest for later.

Real-data trial saves persist in the encrypted trial file printed at startup.
Resume that file with:

```sh
uv run agent-autofill review --resume /absolute/path/to/trial.sqlite3
```

Resume validates an encrypted trial marker before opening a file for writing;
passing a live vault path is rejected. The synthetic sample is memory-only.
Drafts are temporary until confirmed; the page warns before leaving unsaved
drafts. There is no apply-to-live button in this first version.

## Meanings and permissions

Confirmed onboarding bundles use `shared: true`: their **meaning** can be reused
across tasks inside the private vault. This does not share the data with another
person or website. Every context retrieval and field read still checks access
to all referenced facts. Documents and preferences keep their exact task scope.
A current home address is not automatically a billing or tax default.

Someone else's records receive a separate subject ID for each source group.
Matching names or addresses never merge people automatically. Conflicting field
meanings or incompatible scopes are held for closer review. A changed profile
value invalidates the old review; it can be reconfirmed after seeing the current
data. Revision checks bind confirmation to the displayed facts and prior meaning,
including changes racing with the save operation.

The trial creates only internal, short-lived write grants needed for the exact
selected groups. It never adds an agent or grant to the live vault. The browser
cannot submit arbitrary profile writes or preference changes through this API.
Loopback binding, exact Host/Origin checks, a per-process capability, no-store
responses, a restrictive CSP and text-only value rendering protect the review.

## Verification

```sh
uv run --with pytest pytest -q
node --test tests/agent_autofill/review.test.cjs
uv run --with ruff ruff check agent_autofill tests/agent_autofill
```

Browser verification exercised batch drafting, exact review, one save for three
groups, persisted review status after reload and the compact layout. Tests cover
snapshot isolation, encrypted persistence/resume, rejection of live files,
unauthorized requests, origin and Host checks, atomic failure, stale previews,
cross-task meaning reuse without permission widening, scoped preferences and
safe parsing. No real meanings have been guessed or confirmed on the user's behalf.
