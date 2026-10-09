# Organize Agent Autofill code and documentation

## Goal

Refactor the codebase into clearer cohesive modules and refresh the README without changing public behavior or the information-only security boundary.

## Background

The product behavior is now stable, but the implementation has grown around a
large CLI module, a large broker service module, and three overlapping README
surfaces. The next improvement is maintainability: a new contributor should be
able to find command orchestration, broker policy, transport, and user guidance
without changing the public contract.

## Requirements

- R1: Preserve all current CLI commands, MCP tool names and schemas, storage
  namespaces, authorization rules, one-time handle behavior, and the
  information-only boundary.
- R2: Extract cohesive, low-risk helpers from the largest modules where the
  module boundary is already visible in the code. Keep compatibility wrappers
  in the existing entry points so imports and command dispatch remain stable.
- R3: Remove obvious duplication in user-facing documentation. Make the root
  README the concise product and quick-start guide, keep package-level notes
  focused on developer/package usage, and link to the detailed operational
  guide instead of copying it.
- R4: Keep documentation explicit about direct versus optional extension
  delivery, local-only handling, and the fact that post-action verification is
  owned by the acting agent rather than Agent Autofill.
- R5: Add or update focused tests only where extraction or documentation-facing
  behavior could regress; do not rewrite tests merely to mirror moved code.

## Acceptance Criteria

- [ ] Existing public CLI and MCP behavior is unchanged under the focused and
      full test suites.
- [ ] The main CLI and service responsibilities have named module boundaries
      discoverable from the package tree, with no circular imports.
- [ ] README quick-start instructions work from a fresh checkout and point to
      the canonical detailed documentation.
- [ ] README and package docs contain no contradictory delivery, submission,
      or post-action verification claims.
- [ ] Ruff, mypy, compileall, diff checks, and the full pytest suite pass.
- [ ] No secrets, real account data, or new browser/submission behavior are
      introduced.

## Out of Scope

- Changing encrypted storage schemas, grant semantics, provider adapters, or
  MCP contracts.
- Adding browser navigation, form submission, outbound messages, or automated
  site acceptance checks.
- Rewriting the application into a new framework or introducing dependencies.
