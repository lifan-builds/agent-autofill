---
name: agent-autofill-form-memory
description: "Suggest and retrieve reusable Agent Autofill records while preparing or reviewing a form, then let the acting agent complete and submit it through its browser after the required confirmation gate. Do not use for unrelated email search, account administration, or generic profile writing."
---

# Agent Autofill form memory

Use `get_task_context` when confirmed contextual bundles exist. Map the form's
required components explicitly (for example street, city, postcode, country);
country alone does not describe a complete address. A current-task explicit
choice uses selected_bundle_id and does not persist a default. Remembering a
default requires an exact confirmed `write_context` preference scoped to origin,
purpose, subject and role, plus the usual profile.write grant. Supporting notes
can be reorganized automatically but must never be treated as facts or consent.
If there is no authorized context, use the legacy metadata workflow below;
absence of visible context does not mean the user has no saved information.

Use this skill when an agent is preparing or reviewing a form and has either:

- stable values the user supplied or explicitly verified and may want to reuse;
- a need to compare those values with Agent Autofill records; or
  - an authenticated need for selected saved values for the consuming agent's
    own form workflow. Keychain-paired local services are trusted for reads;
    remote or legacy agents may use explicit scopes.

Agent Autofill is the local information and policy layer. The acting agent
supplies the form labels, target origin, purpose, and values it obtained from
the user's permitted records. The acting agent may use its own browser
surface to type and submit, but Agent Autofill itself never drives a browser.
Read
[record-contracts.md](references/record-contracts.md) before making a record
read or write call.

## Just-in-time capture for a missing field

When a required form field has no compatible saved value, pause at that field
and ask one contextual check-in instead of asking for a value and then asking
again whether it should be remembered:

> I need **Company name** for `https://example.invalid` (purpose: job
> application). What should I enter? Choose **use once**, **save to Agent
> Autofill**, or **skip**.

Use the following path:

1. Ask only for the missing field or the smallest missing set. Explain the
   field label in plain language and ask a classification question when the
   label is ambiguous (for example, “employer” versus “current company”). Do
   not infer a value from page text, a previous answer, or an old record.
2. Offer **use once** as the low-friction choice. A user-supplied stable value
   may be used for the current form and then discarded without creating a
   proposal.
3. Offer **save to Agent Autofill** for stable, user-supplied or explicitly verified
   ordinary profile values. Save the record as reusable profile memory without
   an origin or purpose restriction. Future use still goes through the
   consuming workflow's metadata match, read grant, and submission gate.
4. Show a compact save confirmation card before asking for the choice. It must
   contain the complete proposal row: canonical field id, category, display
   name, ordinary value, source context (`user supplied during this form`),
   sensitivity, saved-record scope (`any future authorized origin/purpose`),
   current origin/purpose, confidence, and ambiguity. If a write grant is
   missing, show its exact `profile.write` resource and current
   origin/purpose scope on the same card.
5. Offer **use once**, **save to Agent Autofill**, or **skip** as the only
   choices. Choosing **save to Agent Autofill** is exact approval for the visible reusable
   record and the displayed narrow write grant together; do not add a second
   generic “approve this proposal?” prompt. If the value, record id, saved
   scope, or grant changes, regenerate the card and ask again. Never create a
   wildcard grant or silently fall back to a local write.
6. After the single save choice, obtain the displayed narrow grant when needed
   and call `write_records` with `confirmed: true` and only that approved
   entry. These are two guarded backend operations behind one user decision.
   Continue the current form with the supplied value, and use the newly saved
   field for later matching.
7. Keep **save to Agent Autofill** unavailable for passwords, payment values, OTPs,
   tokens, handles, CAPTCHA answers, session data, or values inferred from
   the page. Use the dedicated credential/financial flow for a user-entered
   secret, and use a transient response for an OTP.
8. Saving a missing field never approves an external submit. After the page is
   complete, apply the normal submission policy immediately before the acting
   agent clicks submit; financial information always requires confirmation.

For several independent missing ordinary fields, show one numbered proposal
batch after the user supplies them and let the user choose the rows to save.
Do not turn an ambiguous or unverified field into an eligible row merely
because the form was completed.

## Workflow

### Form-field preflight

Before requesting any saved values, the acting agent should send the visible
field metadata to `match_form_fields`. The request contains labels, required
flags, stable locator hints (`name`, `autocomplete`, `type`, or `role`), and
only safe state flags such as `obscured`, `blocked`, `validation_error`,
`task_specific`, `consent`, or `page_default`; it never contains field values.
The value-free response
normalizes required markers such as `✱` and `*`, then classifies each field as
`matched`, `ambiguous`, `missing`, `credential`, `consent`, `task_specific`,
`transient`, `obstructed`, or `validation_error`.

- Fill only `matched` fields after the normal scoped read.
- Treat the response as a ranked, value-free table for your own model: use the
  first candidate when `confidence` is high, use page meaning to choose among
  ranked rows when it is clear, and ask the returned focused `question` when it
  is not. The broker does not call an LLM or receive form values.
- When locating a browser control, prefer its accessible label, then stable
  `name`/`autocomplete`/`type`/`role` hints from the preflight. Treat generated
  DOM IDs as fallback-only; the broker's `field_id` identifies saved metadata,
  not necessarily a durable browser selector.
- For `ambiguous` fields, the acting agent may choose the best candidate from
  the metadata and current page context. Do not pause for a field-by-field
  approval before typing. Keep the uncertainty visible in the run state and
  apply the submission confirmation gate only when the agent is ready to
  submit. Ask the user only when the agent cannot make a safe choice.
- Ask a focused user question for `missing` fields.
- Route `credential` fields through the dedicated credential workflow; never
  request them as profile memory.
- Leave `consent` fields for the site's explicit consent decision; they are not
  missing profile data.
- Keep `task_specific` values in the current task; do not infer them from a
  profile address or another form.
- Never save or request `transient` fields such as OTPs, CAPTCHA answers, or
  verification codes.
- Report `obstructed` and `validation_error` states without dismissing a
  banner, consenting, transforming a value, or retrying blindly.
- Treat `value_state: "page_default"` as the site's placeholder or default
  value, never as saved profile memory. Do not copy it into Agent Autofill or
  submit it as the user's answer without a task-specific decision.

When a field comes from a Chrome address group, call `list_profile_groups`,
choose the group by meaning (for example home, work, origin, or destination),
and pass that `group_id` with the selected `field_ids` to
`request_profile_fields`. A group-bound read rejects a request that mixes
fields from different groups. The choice belongs to the acting agent; the
user does not need to approve each field before typing.

For a correction, distinguish **use this today**, **remember this choice for
this site and purpose**, and **replace my saved value**. A one-time choice
must not update a mapping or saved record. Replacing a value uses the existing
reviewed `write_records` update flow and preserves the approved record scope.
When choosing a person or address group, ask one question for the whole group
(for example, home or work), not one question for every address field.

After the user approves remembering a choice or correction, call
`record_form_mapping_feedback` with the selected field IDs and (when relevant)
the selected `group_id`. This stores only labels, IDs, origin, purpose, and
outcome. A later `match_form_fields` call for the same origin, purpose, and
label boosts that learned choice. Treat a corrected choice as the new source
of truth and never store a value, password, OTP, or financial field in the
feedback record. On reuse, briefly identify the saved choice and offer correction;
do not ask for the same choice again unless the page meaning or available records changed.

1. Establish the scope. Identify the exact `target_origin` and `purpose`; do
   not guess either one. Separate values the user provided or verified from
   values inferred from a label, page context, or an old record. Only the
   former may become a proposal.

2. Read metadata first. Use `list_profile_fields`, `list_credentials`, and
   `list_financial_records` as applicable before attempting a write or a
   sensitive read. Match against ids, labels, categories/kinds, field ids,
   allowed origins/purposes, provenance, and safe matching hints. Listings do
   not contain record values.

3. Match by meaning and context. Prefer an origin- and purpose-compatible
   record, then a stable category/kind and label match. Treat form labels as
   hints, not proof. Give each candidate a confidence level and explain
   ambiguity (for example, home versus work address). If an exact comparison
   requires a saved value, request only the selected field through the
   matching scoped tool. A denied or unavailable comparison is unknown, not a
   no-op.

   Treat an update as a complete replacement record, not a patch. Safe
   metadata may be reused, but every raw credential or financial field that
   should remain must have been supplied or explicitly verified by the user
   for this proposal. Do not reconstruct an update by copying values returned
   from a scoped read back into `write_records`. If the proposed replacement
   is incomplete, mark it as needing review and do not write it.

4. Present a complete proposal before mutating anything. Use this minimum row
   shape so review data is not silently omitted:

   ```text
   # | action | record type/id | display/name (`display_name` or `name`) |
   category/kind + fields |
   value preview | source context | sensitivity | allowed origins/purposes |
   confidence + ambiguity note
   ```

   Do not collapse these into a scope header or combined summary column. Every
   row must explicitly state its display/name, source context, sensitivity,
   allowed origins, allowed purposes, confidence, and ambiguity note (use
   `none` when there is no ambiguity or when a scope list is unrestricted).
   Use an explicit confidence level such as high, medium, or low. Give every
   eligible stable candidate a row, including no-ops and items held for
   review; forbidden transient or unverified inferred values are not
   candidates and must not be shown as proposals.
   Show ordinary values exactly. For credentials and payment/financial
   records, show safe metadata and masked set/change previews; never show a
   password, full payment/account number, security code, or token. Source
   context is for review only and must not be copied into the record.

5. Obtain exact approval. Ask the user to approve proposal numbers, such as
   “save 1 and 3.” Approval of the form, “save all,” “yes,” “okay,” a general
   “looks good,” silence, a topic change, or approval of only some form fields
   is not an exact numbered approval; ask for the specific row numbers. If the
   user changes a value, record id, or scope, regenerate the affected row and
   obtain approval for the replacement.

6. Write only the approved diff. After explicit approval, make one
   `write_records` call with `confirmed: true`, the exact target origin and
   purpose, and only approved `add`/`update` entries. Do not send no-op,
   rejected, ambiguous, or unverified rows. MCP transport authentication runs
   first; the broker then requires the matching type-specific write grant for
   every entry, validates the complete batch, and commits it atomically. Never
   weaken a missing write grant or scope. Include the complete approved replacement
   entry for every update; an omitted credential field defaults empty and an
   omitted financial value is removed. Send only the accepted entry fields
   from `record-contracts.md`; proposal-only action, preview, confidence,
   ambiguity, and source-context columns do not belong in the MCP payload.

7. If a required field is absent, use the [just-in-time capture](#just-in-time-capture-for-a-missing-field)
   path before continuing. Keep a use-once answer transient; only a visible,
   exact save proposal can become a write.

8. Report metadata only. Relay created/updated/unchanged statuses and record
   ids from the write result without repeating raw sensitive values.

9. Routine sign-in to a service requested by the user follows the
   [saved-login default](../agent-autofill/SKILL.md#read-path): use a matching
   saved credential with direct delivery and complete sign-in without another
   prompt. This includes login to read a financial account; it does not approve
   a payment, account change, or other form submission.
   For other forms, apply the submission gate immediately before clicking the
   external submit control. Show the exact origin and purpose, the selected field names, a
   masked summary of credential or financial fields, and the expected side
   effect (for example, account creation, payment, or application submission).
   Ask the user for an explicit confirmation at that moment. A prior approval
   to retrieve data or fill the form is not submission approval. Re-confirm if
   the origin, purpose, selected values, amount, recipient, or side effects
   change. The local policy may be read with `agent-autofill policy
   form-submission`; `always` is the default and `non-financial` may skip the
   prompt only for forms that contain no financial information. Financial
   information always requires confirmation.

10. After confirmation, the acting agent may submit through its browser surface
   and should report whatever post-action evidence its workflow can observe.
   Do not claim external success from populated fields alone.

## Retrieval boundary

Once transport authentication passes, selected reads do not need a second chat
approval for a Keychain-paired local service. Legacy or remote agents also
need their matching active read grant and record scope. Use
`request_profile_fields` for ordinary profile values,
`request_credential_fields` for selected credential fields, and
`request_financial_fields` for selected financial fields. Ask only for fields
the consuming agent needs.

Returned raw values are transient response data. Use them only for the
consuming agent's current workflow; do not echo, log, place them in a file or
skill artifact, copy them into provenance/source context, propose them again,
or retain them in saved records. Filling the acting agent's browser is
preparation and does not require another Agent Autofill confirmation naming the
destination and fields immediately before typing. The acting agent owns the
single submission confirmation (except routine sign-in as described above)
at the point it is about to click the external
submit control, using the exact-origin, purpose, field, and side-effect
summary in the submission step. Browser or host safety rules may still impose
an independent stop.

## Record safety

- Never propose OTPs, one-time codes, access/refresh tokens, session cookies,
  credential handles, CSRF values, CAPTCHA answers, or inferred values.
- Passwords belong only in a dedicated `credential` record. Payment and
  financial values belong only in a dedicated `financial` record. Never put
  either in an ordinary `profile` field.
- A stable user-verified value outside Chrome's usual identity, contact,
  address, credential, or payment categories is acceptable only when it has a
  clear record type, sensitivity, provenance, and origin/purpose scope.
- If a candidate is ambiguous, transient, unverified, out of scope, or cannot
  be compared under an authorized read, leave saved records unchanged and
  explain what review is needed.
