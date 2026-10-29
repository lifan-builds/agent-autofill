# Autofill for agents: the product tour

[Watch the 33-second tour](assets/agent-autofill-tour.mp4).

The film illustrates an agent-assisted workflow with synthetic data. The cards
are explanatory graphics, not screenshots of the current interface. It is a
product introduction, not an end-to-end integration test or a Google OAuth
verification recording.

## What the scenes promise

| Scene | Available behavior | Boundary |
| --- | --- | --- |
| Chrome remembers your details | The local Chrome intake skill reads supported structured names, contact details, and addresses. | Choose the Chrome profile and review the proposed rows. This is not arbitrary autocomplete-history or payment-card import. |
| Bring in the details you approve | `save_approved_profiles` saves reviewed ordinary profile rows. | Requires local execution and exact approved rows; an MCP connection alone cannot read Chrome. |
| The right details for this task | `list_profile_fields` and `list_profile_groups` expose metadata; the consuming agent uses context to select the needed fields. | The agent supplies judgment. The broker is not an embedded language model, and the film does not promise infallible matching. Ask when the address choice is unclear. |
| Inbox to your agent | A configured email source supports authenticated, recipient-scoped OTP requests with origin and purpose. | Mailbox setup and authorization are required. Ambiguous codes fail closed; this is not a promise that every provider or login works. |
| Stored locally, selected details reach your agent | Local records use encrypted storage and connector secrets use the OS credential store. | Selected reads enter the receiving agent's context. Cloud-agent retention rules apply; local storage does not mean nothing leaves the device. |

Start with [installation](../README.md#install-the-command), then follow
[reviewed Chrome intake](chrome-seed-onboarding.md). Read
[access modes](access-modes.md) before choosing trusted local access or narrow
explicit grants. Trusted local access is broader than per-request approval.

Agent Autofill is macOS-first and information-only. Your agent handles the
browser and its confirmation policy. The broker cannot enforce a confirmation
pause inside an arbitrary browser.

## Implementation evidence

The tour was checked against public implementation commit
`ee712fe301aaaff1db183feaa8cb6b13e78cd5ce`:

- Chrome intake: the [local intake recipe](../.agents/skills/agent-autofill-chrome-intake/references/local-intake.md)
  and its [synthetic tests](../tests/agent_autofill/test_chrome_intake_recipes.py).
- Metadata, selected reads, code scoping, ambiguity, and revocation:
  [broker service](../agent_autofill/service.py) and [service tests](../tests/agent_autofill/test_service.py).
- Encrypted local storage: [storage implementation](../agent_autofill/storage.py)
  and [storage tests](../tests/agent_autofill/test_storage.py).

Those three test files passed 80 tests in an isolated checkout. This validates
synthetic broker behavior; no live mailbox, Chrome profile, website submission,
or fresh user onboarding was tested for this film. Newer ranked form matching
and remembered mapping helpers in development are not required by these public
claims.

## Media provenance

The public cut uses the selected editorial visuals with AI narration from
ElevenLabs and original instrumental music composed in code, without sampled
songs. The narration is reused with the maintainer's separate permission.
The visible font is Space Grotesk, licensed under SIL Open Font License 1.1;
Georgia uses the renderer's installed system font. No font files are distributed
with this MP4. All illustrated names and codes are synthetic.
