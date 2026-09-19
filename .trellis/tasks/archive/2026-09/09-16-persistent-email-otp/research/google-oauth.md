# Google Desktop OAuth and Gmail IMAP

Official contracts checked on 2026-09-17:

- Google installed applications open the system browser and may receive the
  authorization response on a loopback IP with an operating-system-selected
  port. PKCE is supported and appropriate for desktop apps, which cannot keep
  client secrets confidential.
  https://developers.google.com/identity/protocols/oauth2/native-app
- Installed-app authorization returns access and refresh tokens; access tokens
  can be refreshed without user interaction when offline access was requested.
- Gmail IMAP/POP/SMTP requires `https://mail.google.com/`; public applications
  using the full-mail scope can require Google verification.
  https://developers.google.com/workspace/gmail/imap/xoauth2-protocol
- Gmail IMAP authenticates with SASL XOAUTH2 using a short-lived OAuth access
  token. Agent Autofill can keep the access token in memory and persist only the
  refresh/client material in Keychain.

Product consequence: the MVP accepts a user-owned Google Cloud Desktop OAuth
client JSON. It does not bundle or publish a shared Agent Autofill client. App
Passwords and legacy caller-supplied bearer tokens remain optional fallbacks.
