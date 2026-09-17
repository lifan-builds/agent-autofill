# Directory Structure

> How backend code is organized in StopAskingMe today.

## Overview

StopAskingMe is a single Python 3.11+ package. The package is intentionally
small and flat: transport and CLI entry points depend on a central `Broker`,
which depends on the encrypted `Store` and provider adapters. There is no
separate web application, ORM layer, or generic utilities package.

## Directory Layout

```text
stopaskingme/
├── __main__.py          # `python -m stopaskingme` entry point
├── cli.py               # setup and local administration commands
├── server.py            # JSON-lines MCP transport and tool catalog
├── service.py           # authorization and broker use cases
├── storage.py           # encrypted SQLite store and Keychain provider
├── models.py            # immutable data-transfer dataclasses
├── adapters.py          # provider-neutral Protocol contracts
├── imap_adapter.py      # read-only IMAP integration
├── messages_adapter.py  # read-only macOS Messages integration
├── chrome.py            # read-only Chrome metadata/import helpers
└── inbox.py             # loopback-only local review page
tests/stopaskingme/       # pytest modules parallel to package behavior
scripts/stopaskingme     # repository-local executable wrapper
docs/stopaskingme.md     # setup and operation documentation
```

## Module Organization

- Put authorization, grants, origin/purpose checks, audit decisions, and
  information-returning use cases on `service.Broker`. Examples include
  `request_profile_fields`, `request_auth_code`, and
  `request_credential_use`.
- Put persistence and encryption mechanics in `storage.Store`; callers use
  namespace/key operations rather than owning encryption details.
- Define small integration boundaries as `Protocol`s in `adapters.py`, then
  keep provider-specific I/O in a named adapter module. `IMAPEmailAdapter` and
  `MacMessagesAdapter` are the current examples.
- Keep transport adaptation in `server.py`: validate the agent token, dispatch
  to `Broker`, and serialize the result. Do not put authorization rules only in
  the transport because the CLI and tests also call `Broker` directly.
- Keep interactive setup and administrative output in `cli.py`; it assembles a
  `Store` and `Broker` but does not duplicate their business rules.
- Put simple immutable records in `models.py` as frozen dataclasses.
- Add focused tests under `tests/stopaskingme/test_<module-or-feature>.py`.

## Naming Conventions

- Modules, functions, local variables, and SQLite identifiers use
  `snake_case`; classes and exceptions use `PascalCase`.
- Private module helpers start with `_`, for example `_bounded_limit`,
  `_origin_matches`, `_snapshot`, and `_call`.
- Integration classes name the provider and role, such as
  `IMAPEmailAdapter` and `MacMessagesAdapter`.
- Resource operations use explicit verbs (`list_`, `search_`, `request_`,
  `consume_`, `register_`, `remove_`) rather than a generic handler hierarchy.

## Boundaries to Preserve

- Keep the product local-first and information-only. Do not add browser
  control, outbound messages, or form submission without an explicit product
  decision.
- Keep the Agent Inbox bound to loopback. `inbox.serve` defaults to
  `127.0.0.1`, and the CLI rejects non-loopback host arguments before calling
  it; any new caller must preserve the same restriction.
- Do not create a new layer or helper module for a one-off operation. The
  existing structure favors direct, feature-named modules and methods.

## Representative Examples

- `stopaskingme/service.py`: central business and authorization boundary.
- `stopaskingme/storage.py`: compact persistence boundary with encrypted
  values and redacted audit metadata.
- `stopaskingme/imap_adapter.py` and `stopaskingme/messages_adapter.py`:
  read-only provider adapters implementing the contracts in `adapters.py`.
- `tests/stopaskingme/test_transport.py`: verifies the public MCP surface keeps
  writes behind explicit confirmation and grant checks, and keeps sensitive
  reads field-selective and authenticated.
