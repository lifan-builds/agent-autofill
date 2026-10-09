# Design

## Refactor boundary

Keep `agent_autofill.cli` as the executable compatibility surface and
`agent_autofill.service.Broker` as the policy owner. Extract implementation
groups behind those surfaces rather than changing call signatures:

- CLI status and metadata presentation become a small status module.
- CLI record/profile presentation helpers become a small records module when
  extraction is mechanical and does not require moving parser registration.
- Shared service validation and metadata projection helpers move only when they
  have no broker-state coupling; stateful methods remain on `Broker`.
- The MCP server remains the transport boundary and the loopback fill server
  remains separate from browser UI code.

If a proposed move would require a new abstraction, schema, or permission
rule, leave it in place and document the follow-up instead of widening scope.

## Documentation structure

- `README.md`: product explanation, safe quick start, the two credential
  delivery choices, and links to deeper references.
- `agent_autofill/README.md`: package/developer notes and entry points, with
  no duplicated operational runbook.
- `docs/agent-autofill.md`: detailed operator and integration reference.
- `browser_extension/README.md`: extension-only setup and safety contract.

## Compatibility and rollback

Use re-exports or thin wrappers when moving a function that tests or local
consumers import directly. Validate each extraction with the existing tests;
reverting the new module import is sufficient to roll back a mechanical move.
No migration or data rewrite is needed.
