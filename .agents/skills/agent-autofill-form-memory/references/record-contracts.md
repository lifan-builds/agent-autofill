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
| `request_profile_fields` | `field_ids`, `target_origin`, `purpose` | none | Read selected profile values under `profile.read` grants. |
| `request_credential_fields` | `credential_id`, `field_ids`, `target_origin`, `purpose` | none | Read selected credential fields under `credential.read`. |
| `request_financial_fields` | `record_id`, `field_ids`, `target_origin`, `purpose` | none | Read selected financial fields under `financial.read`. |
| `write_records` | `target_origin`, `purpose`, `confirmed`, `entries` | none | Persist one explicitly approved batch. `confirmed` must be literal `true`. |

The MCP catalog declares `agent_id` as an optional string property on each of
these tools. The three `request_*_fields` schemas use an array of strings for
`field_ids`; `write_records` uses an array of objects for `entries`. The broker
limits field and entry collections to 1 through 100 items and rejects
duplicates.

The following existing catalog operation is intentionally not a record-write
path:

| Tool | Required inputs | Optional input | Boundary |
| --- | --- | --- | --- |
| `request_auth_code` | `account_id`, `target_origin`, `purpose` | `max_age_seconds` | Returns one recent target-bound OTP only under `email.otp`; an OTP is transient and never becomes a proposal. |

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
The broker requires an active grant for the exact agent, operation, record id
(or wildcard resource), target origin, and purpose. It then enforces the
record's own `allowed_origins` and `allowed_purposes`. A missing, expired,
revoked, wrong-resource, wrong-origin, or wrong-purpose grant denies the
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
