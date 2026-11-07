# Autofill for agents: the product tour

[Watch the 33-second tour](assets/agent-autofill-tour.mp4).

The film illustrates an agent-assisted workflow with synthetic data. The cards
are explanatory graphics, not screenshots of the current interface. It is a
product introduction, not an end-to-end integration test or a Google OAuth
verification recording.

## What the scenes promise

| Scene | Available behavior | Boundary |
| --- | --- | --- |
| Chrome remembers your details | The local Chrome intake skill reads supported structured names, contact details, and addresses from `Default` or `Profile N`. | Choose the Chrome profile and review the proposed rows. This is not arbitrary autocomplete-history or payment-card import. |
| Bring in the details you approve | `save_approved_profiles` saves reviewed ordinary profile rows, and `apply_trial_to_live` promotes confirmed `Home` / `Work` bundle meanings. | Requires local execution and exact approved rows; an MCP connection alone cannot read Chrome. |
| The right details for this task | `list_profile_fields`, `list_profile_groups`, and `match_form_fields` expose metadata and confirmed meanings; the consuming agent uses context to select the needed fields. | The agent supplies judgment. The broker is not an embedded language model, and the film does not promise infallible matching. Ask when the address choice is unclear. |
| Inbox to your agent | Configured email and macOS Messages sources support authenticated, scoped OTP and verification reads with origin and purpose. | Mailbox or Full Disk Access setup and authorization are required. Ambiguous codes fail closed; this is not a promise that every provider or login works. |
| Stored locally, selected details reach your agent | Local records use AES-GCM encrypted SQLite storage, connector secrets use macOS Keychain, and `mcp-install` configures local MCP clients. | Selected reads enter the receiving agent's context (unless `credential.use` extension handoff is used for website logins). Cloud-agent retention rules apply; local storage does not mean nothing leaves the device. |

Start with [installation](../README.md#install-the-command), then follow
[reviewed Chrome intake](chrome-seed-onboarding.md). Read
[access modes](access-modes.md) before choosing trusted local access or narrow
explicit grants. Trusted local access is broader than per-request approval.

Agent Autofill is macOS-first and information-only. Your agent handles the
browser and its confirmation policy. The broker cannot enforce a confirmation
pause inside an arbitrary browser.

## Implementation evidence

The tour (`v8`) was checked against public implementation commit
`7adda2d`:

- Chrome multi-profile intake and live bundle promotion: [chrome.py](../agent_autofill/chrome.py),
  [review.py](../agent_autofill/review.py), and
  [Chrome/review tests](../tests/agent_autofill/test_chrome.py).
- Metadata, confirmed meanings, form matching, selected reads, code scoping,
  macOS Messages `attributedBody` decoding, and revocation:
  [broker service](../agent_autofill/service.py),
  [messages adapter](../agent_autofill/messages_adapter.py), and
  [service tests](../tests/agent_autofill/test_service.py).
- Encrypted local storage and MCP client setup:
  [storage implementation](../agent_autofill/storage.py) and
  [CLI tests](../tests/agent_autofill/test_cli.py).

The focused suite passed 377 Python tests and 22 Node extension tests. This
validates synthetic broker behavior; no live mailbox, Chrome profile, website
submission, or fresh user onboarding was recorded in this film.

## Media provenance

The public cut uses editorial Remotion visuals (`AutofillV8`) with AI narration
from ElevenLabs, original instrumental music composed in code (without sampled
songs), and procedural synthesized UI sound effects. The narration is reused
with the maintainer's separate permission. The visible fonts are Instrument
Serif, Inter, and JetBrains Mono, licensed under SIL Open Font License 1.1. No
font files are distributed with this MP4. All illustrated names, addresses, and
codes are synthetic.
