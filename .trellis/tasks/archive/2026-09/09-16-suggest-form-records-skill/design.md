# Technical Design

## Skill Shape

Create a repository-local skill at:

```text
.agents/skills/agent-autofill-form-memory/
├── SKILL.md
└── references/
    └── record-contracts.md
```

The `SKILL.md` frontmatter name is `agent-autofill-form-memory`. Its description
targets agents preparing or reviewing form values that can reuse Agent Autofill
records or notice stable user-verified values worth saving. It excludes
unrelated email search, account administration, and generic profile writing so
implicit discovery remains precise.

The entrypoint stays instruction-first. `record-contracts.md` holds the exact
MCP tool inputs, record schemas, grant operations, forbidden transient fields,
and proposal format because those details are substantial and versioned with
the broker contract. No script is needed for semantic mapping; the LLM already
has the form context and field labels.

## Inputs and Boundary

The consuming agent supplies the current form labels, target origin, purpose,
and user-provided/verified values from its own browser or conversation context.
The skill does not read the screen or operate a browser. Agent Autofill supplies
saved metadata and authorized values only.

Retrieval calls require agent authentication and the matching active read
grant. They do not require fresh chat approval. If retrieval is denied, the
skill reports that the saved value is unavailable; it does not ask the broker
to weaken scope. The consuming agent decides whether typing or submission in a
browser needs user confirmation.

## Semantic Matching

Map form labels and context to saved record metadata using meaning rather than
a fixed browser-field-name map. Prefer exact origin/purpose-compatible records,
then stable category and label matches. Surface uncertainty instead of
guessing when multiple records could apply.

Supported categories include Chrome-style identity, contact, address,
credentials, and payment fields plus other stable user-verified values that
fit the profile, credential, or financial contracts. Do not infer identity,
medical, government, account, or financial values merely from context.

## Proposal Contract

After the user provides or verifies a stable value, compare it with available
saved records and prepare a proposal row with:

```text
proposal number, action (add/update/no-op), record type/id,
category or kind, field names, proposed value preview, source context,
sensitivity, allowed origins/purposes, confidence, ambiguity note
```

Show ordinary proposed values exactly. Mask passwords, full payment/account
numbers, security codes, and similarly sensitive values while still showing
the exact record, fields, scopes, and whether each sensitive field is being set
or changed. Source context is review-only and must not contain hidden page
state or be copied into the saved record.

Never propose OTPs, one-time codes, access/refresh tokens, cookies, sessions,
credential handles, CSRF values, CAPTCHA answers, or unverified inferences.
Route passwords to `credential` and payment/financial fields to `financial`;
never disguise them as profile values.

## Approval and Write Flow

1. Present the complete add/update/no-op proposal before any mutation.
2. Ask for explicit approval of proposal numbers. Silence, topic changes,
   partial answers, or general approval of the form itself are not approval to
   save records.
3. If the user edits a value or scope, regenerate the affected proposal before
   saving.
4. Send only approved add/update entries in one `write_records` call with
   `confirmed: true`, target origin, and purpose. Never send no-op or rejected
   rows.
5. Report the metadata-only created/updated/unchanged result. Do not repeat raw
   sensitive values.

The broker enforces authentication and write grants but cannot verify the chat
transcript; this ordering is a skill invariant.

## Skill Validation

Run `quick_validate.py`, then forward-test realistic prompts in an isolated
workspace:

- a normal address/contact form with one add, one update, and one no-op;
- ambiguous home/work addresses;
- a credential plus payment form with masked proposal previews;
- an OTP login where the OTP is retrieved but never suggested for storage;
- unrelated email work that should not invoke the skill;
- approval rejection and partial approval that must not over-save.

