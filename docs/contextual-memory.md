# Contextual memory

Agent Autofill keeps ordinary facts in the encrypted profile store. Contextual
records reference those facts and explain how to use them. The acting agent can
organize supporting notes automatically; changing facts, identity relationships
or defaults still requires an exact user confirmation and scoped write grant.

## Current implementation

| Record | Content | Permission to change |
| --- | --- | --- |
| Bundle | Semantic field IDs, subject, role, currentness, title | Confirmed proposal and profile.write access |
| Document | Flexible supporting prose, bundle references, evidence versions | Read access to every referenced ordinary fact; scope stays fixed |
| Preference | One selected bundle for the exact origin, purpose, subject and role; optional expiry | Confirmed proposal and profile.write access |
| Observation | Used once, accepted, corrected or rejected; referenced bundles | Read access; never changes selection authority |

Context records and document history use the existing encrypted SQLite store.
Titles and note bodies are private context, not public metadata. Retrieval
checks all referenced facts before returning any of them. The Inbox shows
counts, revisions and changed-evidence indicators without note bodies.

Documents and preferences use exact origin/purpose conditions. Confirmed bundle
meanings may now opt into `shared: true` for reuse across authorized tasks; this
does not change field permissions or create a default. The local
[profile review trial](profile-review.md) uses this mode. It does not merge
identities, infer currentness from Chrome timestamps, or learn authority from
repeated usage. Existing form
mappings are consulted only when `field_label` is supplied and matches their
exact original origin, purpose, normalized label and field set; no broad
migration is performed.

## Agent workflow

1. During intake, show the exact proposed facts and bundle meaning. Unresolved
   subject, role and currentness stay `unknown`. Use `write_context` with
   `kind: bundle`, `confirmed: true`, and the approved `profile_entries` to
   commit facts, provenance and context together. Every updated fact needs its
   exact previous timestamp; bundle edits need the previous revision.
2. Call `get_task_context` with the exact origin/purpose, known subject and
   role, and explicit required semantic components. Use the bundle's spelling
   consistently: for example `street`, `city`, `postcode`, `country`. A short
   optional `task_summary` ranks relevant notes but is not persisted and cannot
   establish a default.
3. A `ready` response identifies the selected bundle. Read its requested field
   IDs through `request_profile_fields`, passing `bundle_id` to prevent mixing
   or reading against changed evidence. Browser interaction remains with the
   acting agent.
4. Ask one focused question for `needs_clarification`, `missing_information`,
   or `needs_review`. `no_authorized_context` does not distinguish absent data
   from inaccessible data; use the existing metadata matcher for unorganized
   records, without treating that absence as permission to invent a value.
5. Offer use once, remember for this situation, or update saved information.
   Use once may record an observation. A preference requires confirmation.
   Notes may explain already-confirmed relationships but are always returned
   as untrusted supporting context, never instructions or facts.

Every contextual mutation requires `expected_revision` (zero for creation).
The store checks prior records under a SQLite write lock, including referenced
facts and bundles. A stale proposal cannot silently overwrite a newer one.
Documents retain encrypted revisions; restore with `restore_revision` and the
current expected revision. Restore fails if the evidence changed. Deleting a
profile fact atomically purges dependent bundles, notes, history, observations,
preferences and legacy mappings. Updating evidence hides stale context and
asks for review rather than quietly switching to another default.

Documents must not contain passwords, financial values, mailbox bodies,
tokens, OTPs or copied conversations. Automatic organization is an agent
workflow over the tools, not an autonomous background model in the broker.

## Evaluation and rollout

See [the progressive evaluation](../evals/context_memory/README.md) for the
intake → retrieval → clarification → scoped read → feedback → reuse → invalidation
funnel, regression checks and isolated real-data replay. This implementation is
in the development checkout. The installed/global broker has not been replaced.
