# Agent Autofill record contracts

This reference mirrors the record-related MCP catalog in
`agent_autofill/server.py` and the validation in `agent_autofill/service.py`.
Use these names and inputs exactly; there is no generic profile-write or
password-read alias.

## MCP calls

The transport removes optional `agent_id` and `agent_token` dispatch fields
before invoking the broker. `agent_id` is optional in the advertised schemas
(the server defaults it to `local-agent`); `agent_token` is accepted by the
transport but is not an operation input in the catalog. Every tool below
except `list_profile_fields` requires successful agent authentication.
Authentication does not replace the broker's operation,
record, origin, purpose, expiry, or revocation checks.

| Tool | Required operation inputs | Optional operation inputs | Use |
| --- | --- | --- | --- |
| `list_profile_fields` | none | none | Metadata only; values are omitted. |
| `list_credentials` | none | none | Authenticated credential metadata; secrets are omitted. |
| `list_financial_records` | none | none | Authenticated payment/financial metadata; values are omitted. |
| `match_form_fields` | `fields` | `limit` | Value-free ranked record table with matched/missing/ambiguous/credential/consent/transient/task-specific state. |
| `record_form_mapping_feedback` | `target_origin`, `purpose`, `field_label`, `selected_field_ids`, `confirmed` | `group_id`, `outcome` | Store a value-free mapping choice after a user-confirmed outcome so later matching can prefer it. |
| `request_profile_fields` | `field_ids`, `target_origin`, `purpose` | `group_id` | Read selected profile values under `profile.read` grants; group-bound reads cannot mix address groups. |
| `request_credential_fields` | `credential_id`, `field_ids`, `target_origin`, `purpose` | none | Read selected credential fields under `credential.read`. |
| `request_financial_fields` | `record_id`, `field_ids`, `target_origin`, `purpose` | none | Read selected financial fields under `financial.read`. |
| `write_records` | `target_origin`, `purpose`, `confirmed`, `entries` | none | Persist one explicitly approved batch. `confirmed` must be literal `true`. |

The MCP catalog declares `agent_id` as an optional string property on each of
these tools. The three `request_*_fields` schemas use an array of strings for
`field_ids`; `write_records` uses an array of objects for `entries`. The broker
limits field and entry collections to 1 through 100 items and rejects
duplicates.

`match_form_fields` may also receive `target_origin` and `purpose` to apply a
previously learned mapping. Each field may include only value-free browser
metadata (`field_id`, `name`, `autocomplete`, `type`, and `role`) as locator
hints. It may also include the safe booleans `task_specific`, `consent`, and
`page_default`. Explicit hints take precedence over ordinary label heuristics
after credential, transient, obstruction, and validation checks. A
`page_default` field gets `value_state: "page_default"`; this describes the
site's visible default and is never a saved profile value.

Each result includes a value-free `candidate_rows` table (also available as
`candidates`) with rank, score, stable field metadata, and Chrome `group_id`
metadata. `confidence` and `llm_guidance` tell the consuming model what to do:
use the top row for a strong match, choose a row or ask the focused `question`
when candidates are tied, or ask for a missing value. The broker does not call
an external model; the acting agent supplies page context and judgment.
An `ambiguous` result still includes `decision: agent_judgment` and the
submission boundary. After a confirmed result or user correction,
`record_form_mapping_feedback` persists the selected field IDs and outcome.
That feedback is value-free and makes the next table rank the learned choice
first. It never accepts profile values or secrets.

The following existing catalog operation is intentionally not a record-write
path:

| Tool | Required inputs | Optional input | Boundary |
| --- | --- | --- | --- |
| `request_auth_code` | `account_id` | `target_origin`, `purpose`, `max_age_seconds`, `origin_policy` | Returns one recent OTP from the explicitly authorized mailbox under `email.otp`; `origin_policy` is `mailbox-read` and an OTP is transient and never becomes a proposal. |

`request_credential_use` also exists for the compatibility handle flow with
required `credential_id`, `target_origin`, and `purpose`, plus optional
`expires_in`. It returns a short-lived handle rather than a password. This
skill uses the field-selective credential read above when raw fields are
actually needed; a handle is transient and must never be saved, echoed, or
placed in a proposal.

## Record namespaces and grants

`write_records` entries have one of these exact `record_type` values:

| Record type | Write grant | Read grant | Canonical id |
| --- | --- | --- | --- |
| `profile` | `profile.write` | `profile.read` | `field_id` |
| `credential` | `credential.write` | `credential.read` | `credential_id` |
| `financial` | `financial.write` | `financial.read` | `record_id` |

The entry's `record_id` is accepted for all types. `field_id` is an alias for
profile entries and `credential_id` is an alias for credential entries; if
both an alias and `record_id` are present they must identify the same record.
The broker requires an authenticated agent. Keychain-paired local services
receive trusted read access without a second grant ceremony; legacy and remote
agents require an active grant for the exact operation, record id (or wildcard
resource), target origin, and purpose. Writes always require their type-specific
grant. Record-level `allowed_origins` and `allowed_purposes` continue to apply
to legacy/remote reads and writes. A missing, expired, revoked,
wrong-resource, wrong-origin, or wrong-purpose grant denies a grant-based
operation without returning a raw value or writing a record.

## Entry schemas

These are the accepted `entries` keys. Fields marked optional use the broker's
defaults; `record_type` and the appropriate id are always required.

### Profile entry

```text
{
  "record_type": "profile",
  "record_id": "...",              # or "field_id"
  "category": "...",
  "display_name": "...",
  "value": <scalar or short scalar list>,
  "sensitivity": "normal",         # optional
  "allowed_origins": ["..."],       # optional
  "allowed_purposes": ["..."],      # optional
  "provenance": { ... }              # optional metadata object
}
```

`category`, `display_name`, and `value` are required. Profile values may be a
string, number, boolean, null, or short list of those scalar types. Profile
categories cannot carry credential, financial, payment, password, token,
cookie, session, handle, or OTP data. `updated_at` is assigned by the broker;
do not submit it.

### Credential entry

```text
{
  "record_type": "credential",
  "record_id": "...",              # or "credential_id"
  "name": "...",
  "origin": "https://example.invalid",
  "username": "...",               # optional; defaults to empty string
  "password": "<user-provided password>",
  "note": "...",                   # optional; defaults to empty string
  "sensitivity": "credential",     # optional; must be credential
  "allowed_origins": ["..."],       # optional; defaults to normalized origin
  "allowed_purposes": ["..."],      # optional
  "provenance": { ... }              # optional metadata object
}
```

`name`, `origin`, and `password` are required. `username` and `note` are
optional. Keep password values out of proposal text, provenance, logs, and
diagnostics; the approved MCP payload is the only place this workflow needs
to send one. A credential's `origin` identifies the site record, while the
record and grant scopes still govern each read or write.

The fixed selectable credential field ids are exactly:

```text
name, origin, username, password, note
```

`request_profile_fields` returns each requested field id as a `{value,
provenance}` object. The credential and financial field-read tools return only
the requested fields plus a `provenance` object. None of these three read
tools accepts `confirmed` or requires a second chat approval after transport
authentication, an active matching read grant, and the record scope pass.

### Financial entry

```text
{
  "record_type": "financial",
  "record_id": "...",
  "kind": "payment_card",
  "display_name": "...",
  "values": {
    "field_id": "<user-provided financial value>"
  },
  "matching_hints": {                # optional safe hints
    "network": "...",
    "last4": "....",
    "account_last4": "...."
  },
  "sensitivity": "financial",       # optional; must be financial
  "allowed_origins": ["..."],        # optional
  "allowed_purposes": ["..."],       # optional
  "provenance": { ... }               # optional metadata object
}
```

`kind` must be one of `payment_card`, `bank_account`,
`financial_account`, or `custom_financial`. `display_name` and a non-empty
`values` object are required. Financial value field ids are selected by the
record owner, must be unique strings, and must not be reserved metadata names
or transient authentication names. Values are strings. `matching_hints` may
contain only the safe keys `account_last4`, `country`, `currency`,
`institution`, `last4`, and `network`; a last-four hint is exactly four
digits. Full numbers and security codes are never proposal previews or
metadata listings.

`request_financial_fields` accepts selected `field_ids` from the record's
`values` map and returns only those values plus `provenance`. An unknown field,
missing record, or failed scope is an error without a raw submitted value in
the error text.

## Metadata and response shapes

`list_profile_fields` returns metadata with these fields when present:

```text
field_id, category, display_name, sensitivity,
allowed_origins, allowed_purposes, provenance, updated_at
```

It never includes `value`.

`list_credentials` returns:

```text
credential_id, name, origin, username, field_ids,
allowed_origins, allowed_purposes, sensitivity, provenance,
created_at, updated_at
```

It never includes `password`.

`list_financial_records` returns:

```text
record_id, kind, display_name, field_ids, matching_hints,
allowed_origins, allowed_purposes, sensitivity, provenance,
created_at, updated_at
```

It never includes the `values` map or a full financial number.

`write_records` returns metadata only:

```text
{
  "records": [
    {"record_type": "...", "record_id": "...", "status": "created"}
  ]
}
```

Each status is `created`, `updated`, or `unchanged`. The broker validates all
entries and grants before one encrypted transaction; a failed validation or
storage operation leaves the batch uncommitted.

## Missing-field promotion contract

When a form has a required field with no compatible saved record, the acting
agent may run a just-in-time check-in:

```text
I need <field label> for <target origin> (purpose: <purpose>).
Choose: use once | save to Agent Autofill | skip.
```

The answer is available to the current form immediately. `use once` creates no
proposal and no write. `save to Agent Autofill` is eligible only when the value is
stable and user-supplied or explicitly verified. The saved profile record is
reusable across future origins and purposes, so omit `allowed_origins` and
`allowed_purposes` (the broker stores those as unrestricted empty lists). The
current origin and purpose still scope the narrow write grant for this save and
every later read is governed by its own request and grant.

The compact confirmation card is the approval unit and must contain the
canonical id, category, display name, exact ordinary value, source context,
sensitivity, saved-record scope, current allowed origin, current purpose,
confidence, and ambiguity. Saved-record scope must say `any future authorized
origin/purpose` for this path. If `profile.write` is missing, include its exact
resource and current scope on that same card; the agent must not create a
wildcard grant or use an administrative fallback. The user's single **save to
Agent Autofill** choice approves both the visible reusable entry and that displayed
narrow grant. Obtain the grant and send only the accepted entry to
`write_records` with literal `confirmed: true`; do not add a second generic
proposal-approval prompt.

Do not offer promotion for OTPs, one-time codes, passwords, payment values,
tokens, handles, CAPTCHA answers, session data, or inferred page values. Use
the dedicated credential/financial workflow for a user-entered secret and keep
one-time values transient. A missing-field save approval never approves an
external browser submission.

An update replaces the complete stored record at that id; it is not a field
patch. In particular, omitted optional credential fields take their defaults,
and financial fields omitted from `values` disappear. Build an update only
from safe saved metadata plus raw values the user supplied or explicitly
verified for that proposal. Do not copy raw values returned by a scoped read
back into `write_records` merely to preserve them. If the user-verified data
is not a complete replacement, classify the candidate as needing review and
leave the stored record unchanged.

## Proposal and approval format

Create a row for every eligible stable candidate, including no-ops and rows
held for review, before calling `write_records`. Forbidden transient material
and values inferred without user provision or verification are not candidates
and must not appear as proposals.

```text
| # | action | record type/id | display/name (`display_name` or `name`) | category/kind + fields | value preview | source context | sensitivity | allowed origins/purposes | confidence + ambiguity |
|---|---|---|---|---|---|---|---|---|---|
| 1 | add | profile; id=address.home | Home address | address: street, city | <ordinary value> | user supplied now | normal | origin=example.invalid; purpose=form | high; ambiguity=none |
| 2 | update | credential; id=site-login | Example login | credential: password | [masked; change] | user verified now | credential | origin=example.invalid; purpose=login | high; ambiguity=none |
| 3 | no-op | profile; id=email | Email address | contact: email | <same ordinary value> | user verified now | normal | origin=example.invalid; purpose=form | high; ambiguity=none |
```

For each row also show the record's display/name (`display_name` for profile or
financial records, `name` for credentials), source context, sensitivity, allowed
origins, allowed purposes, confidence, and an ambiguity note when applicable.
Write `none` when an origin or purpose list is unrestricted, and use an
explicit confidence level such as high, medium, or low. The source context can
say that a value came from the user's current response or was verified during
the current form review, but must not contain hidden page state, a raw secret,
or a full financial number.

Classify rows as follows:

- `add`: no record with the canonical id exists;
- `update`: that record exists and the explicitly approved entry differs;
- `no-op`: the complete normalized entry is equal to the saved record.

Do not label a row `no-op` merely because its metadata looks similar. If the
comparison value is not available under an authorized field-selective read,
mark the row as needing review and do not write it.

For ordinary profile values, show the proposed value exactly. For credential
and financial records, show field names and safe metadata, but represent each
secret-bearing field as `[masked; set]` or `[masked; change]`. The user must
approve the exact numbered entry and scopes, while the raw sensitive value
remains transient and is not repeated in chat or artifacts.

## Forbidden transient material

Never propose or persist these as reusable form records:

```text
access_token, auth_code, captcha, cookie, csrf, credential_handle,
handle, login_code, one_time, otp, refresh_token, session, token,
verification_code
```

This also covers one-time authentication codes returned by
`request_auth_code`, session artifacts, and any inferred value the user did
not provide or verify. A password is not transient, but it is still forbidden
in a profile entry and may be saved only in a dedicated encrypted credential
record. Payment/financial values may be saved only in a dedicated encrypted
financial record. Retrieved sensitive values may appear only in the
successful, authenticated, field-selective response that requested them; do
not copy them into proposals, audits, listings, exceptions, files, or saved
records.
