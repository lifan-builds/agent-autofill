# Troubleshooting

Start with a value-free diagnosis:

```bash
agent-autofill --db ~/.agent-autofill.sqlite3 doctor
```

The CLI reports counts and a concrete next action. It does not print values,
passwords, tokens, connector secrets, or OTPs.

## Keychain is unavailable

Unlock the macOS `login` keychain in Keychain Access, then run `doctor` from a
normal host process. A restricted worker or locked keychain can prevent the
encrypted vault key from being read. Keep the key, connector secrets, and
service tokens in the operating-system credential store; do not copy them into
files or environment variables.

## A grant or handle was rejected

Requests are bound to the exact agent, resource, origin, purpose, expiry, and
delivery mode. Review the metadata first, then create a fresh grant or handle:

```bash
agent-autofill --db ~/.agent-autofill.sqlite3 \
  credentials list --target-origin https://example.test
agent-autofill --db ~/.agent-autofill.sqlite3 \
  grant-site codex https://example.test "account setup" --delivery extension
```

Never retry a consumed OTP or extension handle. Request a new one after fixing
the underlying scope or origin problem.

## Chrome intake stops or reports a lock

Chrome structured-address intake is read-only and stops when the source schema
is unsupported or the database is locked. Close Chrome yourself, rerun the
preview, and review the proposal before approving any write. Agent Autofill
does not close the browser or fall back to copying private databases.

## The extension cannot reach the bridge

Start the loopback bridge and refresh the popup:

```bash
agent-autofill --db ~/.agent-autofill.sqlite3 fill-server
```

The popup must show the expected HTTPS origin and an available bridge. The
bridge is loopback-only, consumes a handle once, and never submits the page.

## A source is configured but not ready

Run the source-specific read-only check and inspect metadata:

```bash
agent-autofill --db ~/.agent-autofill.sqlite3 sources
agent-autofill --db ~/.agent-autofill.sqlite3 email-test <account-id>
```

Connector passwords and OAuth payloads remain in Keychain. Remove a source
explicitly when it is no longer needed; a failed secret removal leaves the
source configured so it can be recovered safely.

## Need a safe product tour

Use the synthetic demo. It opens an in-memory vault and never touches your
database, Keychain, browser, mailbox, or Messages data:

```bash
agent-autofill demo
```
