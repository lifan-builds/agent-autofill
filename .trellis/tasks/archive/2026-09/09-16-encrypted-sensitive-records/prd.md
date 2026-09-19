# Encrypted Credentials and Payment Records

## Goal

Allow Agent Autofill to retain explicitly approved password credentials and
payment/financial form data locally with encryption so they can be reused
without being placed in ordinary profile records.

## Confirmed Current Behavior

- `Store` encrypts all JSON values with Fernet before SQLite persistence; the
  database encryption key is obtained from the operating-system credential
  store in normal use.
- Password credentials already use the encrypted `credential` namespace and
  metadata-only listing. `request_credential_use` returns a short-lived,
  origin-bound, one-time handle plus the username; the raw password is exposed
  only to the internal `consume_credential_handle` method.
- There is no dedicated payment or financial record model, listing, grant, or
  use-capability flow.

## Requirements

- Keep OTPs, OAuth/access/refresh tokens, session cookies, and one-time handles
  out of reusable records.
- Preserve password credentials as a dedicated encrypted record type; never
  store them as profile fields.
- Add a dedicated encrypted type for approved payment/financial records with
  metadata-only listings and explicit sensitivity.
- Support broad Chrome-style saved-record categories while keeping passwords
  and payment/financial values in dedicated types rather than ordinary profile
  fields.
- Require exact user approval before creating or updating a credential or
  payment/financial record suggested by the skill.
- Do not log or include raw sensitive values in audits, errors, screenshots,
  tests using real data, or documentation.
- Bind raw-value access to an authenticated agent, scoped grant, target origin,
  purpose, and short expiry. Agent Autofill returns the authorized value as an
  information service; it does not operate the browser.
- Do not add a second per-use approval prompt at retrieval time. A valid active
  grant authorizes reads until expiry/revocation; the consuming agent is
  responsible for approval before browser typing or submission.

## Acceptance Criteria

- [x] Approved credential and payment/financial records are encrypted at rest
      and survive a new process.
- [x] Metadata listings are useful for deduplication and review but omit
      passwords, full account/card numbers, security codes, and other raw
      financial values.
- [x] Record metadata exposes category, sensitivity, display label, and
      non-secret matching hints sufficient for LLM field mapping without
      exposing raw passwords or full payment values.
- [x] Rejected or unapproved proposals create no reusable record.
- [x] OTPs and session artifacts cannot be stored through these operations.
- [x] Outside the successful explicitly scoped read response, raw sensitive
      values do not appear in audit rows, protocol diagnostics, exceptions,
      screenshots, fixtures containing real data, or docs.
- [x] An authenticated, correctly scoped agent can retrieve an approved raw
      credential or payment/financial value, while wrong-origin, wrong-purpose,
      expired, revoked, and unauthenticated requests return no raw value.
- [x] Disconnect/delete behavior is explicit and leaves no orphaned use grant
      capable of accessing the removed record.

## Out of Scope

- Performing a purchase, submitting a payment form, browser automation, or
  syncing records to a cloud vault.
