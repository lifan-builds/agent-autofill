# Smooth user experience

## Goal

Make Agent Autofill understandable and quick to use for the common local-first
workflow: set up the broker, see what is stored, understand what needs attention,
and prepare an approved website fill without exposing secrets or widening the
information-only boundary.

## Confirmed current behavior

- Setup is a long sequence of independent CLI prompts for agent identity, Chrome,
  mail, Messages, and optional grants.
- The Inbox is a static metadata dashboard with raw tables and audit details.
- The extension requires a manually started loopback bridge, unpacked extension
  installation, and repeated entry of agent ID plus one-time handle.
- Profile values can currently be passed as command-line arguments.
- Local services already pair through the operating-system credential store, and
  the broker already enforces encrypted storage, scoped authorization, explicit
  writes, origin binding, one-time handles, and loopback-only review surfaces.

## Requirements

- R1: Add a guided `quickstart`/status path that detects available local sources,
  explains readiness and blockers, and presents a concise value-free summary of
  the setup choices before any writes. It must be resumable and preserve the
  existing explicit approval requirements.
- R2: Simplify the Inbox around “what is stored” and “needs attention.” Show
  human-readable cards for sources, profile groups, credentials, financial
  metadata, agents, expiring grants, and recent activity. Keep raw audits and
  machine-readable metadata available behind an advanced disclosure.
- R3: Add a `doctor` command or equivalent status output with actionable checks
  for broker availability, Keychain readiness, configured source health, and
  expiring/revoked agents or grants. Diagnostics must remain value-free.
- R4: Reduce extension friction by persisting only nonsecret local preferences,
  detecting bridge availability, showing the active origin, and giving clear
  recovery messages. Keep manual review and submit, one-time origin-bound
  handles, and no navigation/click/submit behavior. Treat the extension as
  optional: users may instead choose direct authenticated field-selective agent
  handling, while extension mode keeps the password out of agent input.
- R5: Add an interactive profile add/edit path that does not require sensitive
  values in shell arguments and presents safe metadata confirmation before
  storing encrypted values.
- R6: Update documentation and focused tests for the new entry points and UI
  behavior. Preserve all existing public contracts and unrelated user edits.

## Acceptance criteria

- A fresh user can discover the broker state and next action from one command
  without knowing internal grant names or source IDs.
- Inbox first view answers what is stored, what is missing, and what needs
  attention without showing profile values, passwords, card numbers, tokens, or
  OTPs; advanced metadata remains available.
- Status and error output gives a concrete next step and never logs secrets.
- Extension settings survive reopening without storing handles, passwords, or
  tokens; the current origin and bridge state are visible before consumption.
- Website credential workflows clearly offer direct agent handling or optional
  extension handling, with the exposure trade-off explained before use.
- The executable credential request requires a delivery choice. Direct mode
  requires selected field IDs; extension mode issues only the scoped handle.
  Refused requests never fall back to the other mode. `grant-site` confirms
  exactly the operation corresponding to the chosen delivery mode.
- Profile values entered through the interactive path do not appear in shell
  history, process arguments, logs, audits, or review HTML.
- Existing tests plus focused UX tests, Ruff, compile checks, and diff checks pass.

## Out of scope

- Browser automation, navigation, form submission, outbound messages, or
  automatic approval of sensitive reads.
- Changes to encrypted storage schemas, grant semantics, credential/financial
  field-return contracts, or provider-owned databases.
- Chrome v20/App-Bound Encryption or passkey support, package distribution, or
  live-provider acceptance.

## Risks and deferred items

- A fully graphical setup flow would be a larger product surface; this slice
  keeps the existing CLI and loopback dashboard entry points.
- Source health checks must use existing read-only adapters and must avoid
  prompting for or printing connector secrets.
