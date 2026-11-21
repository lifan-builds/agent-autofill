# Local multi-day trial

The user enabled this global installation to try the confirmed onboarding
meanings in daily tasks. This is the selected ordinary-profile source:

- CLI: `/opt/homebrew/bin/agent-autofill`
- Encrypted trial: `/Users/lfan/.agent-autofill/reviews/trial-4b4b83f70280c1ba.sqlite3`
- Development checkout: `/Users/lfan/Project/agent-autofill`

Use the installed CLI from any working directory; `uv run` is unnecessary.
For ordinary profile context use `review-agent --resume` with this trial and
`--query`, supplying JSON on stdin as described in conversation-onboarding.md.
Do not run `--profile-snapshot` again or repeat the initial questionnaire.
Retrieve existing meanings first. If the trial is missing, stale or inaccessible,
report that condition; do not silently choose another source.

For actual tasks supply the actual website origin, a specific purpose and
only the required semantic fields. Start with metadata; `read_values: true`
returns only the selected authorized fields when the result is ready.
Never redirect values to files or persist them in reports. Use a subprocess
argument list with JSON stdin for calls; never interpolate source values into
shell commands. A local owner read is not approval to submit a form.

Confirmed role meanings available in this trial include `shopping-informal`
and `official` for name selection, `personal-contact` for email/phone, `home`
for the current US home, `home-china` for the China home, and `virtual-shopping`
for the user-designated virtual-shopping address. Discover candidates through
authorized context metadata; the personal values and relationships remain in
the encrypted store. Do not treat an address designation as validation of a
merchant's billing, residency or tax requirements.

Retrieve name, contact and address separately according to their roles. Never
take a name from a home address bundle when a more specific confirmed name role
applies. Keep all components of each address within one bundle. Do not use a
virtual-shopping address for physical shipping. Unknown role or currentness
on a family member's record remains unresolved even though the relationship
is confirmed. Ask only the consequential remaining question.

The global `agent-autofill` MCP connection uses the live broker for existing
credentials, email, Messages and financial workflows under their normal scoped
access rules. It does not contain these trial meanings. Do not use its ordinary
profile fallback to override trial exclusions, corrections or unresolved cases.
No live-vault migration was authorized by enabling this trial.

Corrections to the fixed who/role/currentness choices can use the existing
proposal/confirmation CLI. Richer relationships and usage rules need the
scoped context write workflow; do not overwrite them with the page's limited
choices or claim a chat acknowledgment was a saved correction. If that path
is unavailable, keep the correction in the conversation and state the limit.

At the end of a useful real task, report a brief result only when helpful:
what meaning was reused, whether clarification was needed, and any failure.
Do not ask for an evaluation survey after every task or create background
tracking, recordings, external telemetry or recurring reminders.
