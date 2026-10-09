# First-run authorized workflow

## Goal

Let a new local installation complete its first authenticated information request through the existing setup CLI and MCP transport, with explicitly selected permissions. Prove the path with synthetic fixtures and reconcile the continuation guide with delivered behavior.

## Background and evidence

- `agent_autofill/cli.py:625`: setup creates an agent, imports Chrome data, and optionally configures IMAP/Messages, but creates no grants.
- `agent_autofill/service.py:1406`: existing grants support operation, resource, optional origin/purpose, and a 600-second default expiry.
- `agent_autofill/service.py:1586`: email search is account-scoped, without time-window or origin/purpose inputs.
- `agent_autofill/service.py:1643`: OTP reads require origin/purpose, freshness, exactly one candidate, hostname binding, and per-agent/account/message one-time consumption. They return provenance, not confidence or an expiry field.
- `agent_autofill/server.py:331`: the JSON-lines entry point restores persisted connectors. Existing transport tests primarily call `_call` directly.
- `.trellis/tasks/archive/2026-09/09-16-persistent-email-and-form-suggestions/prd.md`: Gmail OAuth persistence and scoped credential/financial reads are already delivered.

## Approval status

The user approved the final scope on 2026-09-19. Implementation is authorized against the requirements below.

## Requirements

- R1: Append an optional grant step to setup. Skipping it, leaving the agent blank, or declining final confirmation creates no new grants. Never rotate an existing token or silently reactivate a revoked identity.
- R2: Offer exact saved profile field IDs and one exact configured email account, without displaying their values or connector secrets. Select profile read, account-scoped email search, and origin/purpose-bound email OTP independently. No wildcard, write, credential, financial, Messages, or email-read grants are included.
- R3: Require nonempty origin and purpose for selected profile/OTP grants. Use a fixed 600-second lifetime. Show exact operations/resources/scopes/lifetime and explicitly explain that email search covers the selected account, not just the target origin. Require affirmative confirmation before persisting the validated proposal.
- R4: Exercise an empty temporary encrypted database through CLI `main`, fixture Chrome import, persisted fake IMAP setup, and a newly constructed server through its `main` JSON-lines loop. Replace external Keychain/provider I/O only; retain real storage, authentication, authorization, serialization, and connector reconstruction.
- R5: Reconcile `NEXT_STEPS.md` and setup documentation with actual delivered contracts. Do not implement unsupported roadmap promises such as time-window search or newest-code selection.

## Acceptance Criteria

- [x] AC1 (R1-R3): Setup tests prove only explicitly selected and confirmed grants are created. Skip, cancellation, blank identity, invalid selection, and incomplete scope create no proposed grants; existing tokens/grants remain intact. Revoked identities are not reactivated.
- [x] AC2 (R4): A deterministic integration test imports synthetic Chrome profile/login metadata, creates an agent, configures a fake mailbox, and grants access through setup. Reopening the database via server startup restores the connector and accepts an authenticated JSON-lines profile read and benign mailbox search.
- [x] AC3 (R4): The same path retrieves one synthetic recent origin-bound OTP with account/message/timestamp provenance. Repeating that request with the same agent is denied, including after reopening the database.
- [x] AC4 (R4): Focused integration/service coverage denies a wrong token, ungranted field/account, wrong origin/purpose on profile/OTP reads, expired grant, stale/ambiguous OTP, and the next request after revocation. Reuse existing coverage rather than duplicating every case at every layer.
- [x] AC5 (R2-R4): Grant previews contain no stored values or connector secrets. The synthetic OTP appears only in the expected successful OTP response in this scenario, not in audits, errors, or setup output. Search fixtures are benign; this does not claim general email-content sanitization. New-agent tokens retain their deliberate one-time setup display.
- [x] AC6 (R5): Documentation accurately describes search/OTP semantics, acknowledges delivered Gmail/scoped sensitive-field reads, and distinguishes fixture integration from live-provider acceptance.
- [x] AC7: Focused tests, the full existing suite, and Ruff pass without accessing personal databases, browser profiles, live mailboxes, or the operating-system credential store.

## Out of scope

- Live account/OTP verification or changes to the migrated database and Keychain.
- New providers, browser automation, form submission, outbound messages, a fill helper, packaging/release work, publishing, or pushing commits.
- New schemas or transport abstractions, general search redaction, time-window search, OTP selection changes, global one-time OTP semantics, or automatic token rotation.
- Reimplementing delivered Gmail/credential/financial capabilities or bumping the version merely because of the milestone label.

## Risks and limitations

- A 10-minute grant may expire during consuming-agent configuration; document renewal with the existing grant command.
- Account-scoped search discloses authorized mailbox content and must not be described as origin-bound or OTP-only.
- In-process entry-point coverage establishes persistence/reconstruction/protocol behavior, not installation, live Keychain, or live IMAP acceptance.
- Skipping the grant step does not roll back earlier imports or connector configuration.
