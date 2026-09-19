# Persistent Email Connection and OTP Retrieval

## Goal

Connect a generic IMAP mailbox through the Agent Autofill CLI, including a
Thunderbird-style Google Sign-In path for Gmail, verify the credentials before
saving them, and let later CLI/server processes retrieve a recent OTP for an
authenticated agent with an active scoped grant.

## Confirmed Current Behavior

- `email-add` accepts an IMAP password or OAuth bearer token and stores it in
  Keychain through `KeyringProvider`; encrypted SQLite stores only connection
  metadata and the Keychain lookup name.
- `IMAPEmailAdapter` authenticates and opens the mailbox read-only for each
  operation. No OAuth authorization-code flow, token refresh, or saved
  provider session lifecycle exists.
- `request_auth_code` returns an OTP when its account/origin/purpose grant is
  active, the code is recent and uniquely bound to the target origin, and it
  has not already been consumed. Audit metadata excludes the code.

## Requirements

- Keep `email-add` as the generic Thunderbird-style setup command. It accepts
  host, port, mailbox, username, and either a password or caller-supplied
  bearer token; it must perform a read-only connection test before declaring
  the source configured.
- Add `email-add-gmail` for Google OAuth 2.0 installed-app authorization. The
  local operator supplies a Google Cloud Desktop app client JSON, the command
  opens Google Sign-In in the system browser, receives the authorization code
  on a random loopback port, uses PKCE, and verifies Gmail IMAP read-only
  access before saving the connection.
- Request `https://mail.google.com/` with offline access. Store the refresh
  token and OAuth client material only in the operating-system credential
  store. Keep access tokens in process memory only and refresh them before a
  new IMAP connection when needed.
- Do not bundle a shared Google OAuth client. App Password login and the
  caller-supplied bearer-token mode remain optional generic-IMAP fallbacks.
- Add `email-test <account_id>` to prove that the saved source can reconnect
  without asking for the secret again. Starting `serve` must reconstruct the
  same adapter from saved metadata plus the Keychain secret.
- Store the password or bearer token only in the operating-system credential
  store. Store host, port, mailbox, username, auth kind, label, and the opaque
  Keychain lookup name in encrypted SQLite.
- Preserve read-only mailbox access and the existing account-level adapter
  boundary.
- Treat scoped OTP exposure as intentional: successful responses may include
  the code and account/message/received-at provenance, while
  audit/error/diagnostic output must not.
- Require no additional approval at retrieval time after transport
  authentication and an active account/origin/purpose-scoped `email.otp`
  grant succeed. Browser typing or submission policy belongs to the consuming
  agent.
- Fail closed for expired/revoked authentication, expired grants, wrong
  account/origin/purpose, stale codes, ambiguity, and reuse.
- Use deterministic fake-provider coverage for automated tests and a separate
  opt-in manual smoke procedure for a live account; never commit live mailbox
  data or credentials.

## Acceptance Criteria

- [x] `agent-autofill ... email-add` tests a generic IMAP login and reports the
      configured account without printing the password or bearer token.
- [x] `agent-autofill ... email-test <account_id>` verifies a saved read-only
      connection and emits only non-secret account metadata.
- [x] Closing the login process and starting a fresh server process can use the
      saved authentication to query the mailbox while it remains valid.
- [x] `email-add-gmail` uses a browser authorization-code flow with loopback
      callback, state validation, PKCE, and the Gmail mail scope; no access or
      refresh token appears in CLI output, SQLite, exceptions, or tests.
- [x] A saved Gmail source refreshes an access token from Keychain-backed
      OAuth state in a fresh process and authenticates to
      `imap.gmail.com:993` through XOAUTH2.
- [x] The existing scoped OTP rules remain enforced and gain coverage for
      unauthenticated requests, expired grants, stale codes, account isolation,
      saved-connection reuse, and no-extra-approval retrieval.
- [x] OTP responses identify the account, message, and received timestamp;
      audit records identify the request/source but omit the OTP and body.
- [x] Disconnecting the source removes its stored authentication material and
      prevents subsequent retrieval.

## Live Acceptance — 2026-09-17

- Created a dedicated user-owned Google Cloud OAuth project, enabled the Gmail
  API, configured an external testing audience with one test user, added the
  `https://mail.google.com/` scope, and created a Desktop OAuth client.
- Completed the loopback/PKCE consent flow against the user's Gmail account.
  The initial XOAUTH2 login selected `INBOX` read-only and saved the connector.
- Ran `email-test personal-gmail` in a separate process; it reconstructed the
  provider from encrypted metadata plus Keychain state, refreshed an access
  token, and authenticated to `imap.gmail.com:993` without prompting.
- Reconstructed the adapter through the server startup path in another fresh
  process and fetched recent mailbox data read-only. The smoke printed only a
  message count, never message contents or OAuth material.
- No single unambiguous OTP was present in the bounded one-hour live window,
  so a real code was not returned or consumed. Deterministic coverage verifies
  the scoped one-time OTP response and audit/persistence redaction contract.
- Removed the temporary duplicate OAuth client material used during setup;
  only the saved connector's required client/refresh state remains in
  Keychain.

## Out of Scope

- Browser-cookie/profile capture, password scraping, outbound email, browser
  control, and applying the OTP to a form.
- Supporting provider-specific OAuth for providers other than Google.
- Registering, publishing, or verifying a shared Agent Autofill Google OAuth
  client. The local operator owns the supplied Desktop client configuration.
- Migrating legacy caller-supplied OAuth bearer tokens; they continue to be
  reused only while valid.

## Technical Notes

Generic IMAP does not have a portable serialized login session. The saved
state is the account configuration plus Keychain secret; each operation opens
a fresh connection, selects the mailbox read-only, and logs out.

Google desktop authorization follows the installed-app flow documented at
https://developers.google.com/identity/protocols/oauth2/native-app. Gmail IMAP
uses the `https://mail.google.com/` scope and SASL XOAUTH2 documented at
https://developers.google.com/workspace/gmail/imap/xoauth2-protocol.
