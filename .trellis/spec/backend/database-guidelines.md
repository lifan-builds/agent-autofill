# Database Guidelines

> Current persistence patterns for StopAskingMe.

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
  `grant`, `agent`, `connection`, `credential`, and `otp-used`.
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
  transactions are not an established convention; adding one requires an
  explicit design and tests for failure/rollback behavior.
- Any future schema change must remain compatible with existing local database
  files. Do not silently drop or rewrite user data.

## Security and File Handling

- Do not store database keys or connector credentials in SQLite. Normal keys
  and connector secrets live behind `KeyringProvider`.
- Non-memory broker database files are created with mode `0600`.
- Keep audit metadata encrypted and filtered. `Store.audit` removes the known
  secret-bearing keys `body`, `code`, `password`, and `token` before sealing.
- Open Messages with a read-only URI. Never write to the user's Messages
  database or copy it into project artifacts.
- Read Chrome databases from a temporary snapshot, close the connection in a
  `finally` block, and unlink the snapshot. Password material is accepted only
  from an explicit user-exported CSV and is encrypted immediately.

## Common Mistakes to Avoid

- Interpolating user-provided values into SQL. The existing dynamic SQL in
  `chrome.py` is limited to column names discovered from the database schema.
- Reading or listing decrypted `value` envelopes outside `Store` and the
  narrowly scoped broker operations that need them.
- Returning a stored password through broker metadata or MCP output.
- Treating `create table if not exists` as a general migration system.
- Using a normal read/write connection for Messages or reading a live Chrome
  database without taking a disposable snapshot.

## Representative Tests

- `tests/stopaskingme/test_storage.py` proves values persist across connections
  while plaintext does not appear in the raw `kv.value` column.
- `tests/stopaskingme/test_service.py` proves audit storage omits search terms,
  OTP values are not logged, and credential handles are one-time.
- `tests/stopaskingme/test_messages_adapter.py` builds a minimal fixture and
  verifies conversation-scoped reads.
