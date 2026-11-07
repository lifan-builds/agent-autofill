# Product video alignment review — 2026-10-28

Reviewed the 33-second v6 film and its production notes from the referenced
chat, public main `ee712fe301aaaff1db183feaa8cb6b13e78cd5ce`, and the current
development checkout. The public cut is staged separately in the attached
`autofill-video` worktree so existing development changes are preserved.

## Findings and disposition

| Finding | Resolution | Release state |
| --- | --- | --- |
| “Uses the task's context” could imply the broker contains an LLM. | Public cut says **Your agent uses context**. Public metadata/group APIs support this workflow. | Published in `090abeb`. |
| “Your saved information stays local” can sound like a no-data-egress guarantee. | Public cut says **Stored locally. Selected details reach your agent.** Companion docs explain cloud-provider context and trusted local access. | Published in `090abeb`. |
| Inbox-to-agent could appear to work without configuration. | Public cut says **Connected inbox** and **Scoped request**; docs state setup and authorization requirements. | Published in `090abeb`. |
| Local matching incorrectly reports “Delivery address” missing when Chrome street records exist. | Added composite address labels for delivery, shipping, and billing. Candidates preserve address groups and require agent judgment about completeness, even for a single component. Explicit task-specific hints still take precedence. | Fixed locally with 15 parameterized regression cases; not on public main. |
| Ranked matching and learned mapping helpers are absent from public main. | Public tour describes the consuming agent choosing from existing metadata APIs; it does not advertise the development-only helpers as released. | Separate product release still needed for those helpers. |
| v6 includes free-plan ElevenLabs narration and a private-review footer. | The user confirmed separate permission for the existing narration. The final public cut retains that voice and original music, with clarified claims and a macOS/AI-narration/browser-responsibility footer. Original v6 preserved. | Narration permission resolved; published in `090abeb`. |

## Design assessment

The local-first, information-only broker fits the video when **autofill for
agents** means reviewed reuse of information by a consuming agent. The broker
does not navigate, fill arbitrary forms, or submit them. Semantic choice and
clarification remain the agent's responsibility. A metadata match does not
authorize a value read, and an authorized read does not prove a website accepted
anything.

Chrome intake preserves source groups, requires reviewed rows, and keeps
password import separate. OTP retrieval requires a configured source and
explicit origin/purpose. Synthetic service tests cover ambiguous codes,
one-time consumption, revocation, and audit redaction. No blanket promise of
automatic setup, zero interruptions, or all-provider support is justified.

## Verification

- Development checkout: 130 focused tests passed across video workflow,
  cross-layer workflows, Chrome intake, service, matching benchmark, form
  policy, and storage. Ruff passed.
- Public baseline: 80 focused Chrome intake, service, and storage tests passed.
  `uv run --with ruff python -m ruff check --isolated agent_autofill tests/agent_autofill`
  passed. An earlier shell `ruff` invocation applied a different expanded rule
  set and reported 106 pre-existing findings; it was not used as evidence of a
  clean public baseline.
- Independent review found a country-only false match in the first fix.
  The corrected matcher now requires whole-address completeness judgment;
  regressions cover country-only, postcode-only, street-only, multiple
  components in one group, and competing address groups.
- Public video: 33.003 seconds, 1920×1080, H.264/AAC. Synthetic illustrations;
  original instrumental and existing ElevenLabs narration, reused with the
  user's confirmed separate permission.
- No real account, Chrome profile, mailbox, or website was accessed for this
  review. Synthetic test success does not establish a live end-to-end cycle.

The Google OAuth verification screencast remains a separate deliverable; this
animated product tour does not satisfy it.

## GitHub publication

Published the four-file media/documentation package to main at
`090abeb5ae7e292096184d6c659acb1650946e47` under the user's request to include
the video in GitHub. Verified the remote main SHA and downloaded README,
companion documentation, poster, and MP4 over HTTPS; all bytes match the
reviewed local artifacts. The final MP4 SHA-256 is
`1684a7adf8ad8108798b96009f52a93ea87b57ac8a917bac7884e9d9f7a85101`.
Its decoded audio matches original v6 exactly. Independent verification passed
after the completeness fix and again after restoring the selected narration.

The product-code fix is local to the development checkout and is not part of
this documentation/media publication. The approved X post is published at
https://x.com/LifanBuilds/status/2115665964947931152, with its text and playing
33-second video verified on the permalink.
