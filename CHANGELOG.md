# Changelog

## Unreleased

- Added a synthetic, dependency-free macOS-first product demo.
- Added the competitive and GTM brief, support matrix, security policy, and
  public release guidance.
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
