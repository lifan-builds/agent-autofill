# Changelog

## 0.2.0

- Added zero-Full-Disk-Access macOS Messages reading via read-only
  `faster-mac-use` (`fmu-ax`) Accessibility snapshots of `Messages.app` with
  zero focus theft; explicit `--database` / `--backend db` keeps read-only
  `chat.db` support.
- Added scoped macOS SMS and iMessage 2FA code retrieval via
  `request_messages_auth_code` (origin-matched by default, Tapback-filtered,
  transient response only) and routed `request_auth_code` through Messages
  when targeting a configured Messages source.
- Fixed unlabeled OTP fallback extraction to ignore calendar years (`1990`–`2099`).
- Allowed trusted local `write_records` calls and defaulted dual-save for newly
  created website logins.
- Added `username_hint` filtering to `list_credentials` and improved semantic
  profile group field resolution.
- Fixed cross-platform `mypy` type checking across Linux, macOS, and Windows.
- Tightened `README.md` quickstart, visual proof, and Mac Messages 2FA docs.

## 0.1.3

- Relicensed as open source `AGPL-3.0-only` with Section 7 attribution terms
  (`LICENSE`, `ATTRIBUTION.md`). `agent-autofill --version` and MCP
  `serverInfo.title` print the attribution. Contributions need a one-time CLA
  (`CLA.md`).
- Added a synthetic, dependency-free macOS-first product demo.
- Added the support matrix, security policy, and public release guidance.
- Clarified the Bitwarden parity foundation and the remaining platform and
  distribution gaps.

## 0.1.2

- Fixed dashboard totals for vaults with more than 100 records and labeled
  bounded metadata previews. Added section navigation, earlier attention items,
  clearer group labels, and no-store responses.
- Disabled Fill until a request or manual handle is available. Expired requests
  cannot fill username-only pages; both delivery steps target the checked document.
- Report page changes when reactive forms replace or clear fields during delivery.
- Added real-browser synthetic field checks and browser-helper tests to CI.
- Recover from malformed server requests and redact unexpected backend errors.
- Keep invalid Messages timestamps unknown and clean up failed temporary imports.
- Resolve repository-wide lint and type-check failures.

## 0.1.1

- Website OTP requests now default to sender-domain matching. Mailbox-wide
  selection requires explicit `origin_policy="mailbox-read"`.
- OTP errors include safe reason codes and recovery actions.
- Fixed inventory counts, address grouping, and matching beyond 100 records;
  added offsets to bounded metadata listings.
- Doctor reports installation identity and specific Keychain failure categories,
  with unknown inventory on startup failure and documented macOS recovery.

## 0.1.0

- Initial local-first information broker release.
- Encrypted local storage, scoped grants, profile and financial records,
  read-only email and Messages adapters, and optional Chrome handoff.
