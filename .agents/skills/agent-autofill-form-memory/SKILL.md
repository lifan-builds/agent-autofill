---
name: agent-autofill-form-memory
description: "Suggest and retrieve reusable Agent Autofill records while preparing or reviewing a form: semantically match stable user-provided or user-verified values to saved profile, credential, and financial metadata, present exact add/update/no-op proposals, and save only explicitly approved entries. Do not use for unrelated email search, account administration, or generic profile writing."
---

# Agent Autofill form memory

Use this skill when an agent is preparing or reviewing a form and has either:

- stable values the user supplied or explicitly verified and may want to reuse;
- a need to compare those values with Agent Autofill records; or
- an authenticated, scoped need for selected saved values for the consuming
  agent's own form workflow.

Agent Autofill is an information layer. The consuming agent supplies the form
labels, target origin, purpose, and values it obtained from the user or its
own permitted context. This skill does not read a screen, control a browser,
type into a form, or submit anything. Read
[record-contracts.md](references/record-contracts.md) before making a record
read or write call.

## Workflow

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
   weaken a missing grant or scope. Include the complete approved replacement
   entry for every update; an omitted credential field defaults empty and an
   omitted financial value is removed. Send only the accepted entry fields
   from `record-contracts.md`; proposal-only action, preview, confidence,
   ambiguity, and source-context columns do not belong in the MCP payload.

7. Report metadata only. Relay created/updated/unchanged statuses and record
   ids from the write result without repeating raw sensitive values.

## Retrieval boundary

Once transport authentication, the matching active read grant, and the
record's origin/purpose scope pass, selected reads do not need a second chat
approval. Use `request_profile_fields` for ordinary profile values,
`request_credential_fields` for selected credential fields, and
`request_financial_fields` for selected financial fields. Ask only for fields
the consuming agent needs.

Returned raw values are transient response data. Use them only for the
consuming agent's current workflow; do not echo, log, place them in a file or
skill artifact, copy them into provenance/source context, propose them again,
or retain them in saved records. The consuming agent owns any confirmation it
needs before typing or submitting a value in a browser.

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
