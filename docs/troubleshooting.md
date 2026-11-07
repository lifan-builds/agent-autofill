# Troubleshooting

Start with a value-free diagnosis:

```bash
agent-autofill --db ~/.agent-autofill.sqlite3 doctor
```

The CLI reports counts and a concrete next action. It does not print values,
passwords, tokens, connector secrets, or OTPs.
When the broker cannot open, inventory is unknown: JSON counts are `null`,
not zero. This does not establish that saved data is missing.

## Keychain is unavailable

Read the failure category before changing settings:

- `keychain_auth_failed` / `-25293`: macOS denied this process's vault request.
  The Keychain may already be unlocked. Run the same installed
  `agent-autofill doctor` command in an interactive Terminal. If it succeeds
  there, compare the launching application and runtime with the failing
  process. If it also fails, inspect the `agent-autofill` / `vault:v1` item's
  Access Control and the actual Python executable's signing identity.
- `keychain_interaction_unavailable` / `-25308`: unlock the login Keychain and
  use an interactive logged-in session where a permission prompt can appear.
- `keychain_request_cancelled` / `-128`: retry when ready to answer the prompt.

A valid code signature does not prove permission to read a Keychain item.
The Python keyring library's signing suggestion is generic; it does not verify
the executable. A virtual environment can also resolve to a shared Python
installation whose identity changes during updates. Record the resolved
executable and compare it when diagnosing a failure; do not re-sign it blindly.

### macOS authentication recovery

If `-25293` also occurs in Terminal while the login Keychain appears unlocked,
compare with Apple's own settings-only diagnostic:

```bash
security show-keychain-info ~/Library/Keychains/login.keychain-db
```

If that also reports an authentication error, the failure extends beyond
Agent Autofill's Python integration. A developer reported this pattern after
macOS 26.4 and Apple acknowledged changes to login Keychain unlocking in its
[developer discussion](https://developer.apple.com/forums/thread/822120).
This is a possible cause, not a diagnosis from the error code alone.

The reported workaround is to manually lock the **login** keychain once in
Keychain Access, then unlock it through the normal macOS password prompt.
Other apps may briefly request Keychain access. Enter the password only in
the system prompt, then rerun `agent-autofill doctor`. Agent Autofill does not
automatically lock a shared Keychain or collect its password. If unlocking
fails, stop and use macOS support; do not reset or recreate the keychain.

If the Lock command is disabled, do not force it or change Keychain permissions.
On macOS 26.5.2, users have also reported system-wide authentication failures
with Codex's Locked Use authorization hook enabled. Their
[recovery reports](https://github.com/openai/codex/issues/35553) describe turning
off Locked Use in Codex settings, then locking the Mac screen and unlocking it
with the typed macOS account password. This disables computer use while locked;
it is a user-controlled workaround, not a confirmed cause for every `-25293`.
Afterward, check both `security show-keychain-info` and `agent-autofill doctor`
before declaring recovery. Do not edit the system authorization database.

This recovery sequence was verified on a macOS 26.5.2 installation on
2026-10-19: the settings-only check succeeded, the existing encrypted broker
opened, and scoped email-code requests worked again without recreating keys.
That confirms recovery on one installation; it does not establish which
component caused the failure or guarantee that it will not recur.

Keep the key, connector secrets, and service tokens in the OS credential store.
Do not delete/recreate the vault, enable unrestricted application access, or
copy secrets to files or environment variables as a recovery step. Diagnose
with `doctor`; a successful synthetic demo does not verify live Keychain access.

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

For a saved Google OAuth source whose authorization expired or was revoked:

```bash
agent-autofill email-reconnect-gmail <account-id>
agent-autofill email-test <account-id>
```

Reconnection reuses the OAuth client stored in Keychain and opens Google's
normal consent flow with Gmail read-only access. It checks the account identity
and configured mailbox before replacing the saved connector. A failed or
cancelled authorization preserves the old connection. No client-secret export
or mailbox read is needed. An older IMAP source is migrated to the read-only
Gmail API; this does not revoke any older Google app permissions.

## Need a safe product tour

Use the synthetic demo. It opens an in-memory vault and never touches your
database, Keychain, browser, mailbox, or Messages data:

```bash
agent-autofill demo
```
