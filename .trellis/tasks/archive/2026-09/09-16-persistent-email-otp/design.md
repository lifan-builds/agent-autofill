# Technical Design

## CLI Contract

### `email-add-gmail`

Add a provider-specific convenience command:

```text
email-add-gmail ACCOUNT_ID USERNAME --client-secrets PATH
                [--label LABEL] [--mailbox INBOX]
```

`PATH` is a Google Cloud OAuth client JSON created with application type
`Desktop app`. Parse only the installed-app client id and optional client
secret; use the fixed Google authorization/token endpoints. Generate a fresh
state value and PKCE verifier/challenge, bind a temporary HTTP listener to
`127.0.0.1` on an operating-system-selected port, and open the authorization
URL in the system browser. Accept exactly the matching loopback callback,
exchange its code, and require both an access token and refresh token.

Request `https://mail.google.com/` and offline access. The first access token
is used to verify XOAUTH2 against `imap.gmail.com:993` and select the requested
mailbox read-only. Persist one versioned Keychain JSON payload containing the
client id, optional client secret, and refresh token only after that check
succeeds. SQLite stores `auth_kind=google_oauth` and non-secret IMAP metadata.
Never persist the short-lived access token.

### `email-add`

Retain the existing command and arguments:

```text
email-add ACCOUNT_ID HOST USERNAME [--label LABEL] [--port 993]
          [--mailbox INBOX] [--oauth2]
```

The command prompts through `getpass` for a password or caller-supplied bearer
token. It constructs an `IMAPEmailAdapter` in memory and calls a small
read-only connection check that authenticates, selects the mailbox with
`readonly=True`, and logs out. Only a successful check is persisted.

Use a versioned opaque Keychain lookup name for each replacement. Save the new
secret, atomically replace connection metadata, then delete the prior secret.
If persistence fails, delete the new secret and keep the prior configuration.
CLI output contains only account id, host, mailbox, username, auth kind, and a
success/failure summary.

### `email-test ACCOUNT_ID`

Load saved connection metadata and its Keychain secret, recreate the adapter,
run the same read-only connection check, and report non-secret metadata. It
does not prompt for or rotate the secret. Missing source, missing Keychain
secret, authentication failure, TLS/network failure, and mailbox-selection
failure are distinct safe failures.

### Reconnect and removal

`serve` continues to call `_register_connections`, which reconstructs adapters
from saved metadata and Keychain material. `source-remove` deletes the current
Keychain secret before removing the connection/source and revoking its grants.

Generic IMAP has no portable live session to serialize. Persistence means
reusing saved configuration and secret to establish a fresh connection in a
new process.

## Adapter Boundary

Add a `check_connection()` operation to `IMAPEmailAdapter` and factor common
connect/select/logout behavior so `search`, `recent`, `get`, and CLI testing
all remain read-only. Password and XOAUTH2 bearer-token branches retain their
current behavior. For `google_oauth`, the adapter calls a token-provider
boundary before each new IMAP connection. The provider caches a still-valid
access token in memory and otherwise exchanges the Keychain-backed refresh
token at Google's token endpoint. OAuth failures are translated to safe
operator-facing authentication errors without response bodies or tokens.

## Gmail OAuth Boundary

Keep browser authorization and token exchange in a provider-specific module;
keep the IMAP adapter limited to asking for a current access token and issuing
XOAUTH2. The complete flow is:

```text
Desktop client JSON -> loopback/PKCE authorization -> token exchange
  -> first access token -> read-only IMAP verification
  -> Keychain(client id, client secret, refresh token)
  -> encrypted SQLite(non-secret connection metadata)
  -> fresh process -> refresh exchange -> memory-only access token -> XOAUTH2
```

The loopback listener has a bounded wait and returns a minimal success/failure
page. It does not become a general local web surface. State mismatch, denied
consent, timeout, malformed client JSON, token endpoint failure, absent refresh
token, and rejected IMAP authentication all fail without saving new state.

## OTP Contract

The existing MCP tool remains `request_auth_code`:

```json
{
  "account_id": "personal",
  "target_origin": "https://example.com",
  "purpose": "login",
  "max_age_seconds": 300
}
```

Transport authentication plus an active `email.otp` grant for the account,
origin, and purpose is sufficient. There is no approval parameter.

The successful result is:

```json
{
  "code": "synthetic-example-only",
  "provenance": {
    "source": "email",
    "account_id": "personal",
    "message_id": "42",
    "received_at": "2026-09-17T00:00:00+00:00"
  }
}
```

Do not persist the code. Replace the legacy plaintext `otp-used` key (which
included the code) with a non-secret consumed marker derived from agent id,
account id, and message id. Purge legacy `otp-used` rows during the compatible
upgrade path so old codes do not remain in SQLite keys. Audit metadata contains
message id, account id through the resource column, target origin, and purpose,
but not code, body, subject, sender, or request parameters.

## Failure Matrix

| Case | Result |
| --- | --- |
| Missing/invalid agent token | transport authentication error |
| Missing/expired/revoked/wrong-scope grant | authorization error |
| Unknown account or unavailable saved adapter | safe broker error |
| Missing/invalid connector secret | safe connection error, no secret echo |
| Stale or absent code | no unambiguous recent code |
| Multiple candidates | ambiguity refusal |
| Candidate not bound to target origin | binding refusal |
| Previously returned message/code | consumed refusal |
| Invalid `max_age_seconds` | bounded-input error |

## Compatibility and Operations

Existing saved connections keep their current `secret_name` and load without
migration. A later successful `email-add` or `email-add-gmail` replacement
moves that account to a versioned secret name and removes the former one only
after metadata commits. Server startup reconstructs Google token providers
from Keychain state; it may leave an unavailable source listed for repair, but
OTP retrieval must fail clearly rather than fall back to unscoped in-memory
data.
