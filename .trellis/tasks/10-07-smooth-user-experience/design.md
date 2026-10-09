# Smooth user experience design

## Boundaries

Keep user-facing orchestration in the CLI, rendering in the loopback Inbox, and
small local preferences in the browser extension. Reuse `Broker` metadata APIs,
existing source checks, and existing encrypted storage. Do not add a new
permission model or transport.

## Data flow

`quickstart` and `doctor` read broker metadata and safe source status, render
plain-language next steps, and delegate writes to the existing reviewed commands.
The Inbox derives a concise overview from existing metadata-only lists and keeps
full audit JSON in a collapsed advanced section. The extension stores only a
nonsecret agent identifier and bridge URL in Chrome local storage, checks the
active HTTPS origin before requesting consumption, and clears the one-time handle
after every attempt.

## Compatibility

Existing commands continue to work. New commands are additive. The dashboard
remains loopback-only. No database migration is required. Profile editing uses
the existing `Broker.put_profile` contract and does not alter record schemas.

## Credential delivery choice

The user approved implementing two per-workflow delivery modes on 2026-10-07:
`direct` gives an authenticated agent only its selected credential fields;
`extension` returns a short-lived origin-bound handle to be consumed locally.
Add an explicit `request_credential` MCP entry point requiring a delivery mode,
delegating to the existing broker reads and handle issuance. Direct mode requires
field IDs; extension mode requires origin/purpose and rejects field IDs. Do not
fall back between modes or create both grants. Existing tools stay compatible.

`grant-site` offers the same choice, with `--delivery direct|extension` for
explicit scripted selection. Preserve explicit legacy `--operation` calls.
Dashboard and local-agent guidance explain the choice and use the unified tool.
No persisted global policy, new schema, bridge auto-install, or browser submit
is introduced.

## Trade-offs

The first slice favors clear static rendering and actionable CLI output over a
full frontend framework. Source health is best-effort metadata and connection
checks; it must report unavailable state rather than infer a healthy source.
