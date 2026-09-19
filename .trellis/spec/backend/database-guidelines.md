# Database Guidelines

> Current persistence patterns for Agent Autofill.

## Overview

The project uses Python's `sqlite3` directly; it has no ORM or migration
framework. `storage.Store` owns the broker database. Application values are
JSON-encoded and authenticated-encrypted with Fernet before they enter the
generic `kv` table. The encryption key comes from the operating-system
credential store in normal use and may be supplied explicitly by tests.

Two other SQLite sources are read-only integrations rather than application
databases: Chrome databases are copied to temporary snapshots before reading,
and the macOS Messages database is opened with `mode=ro`.

## Schema and Naming

`Store.__init__` creates the current schema idempotently:

```sql
create table if not exists kv (
  namespace text,
  key text,
  value text not null,
  primary key(namespace, key)
);
create table if not exists audit (
  id integer primary key,
  agent_id text,
  operation text,
  resource text,
  created_at text,
  metadata text
);
```

- Table and column names are lowercase `snake_case`.
- Logical record types are lowercase namespace strings such as `profile`,
  `grant`, `agent`, `connection`, `credential`, and `otp-consumed`. Legacy
  `otp-used` rows are purged when a compatible store opens because older keys
  included the plaintext code.
- Identifiers intended to be opaque use `secrets` or truncated SHA-256 hashes;
  do not derive secret-bearing identifiers from plaintext output.
- Timestamps are timezone-aware ISO 8601 strings produced by `now_iso()`.

## Query Patterns

- Use parameter placeholders for runtime values, as in
  `select value from kv where namespace=? and key=?`.
- `Store.put` is the normal write path and uses `insert or replace`, followed
  by an immediate `commit()`.
- `Store.get` is the normal point read and decrypts only after fetching the
  encrypted envelope.
- `Broker` currently performs a few direct scans/deletes on `store.db` for
  namespace-level operations. Keep values encrypted and use `Store._open` or
  `Store.get` consistently when following those existing patterns.
- Bound result counts at the service boundary. `_bounded_limit` clamps search
  limits to 1 through 100 before an adapter query runs.
- For external databases, inspect table/column availability before selecting
  optional fields. `chrome.py` does this through `sqlite_master` and
  `pragma table_info`.

## Transactions and Migrations

- There is no migration runner or schema-version table. The two base tables
  are created with `create table if not exists` when `Store` opens.
- Current writes are small and commit immediately. Multi-record atomic
  transactions are not an established convention; connector replacement and
  approved record batches are the explicit exceptions and use
  `Store.put_many`/`Store.apply_changes` with rollback coverage. Seal every
  value before beginning the transaction and keep deletes in the same
  transaction as related encrypted updates.
- One-time consumption markers use `Store.put_if_absent`; the `kv` primary key
  rejects a second insert even when separate broker processes race.
- Any future schema change must remain compatible with existing local database
  files. Do not silently drop or rewrite user data.

## Security and File Handling

- Do not store database keys or connector credentials in SQLite. Normal keys
  and connector secrets live behind `KeyringProvider`.
- OTP consumption markers must be derived from a hash of the agent, account,
  and message identifiers; never include the code in a SQLite namespace or
  key. The code itself is transient response data only.
- Non-memory broker database files are created with mode `0600`.
- Keep audit metadata encrypted and filtered. `Store.audit` recursively removes
  secret-bearing keys, including nested password, token, handle, card/account
  number, security-code, and raw-value fields, before sealing.
- Website credentials remain in the encrypted `credential` namespace and
  payment/bank/financial records remain in the encrypted `financial` namespace;
  neither may be flattened into profile fields. Metadata listings omit the
  encrypted `password`/`values` maps and expose only field ids and safe hints.
- Open Messages with a read-only URI. Never write to the user's Messages
  database or copy it into project artifacts.
- Read Chrome databases from a temporary snapshot, close the connection in a
  `finally` block, and unlink the snapshot. Password material is accepted only
  from an explicit user-exported CSV and is encrypted immediately.

### Google OAuth connector state

- A Gmail connection uses `auth_kind=google_oauth` and stores the host,
  username, mailbox, label, and opaque `secret_name` in encrypted `connection`
  metadata. The client JSON path is not persisted.
- The Keychain value is a versioned JSON payload containing only the Google
  client id, optional client secret, and refresh token. Short-lived access
  tokens are process-memory state and must never enter SQLite, Keychain,
  audit metadata, exceptions, or CLI output.
- Treat the OAuth payload as a closed schema: require exactly `version`,
  `provider`, `client_id`, `refresh_token`, and optional `client_secret`.
  Reject and do not preserve unknown keys, especially `access_token`; callers
  canonicalize through `GoogleOAuthTokenProvider` before writing Keychain.
- On server or `email-test` startup, reconstruct a provider from the Keychain
  payload and refresh only when a new IMAP connection needs a token. A failed
  payload parse leaves the source visible but unavailable for reads.
- Connector replacement writes the new refresh/client payload and encrypted
  metadata before deleting the prior Keychain entry. A failed metadata write
  removes the new payload and preserves the previous configuration.

## Common Mistakes to Avoid

- Interpolating user-provided values into SQL. The existing dynamic SQL in
  `chrome.py` is limited to column names discovered from the database schema.
- Reading or listing decrypted `value` envelopes outside `Store` and the
  narrowly scoped broker operations that need them.
- Returning a stored password through broker metadata or an unscoped MCP
  output. The field-selective credential read is the only website-password
  response path.
- Treating `create table if not exists` as a general migration system.
- Using a normal read/write connection for Messages or reading a live Chrome
  database without taking a disposable snapshot.

## Representative Tests

- `tests/agent_autofill/test_storage.py` proves values persist across connections
  while plaintext does not appear in the raw `kv.value` column.
- `tests/agent_autofill/test_service.py` proves audit storage omits search terms,
  OTP values are not logged, and credential handles are one-time.
- `tests/agent_autofill/test_messages_adapter.py` builds a minimal fixture and
  verifies conversation-scoped reads.
