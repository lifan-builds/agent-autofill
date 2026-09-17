# Technical Design

## Record Model

Keep the encrypted generic `kv` table and add logical contracts rather than a
new plaintext schema.

### Profile record

Existing `profile` records remain compatible. New writes retain `field_id`,
`category`, `display_name`, `value`, `sensitivity`, `allowed_origins`,
`allowed_purposes`, and `updated_at`, and add non-secret approval provenance.
Profile values may be JSON scalars or short lists of scalars. Credential,
payment, token, cookie, OTP, and handle categories are rejected from this type.

### Credential record

The existing `credential` namespace gains backward-compatible fields:

```text
credential_id, name, origin, username, password, note,
allowed_origins, allowed_purposes, sensitivity="credential",
provenance, created_at, updated_at
```

Legacy records default `allowed_origins` to their normalized `origin` and
`allowed_purposes` to unrestricted-within-grant. Connector credentials never
enter this namespace.

### Financial record

Add a `financial` namespace:

```text
record_id, kind, display_name, values, matching_hints,
allowed_origins, allowed_purposes, sensitivity="financial",
provenance, created_at, updated_at
```

`kind` supports `payment_card`, `bank_account`, `financial_account`, and
`custom_financial`. `values` is an encrypted map of stable snake-case field
ids to string values. This supports common card/account fields and additional
user-verified fields without flattening them into profile records.

`matching_hints` is broker-derived and metadata-safe: for example network or
institution plus last four digits. Listings include record kind, display name,
field ids, safe hints, scopes, and timestamps; they omit full numbers,
security codes, routing numbers, passwords, and arbitrary raw values.

## Approved Batch Write

Add one MCP tool, `write_records`:

```json
{
  "target_origin": "https://example.com",
  "purpose": "form-memory",
  "confirmed": true,
  "entries": [
    {"record_type": "profile", "record_id": "legal_name", "...": "..."},
    {"record_type": "credential", "record_id": "example-login", "...": "..."},
    {"record_type": "financial", "record_id": "primary-card", "...": "..."}
  ]
}
```

The transport authenticates the agent. The broker bounds the batch, validates
every entry, rejects duplicate ids in the same type, checks the matching
`profile.write`, `credential.write`, or `financial.write` grant for each id,
and requires the call origin/purpose to fit the proposed record scopes. It
rejects missing/false `confirmed` and transient field names such as OTP,
one-time code, access/refresh token, cookie, session, or credential handle.

Only after the full batch passes does the store write all entries in one SQLite
transaction. The response contains record type/id plus `created`, `updated`,
or `unchanged`; it contains no raw value. Audit metadata contains only those
actions and ids.

`confirmed: true` is an enforceable accidental-write guard, not proof of chat
consent. The consuming skill must obtain approval for the exact proposal.

## Direct Scoped Reads

Add field-selective tools:

- `request_credential_fields`: `credential_id`, requested field ids,
  `target_origin`, and `purpose`; requires `credential.read`.
- `request_financial_fields`: `record_id`, requested field ids,
  `target_origin`, and `purpose`; requires `financial.read`.

Each verifies transport authentication, grant scope, record-level allowed
origins/purposes, expiry, and revocation. It returns only the requested values
plus provenance. It has no confirmation or approval parameter; an active
narrow grant is sufficient. The consuming agent owns approval for later
browser typing or submission.

Keep `request_credential_use` and `consume_credential_handle` for compatibility
with existing callers, but do not route the new skill through them.

## Lifecycle

Add metadata listing for financial records and extend credential listings with
safe scope/sensitivity/timestamp metadata. Add a local administrative
`record-delete TYPE ID` command for profile, credential, and financial records.
Deletion removes the encrypted record and revokes exact-id grants for its read
and write operations; wildcard grants remain category capabilities but cannot
read a nonexistent record.

## Validation and Redaction

- Validate at `Broker`; transport schemas mirror but do not replace service
  validation.
- Never send raw entries or returned values to `Store.audit`.
- Expand defensive audit filtering for nested secret-bearing keys even though
  intended audit calls already send metadata only.
- Error messages name the record/type/rule, never a submitted value.
- All examples and fixtures use synthetic values.

## Compatibility and Rollback

No table migration is required. Missing fields on legacy credentials receive
safe defaults on read/update. New financial records and added fields remain
encrypted by the existing Fernet envelope. Rollback can remove the tools while
leaving encrypted records untouched for a later compatible version or explicit
local deletion.

