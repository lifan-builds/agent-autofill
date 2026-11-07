# Support matrix

This matrix is the release contract for the current macOS-first preview. Core
Python behavior now runs in CI on macOS, Linux, and Windows. Native credential
stores, browser profiles, Messages, and provider accounts still require the
listed platform-specific smoke test before they can be called supported.

| Surface | macOS | Linux | Windows | Notes |
| --- | --- | --- | --- | --- |
| Python CLI, encrypted local store | Supported | CI-covered core | CI-covered core | Python 3.11+; OS credential-store behavior must be validated per platform |
| Loopback Inbox and JSON-lines/MCP transport | Supported | CI-covered core | CI-covered core | Loopback only |
| macOS Keychain-backed key and connector secrets | Supported | Coming soon | Coming soon | Uses the operating-system credential store |
| Chrome profile metadata intake | Supported | Coming soon | Coming soon | Read-only snapshot plus review |
| Chrome Password Manager v10/v11 import | Supported with explicit opt-in | Coming soon | Coming soon | v20/App-Bound Encryption and passkeys stop safely |
| macOS Messages read/search | Supported with macOS privacy permission | Not applicable | Not applicable | Read-only; no send/delete |
| IMAP/Gmail read-only email and scoped OTP | Supported | Coming soon | Coming soon | Connector setup and provider permissions apply |
| Profile and financial records | Supported | CI-covered core | CI-covered core | Encrypted local records; field-selective reads |
| Direct selected-field delivery | Supported | CI-covered core | CI-covered core | Authentication and active grant required |
| Optional Chrome extension handoff | Supported | Coming soon | Coming soon | One-time origin-bound handle; review and submit yourself |
| Acting-agent browser navigation and form submission | Supported by the acting agent | Coming soon | Coming soon | Agent Autofill supplies scoped data; typing is preparation, while fresh confirmation is required immediately before submit, always for financial information |
| Outbound messages, deletion, purchases | Unsupported | Unsupported | Unsupported | Requires a separate product decision |

## Product wording

Use: “macOS first; Linux and Windows coming soon.”

Do not use: “cross-platform,” “unreviewed autonomous submission,” “send
messages for you,” or “the site was verified” without evidence from the acting
agent's browser workflow.
