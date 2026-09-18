# Persistent Email OTP and Form Record Suggestions

## Goal

Let an authenticated agent retrieve a recent email OTP through StopAskingMe
without repeatedly asking the user to reconnect their mailbox, and help the
agent propose stable form values worth saving for future form-filling work.

## Background

- OTP exposure is an intentional product capability when the agent has an
  explicit `email.otp` grant bound to the relevant account, origin, and
  purpose. The project guidance and backend specs must be updated to match
  this decision.
- The current CLI can store an IMAP password or caller-supplied OAuth bearer
  token in the operating-system credential store, while connection metadata
  lives in the encrypted broker database. It does not perform a provider login
  flow, refresh an OAuth session, or prove live reconnection across processes.
- `request_auth_code` already applies age, ambiguity, origin, one-time-use, and
  audit-redaction checks, but the complete CLI-login-to-agent-retrieval path is
  not covered end to end.
- The repository has no StopAskingMe product skill. Official OpenAI guidance
  supports a repository-local, implicitly discoverable skill under
  `.agents/skills` with a focused `SKILL.md`.

## Task Map

1. `09-16-persistent-email-otp`: persistent CLI email authentication and
   scoped OTP retrieval.
2. `09-16-suggest-form-records-skill`: agent-facing skill and broker/CLI
   support for reviewable reusable-record suggestions.
3. `09-16-encrypted-sensitive-records`: encrypted local persistence and
   controlled use of password credentials and payment/financial records.

## Requirements

- Preserve local-first storage: authentication secrets or refresh material stay
  in the operating-system credential store, never in MCP responses, logs,
  screenshots, tests, documentation examples, or the encrypted SQLite values.
- OTP values may appear only in the successful response to the authenticated,
  explicitly granted OTP request; audit and diagnostic surfaces remain
  redacted.
- The first mailbox path is generic Thunderbird-style IMAP: host, port,
  mailbox, username, and a password or caller-supplied bearer token. A later
  process reconnects read-only from saved metadata plus the Keychain secret;
  provider-specific OAuth setup is deferred.
- Suggestions must be proposed as a diff against existing saved records and
  require explicit user approval before any record is persisted.
- Approval happens in the agent conversation: after displaying the exact
  add/update diff, the agent may call one authenticated `write_records` MCP
  tool for only the approved entries. The broker applies a type-specific
  `profile.write`, `credential.write`, or `financial.write` grant to each
  entry, bound to the target origin and purpose.
- Do not suggest transient secrets or session artifacts as reusable form data.
- OTPs remain a supported one-time response but are never persisted or
  proposed as saved records.
- Password credentials and payment/financial records may be proposed and saved
  only with explicit approval, using dedicated encrypted record types rather
  than ordinary profile fields.
- Support the broad Chrome-style autofill surface: identity, contact, address,
  payment/financial, credentials, and other stable fields that have a safe
  record type. The LLM performs semantic matching and confidence/ambiguity
  handling while StopAskingMe remains the encrypted system of record.
- StopAskingMe and its skill/MCP are an information layer only. They may return
  raw values, including approved credentials and payment/financial values, to
  an authenticated agent under the applicable narrow grant. Browser
  interaction belongs to the consuming agent and is not implemented here.
- Retrieval itself requires no additional per-use approval beyond successful
  agent authentication and an active scoped grant. The consuming agent owns
  any confirmation policy for typing or submitting the value in a browser.
- Keep outbound email, form submission, and automatic acceptance of
  suggestions out of scope.

## Acceptance Criteria

- [ ] A supported mailbox can be connected through the CLI once and reopened
      by a later CLI/server process without asking for the secret again while
      the saved authentication remains valid.
- [ ] An agent with the correct scoped grant can retrieve one recent,
      unambiguous OTP; wrong-origin, wrong-purpose, expired, ambiguous,
      consumed, revoked, or unauthenticated requests are denied.
- [ ] No authentication secret or OTP leaks into audit rows, diagnostics,
      fixtures containing real data, screenshots, or documentation.
- [ ] The StopAskingMe skill is discoverable for form-filling work and proposes
      useful stable fields missing from or differing from saved records.
- [ ] Chrome-style identity/contact/address/payment/credential fields can be
      mapped to saved records with confidence and ambiguity surfaced for review.
- [ ] Authorized agents can retrieve the approved raw values needed for their
      own browser/form workflow, while unscoped, expired, revoked, or
      unauthenticated requests are denied.
- [ ] Suggestions show the proposed field, source context, sensitivity, and
      whether the operation would add or update a record; ordinary values are
      exact, while credentials and financial values use masked set/change
      previews.
- [ ] Rejecting or ignoring a suggestion changes no saved data; approved
      entries survive a new process and are available to later scoped reads.
- [ ] All three child deliverables pass their focused tests plus the repository
      pytest, Ruff, and compile checks.

## Out of Scope

- Sending email or messages, controlling a browser, typing into or submitting
  forms, confirming payments, or automatically applying an OTP to a
  third-party site. Those actions belong to the consuming agent.
- Persisting browser cookies/profile directories as the mailbox session.
- Saving OTPs, access/refresh tokens, credential handles, or other short-lived
  values as form records. Passwords and payment/financial records use their
  dedicated encrypted record types instead.
- Adding multiple providers before one complete supported provider path works.
