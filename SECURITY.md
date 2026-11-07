# Security policy

Agent Autofill is a local-first, information-only broker. The supported
release surface is **macOS first**; Linux and Windows support are coming soon.

## Report a vulnerability

Please do not open a public issue for a suspected vulnerability. Until a
dedicated security address is published, send a private report to the
repository owner through the contact method listed in the GitHub profile for
`lifan-builds`, including:

- the affected version or commit;
- a minimal, synthetic reproduction;
- the impact and the data boundary crossed; and
- whether any real credential, token, OTP, or financial data was exposed.

Never include real passwords, agent tokens, mailbox tokens, Messages database
copies, OTPs, private keys, or payment values in a report. Redact them and use
synthetic fixtures.

## Supported security boundary

- Values are encrypted in the local SQLite store; database and connector keys
  remain in the operating-system credential store.
- The review surface binds to loopback only.
- Sensitive reads require authentication, an exact resource scope, origin and
  purpose binding where applicable, expiry, and revocation checks.
- Credential-use handles are one-time and origin-bound; direct field reads are
  selected by the caller and audited without raw values.
- Email, Messages, and browser sources are read-only integrations.
- The broker does not navigate browsers, submit forms, send or delete
  messages, purchase anything, or claim that an external site accepted an
  action.

## Disclosure expectations

We will acknowledge a report when we can reproduce it, keep the reporter
updated when a fix is available, and credit the reporter if they want credit.
This project is early-stage; do not assume a report has been received until a
private acknowledgement is sent.
