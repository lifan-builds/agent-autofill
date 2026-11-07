# Contextual memory for Agent Autofill

Status: proposed design, 2026-10-28. Organization policy confirmed by the user:
**Auto-organize; confirm facts and defaults.** Implementation has not started.

## Outcome

An agent should find an appropriate record, understand why it applies, obtain
only the permitted fields, and leave the memory easier to use next time.
The next agent should inherit useful context without needing the previous
conversation or a dump of the user's information.

Keep structured values as the source of truth. Add small, agent-maintained
context documents that reference those values, plus explicit preferences and
evidence. Store all of them in the existing encrypted local database. Render
documents as readable cards or Markdown at the interface; do not create a
second, plaintext personal-information store.

## What the existing implementation provides

This design targets the development checkout, which includes changes beyond
public main. Public main's media update does not publish those changes.

- `agent_autofill/service.py`: profile fields have a category, label, value,
  provenance, access scopes, and update time. `list_profile_groups` reconstructs
  groups from Chrome source provenance. These identify source bundles, not
  necessarily a person, current address, or preferred use.
- `record_form_mapping_feedback` remembers selected field IDs and an optional
  group for an exact origin, purpose, and normalized label. It does not retain
  a structured reason, exception, validity period, or evidence history.
- `agent_autofill/form_memory.py`: deterministic metadata matching helps an
  acting agent choose. It does not run a language model.
- `.agents/skills/agent-autofill-chrome-intake/references/local-intake.md`:
  intake preserves source groups and flags conflicts. Its organizer does not
  merge identities. Confirmed ownership/currentness answers guide proposals,
  but the broker lacks a first-class contextual document for those answers.
- `agent_autofill/storage.py`: encrypted JSON records and transactional batch
  writes already exist. The current transaction primitive does not itself
  provide expected-revision checks for competing agent updates.

## Memory model

| Part | Contents | Authority |
| --- | --- | --- |
| Facts and bundles | Existing field values; stable bundle ID; confirmed subject and role; current, historical, or unresolved state; provenance and validity dates where known | Approved facts. Source imports remain source observations until reviewed. |
| Context document | Short summary, applicable situations, exceptions, unresolved questions, linked bundle IDs, and supporting evidence references | Agent-authored explanation. Each assertion is marked confirmed, derived, or tentative. It cannot create a default or grant access. |
| Preferences and observations | User-approved conditional choices, one-time choices, corrections, and compact evidence of actual usage | Preferences require confirmation. Observations describe what happened and cannot promote themselves into facts. |

These are a few record types in encrypted SQLite, not separate infrastructure.
Documents use a small stable envelope (`id`, `bundle_refs`, `revision`,
`evidence_refs`, `authority`, `updated_at`) with flexible body sections. Agents
may add useful sections rather than forcing every domain into a large schema.
The broker controls the envelope, authorization, revisions, and reference
integrity; the consuming agent writes and interprets the explanatory text.

### Example context document

All identifiers and details in this example are synthetic. Actual values stay
behind field references.

```text
Home address — bundle address_home

Confirmed meaning
  Belongs to person_self. Current home address.
  Evidence: user confirmation event_17.

Approved use
  Preferred for personal deliveries.
  Preference: pref_personal_delivery, confirmed in event_18.

Exceptions and unresolved questions
  Billing-address suitability is unknown.
  Do not infer tax residency from delivery use.

Related information
  address_previous: historical address, replaced by address_home.
  That relationship was confirmed in event_17.

Available fields
  street → field_81; city → field_82; postcode → field_83

Recent evidence
  User chose this bundle for a personal delivery in event_22.
  This records the choice, not proof that a parcel was delivered.
```

The agent can improve wording and discover a useful question such as billing
suitability. It cannot fill that knowledge gap by declaring the answer true.
Facts, preferences, and permissions remain distinct even when the document
displays them together.

### Let document structure follow useful tasks

Documents do not need a permanent folder hierarchy. Start with a few useful
subjects or bundles, then let agents create task-oriented views such as
"Professional contact details" or "Personal deliveries" that reference the
same underlying facts. A field can appear in several views without copying
its value or changing ownership.

An agent may split an overlong document, combine redundant derived summaries,
add an alias, link related documents through confirmed relationships, or move
an unresolved question into a clearer section. Preserve stable references and
evidence through these edits. Combining explanatory documents does not merge
their people or addresses. Conflicting confirmed facts remain visible rather
than being smoothed into a convenient narrative.

Store a short searchable description for each document. Retrieve relevant
documents progressively instead of injecting an entire personal notebook into
every task. A failed lookup should improve an alias or organization only when
supported by the existing evidence; it must not fabricate a new fact. The
success measure is better retrieval on the next task, not longer notes.

## What can change automatically

Under the user's chosen policy, automatic changes are reversible derived
organization: search aliases, views over existing confirmed relationships,
summaries of confirmed facts, and compact descriptions of observed choices.
Every derived assertion cites its input revisions. Tentative explanations may
be retained for review but are excluded from authoritative selection.

Changing address membership or subject ownership is a semantic change, even
if presented as "regrouping." A display-only grouping may be automatic;
merging identities, moving fields into a different address bundle, replacing
values, setting currentness, changing an exception, or creating a default needs
confirmation. Sensitive access rules always use the existing authorization
path and cannot be changed through a context document.

Agents must not smuggle a default into prose such as "always use this one."
Authoritative use conditions must reference an approved preference record.
Generated prose alone cannot authorize a value read or establish identity,
currentness, completeness, or a preferred selection.

## Chrome intake

1. Extract the supported structured fields into the existing source groups.
   Keep their source identifiers and original values intact.
2. Compare with existing bundles. Propose possible matches without merging
   people or flattening address components. Equal values are evidence of
   similarity, not identity.
3. Generate a brief context-document proposal for each meaningful bundle.
   Highlight unresolved ownership, currentness, and use, instead of asking
   the user to classify every raw field.
4. Ask one grouped question when needed: "Which is home, work, or an old
   address? Should any be preferred for personal delivery?"
5. Show the exact resulting facts, relationships, and any proposed defaults
   in one review. Apply the approved subset atomically with its context and
   source links. Resolving ambiguity does not silently approve unrelated rows.
6. On later imports, update source observations and propose conflicting fact
   changes. Preserve confirmed context, stable IDs, preferences, and history.
   A recently observed Chrome row does not overwrite a confirmed current fact.

If an export omits an old row, do not conclude the user no longer owns or uses
it. The omission may reflect an incomplete export or a different profile.

## Capture during a task

Keep the existing three choices: **use once**, **remember for this situation**,
and **update my saved information**.

For example, "send this order to work" selects a record for this task. It does
not create a global shipping default. If the user chooses to remember it, show
the proposed scope: "Use work for deliveries on this shop" or "Use work for
all personal deliveries." Those are different preferences.

Capture a compact evidence item only through an authorized memory workflow:
which bundle was selected, relevant non-secret context, whether the user
corrected it, and the resulting approved update. Do not retain entire chats,
page dumps, mailbox bodies, passwords, OTPs, or form submissions as memory.
An approved one-time use must not become a persistent preference or saved
value. Usage evidence must not retain a transient value either.

After the task, the agent may refresh the linked document from the evidence.
Repeated usage can surface a suggestion to confirm a default, but cannot create
that default. Re-reading an agent's own summary is not new evidence.

## Retrieval for the next task

Add a bounded `get_task_context` operation alongside the existing field APIs.
Input includes the authenticated identity, target origin, purpose, relevant
subject or role when known, requested field semantics, and a short task summary.
The summary is transient and must not contain secrets or a page dump.

The broker applies access rules before returning context, then returns a small
set of bundle summaries, relevant approved preferences, evidence pointers,
conflicts, and missing facts. Free-text documents may contain sensitive context:
they are protected reads, not automatically public "metadata."

The consuming agent uses that packet to select a bundle or ask a focused
question. It then calls the existing scoped field-read operation. For composite
records, all requested address components must belong to the selected bundle;
a country or postcode alone does not establish a complete address.

Selection order:

1. Apply authorization, subject, validity, and explicit incompatibility checks.
   A requested but prohibited record produces a denial, not a fallback.
2. Respect the user's explicit choice for this task.
3. Consider an applicable user-approved contextual preference. An origin-specific
   preference is more specific than a broader one when their contexts agree.
4. Consider compatible confirmed facts and agent reasoning over the available
   context. Use histories and generated descriptions as supporting evidence,
   never as authority for a new default.
5. Ask when meanings, ownership, or approved preferences conflict. Do not use
   frequency or recency alone to break a meaningful conflict.

The result should explain "why this record" and distinguish **missing fact**,
**unclear context**, **conflicting candidates**, and **access denied**. This
prevents asking users to re-enter information merely because selection or
authorization is unresolved.

Context conditions initially cover subject, record role, origin, purpose, and
optional validity dates. Avoid an arbitrary executable policy language. Store
the user's original preference wording alongside those conditions. If the
agent cannot map the current task to a condition confidently, it asks rather
than expanding the condition's meaning.

## Controlled evolution

Add versioned, typed memory updates: automatic derived-document edits and
proposals for semantic changes. Extend the existing reviewed-write flow rather
than introducing unrestricted agent access to the database.

Each update supplies an expected revision and evidence references. Validate
references and authorization, compare revisions, and commit records plus
metadata-only audit entries within one transaction. Reject stale revisions so
one agent cannot overwrite another agent's newer correction.

Summaries are rebuildable views. Store the previous document revision and the
changed references for undo; do not accumulate full conversation histories.
When a fact changes, invalidate dependent summaries and reconsider affected
preferences. Do not silently redirect an approved preference to a new identity.

Forgetting a record must remove or invalidate its derived notes, observations,
indexes, and revision history so it cannot reappear through summarization.
Undo retention must not defeat an explicit forget action. Database identifiers
remain opaque; human-readable context belongs in encrypted payloads.

Imported text and notes are untrusted data. They cannot request tool execution,
relax scopes, or override user instructions. For credentials and financial
records, the first version uses only approved non-secret labels and opaque
references; it does not generate free-form notes from secret values. Ordinary
personal context is also encrypted and scoped. Do not rely on redaction alone
to make an arbitrary generated document safe.

## User experience

The agent presents useful explanations in the task: "Using your confirmed
personal-delivery address" or "Two saved addresses fit; which one is for this
delivery?" It does not expose database organization to the user.

The local Inbox can show read-only cards for people and bundles, each with
purpose, currentness, evidence, unresolved questions, and revision history.
Keep the table as a detail view. Derived organization updates can be summarized
quietly; facts and default changes appear as a compact reviewable diff in the
existing agent confirmation workflow.

## First implementation batch

1. Introduce stable semantic bundles and encrypted context documents that
   reference existing fields. Preserve legacy IDs and source grouping.
2. Extend Chrome review to persist confirmed subject, role, and currentness
   with evidence. Add transactional revision checks before multi-record updates.
3. Add context retrieval, explanations, and conditional preferences. Convert
   existing form mappings into narrow preferences without broadening their
   origin/purpose scope; preserve references to legacy groups.
4. Add task feedback and revisioned document maintenance under the confirmed
   autonomy policy. Keep automatic changes derived and reversible.
5. Add contextual cards to the Inbox and a synthetic sequential-task evaluation.

This first batch covers ordinary profile details, addresses, and contact
choices. It should not reorganize raw email archives, infer sensitive life
facts, or replace credential/payment storage. New typed objects can be added
later when real workflows need them.

Use the existing encrypted SQLite store, MCP/CLI transport, and consuming
agent. Begin with local lexical/structured retrieval over decrypted authorized
records in memory. Avoid plaintext full-text indexes over encrypted context.
Introduce embeddings or a graph engine only if measured retrieval failures
justify them; neither is required for the first batch.

## Acceptance cases

| Scenario | Required outcome |
| --- | --- |
| Chrome contains home, work, and old address groups | Source groups remain intact; confirmed roles persist; old data does not become current by recency. |
| Two people share an address or phone | No automatic identity merge or transfer of field ownership. |
| User chooses work for one delivery | This task uses work; the next unrelated delivery has no new default. |
| User confirms a personal-delivery default | A later applicable task can reuse it with a brief explanation; billing and tax uses remain unresolved. |
| Only a country or postcode is saved | Retrieval reports incomplete address information and never labels it a complete shipping address. |
| A new import conflicts with a confirmed address | Keep the confirmed fact; propose the source change; do not erase the context document. |
| Task has a missing fact versus an ambiguous choice | Ask for the missing detail or the choice, respectively, without redundant save questions. |
| Generated note contains instructions or an unsupported default | It gains no authority, grant, or automatic preference; surface unsupported claims for review. |
| Two agents update the same document | A stale write is rejected; the approved correction is not lost. |
| User revokes access or forgets a record | Context and value reads respect revocation; derived notes and history cannot resurrect forgotten information. |
| Legacy client reads a field | Existing field APIs and narrow grants still work. |
| Same workflow is replayed after a confirmed correction | Selection improves or a meaningful conflict is surfaced; repetition never manufactures confirmation. |

Measure relevant-bundle retrieval, wrong-person/role selection, address
completeness, unnecessary clarification count, repeated corrections, and
context size across task sequences. Compare with the current matcher on the
same synthetic fixtures. Authorization leaks, identity merges without approval,
and unapproved default changes are release-blocking failures; reduced question
counts alone are not success.
# Implementation checkpoint — October 28, 2026

The first exact-origin/purpose implementation is available in the development
checkout. See [the implementation guide](contextual-memory.md) and
[the staged evaluation](../evals/context_memory/README.md). Confirmed bundles,
conditional preferences, revisioned supporting documents, one-time observations,
atomic contextual intake, scoped retrieval, bound reads, stale-evidence review
and cascading forget are implemented. Cross-site defaults remain outside this
first batch. The installed/global broker has not been upgraded by this work.
