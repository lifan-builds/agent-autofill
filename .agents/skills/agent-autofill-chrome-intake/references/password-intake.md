# Optional Chrome Password Manager intake

Use this reference only after the host agent received a clear affirmative
answer to the skill's local-only password question. It describes the existing
CLI path; it does not add a broker API or a password-returning tool.

## Preview, then sync

Close Chrome yourself when asked. The agent must never force-close it. Run a
value-free preview for the explicitly selected profile:

```bash
agent-autofill --db ~/.agent-autofill.sqlite3 chrome password-manager \
  --profile-dir "/path/to/selected/Chrome/Profile" \
  --preview
```

The command reads supported encrypted `Login Data` records through Chrome's
Safe Storage credential in the operating-system credential store. It supports
Chrome v10/v11 records on macOS. The preview returns counts plus safe origin
and username metadata for additions, updates, unchanged entries, and stale
entries. It never returns or prints a password.

Show the complete preview and ask for approval of the exact local sync. If the
user approves, run:

```bash
agent-autofill --db ~/.agent-autofill.sqlite3 chrome password-manager \
  --profile-dir "/path/to/selected/Chrome/Profile" \
  --confirm-import
```

`--confirm-import` is an acknowledgement that the approved additions and
updates will be sealed into the selected local Agent Autofill database. The
database is the only destination. The importer does not create a CSV, stage a
plaintext file, send credentials to the agent provider, or control Chrome.

Do not pass `--remove-stale` unless the user separately names that deletion in
their approval. A stale entry is retained by default. If the preview or sync
reports an unsupported encryption format, a locked profile, or unavailable
Chrome Safe Storage, stop with the safe operator message and explain the local
retry (usually close Chrome or unlock the OS credential store). Never weaken the
boundary by asking for a password pasted into chat or a copied Chrome key.

## Failure and output rules

- A declined, ambiguous, or revoked consent means no password database read and
  no credential writes.
- A missing local database or broker error means no fallback to CSV, remote
  export, browser automation, or an unencrypted file.
- Keep passwords only in the importer process until each is sealed by the
  broker. Do not place them in the proposal, provenance, audit metadata,
  screenshots, logs, diagnostics, or the agent response.
- Report only counts, credential IDs if the command provides them, and safe
  retry guidance. Never repeat origins or usernames beyond the value-free
  preview needed for the user's review.
