# Access and delivery modes

> **Platform:** macOS first. Linux and Windows support are coming soon.

For a service the user asks the agent to access, Agent Autofill defaults to
direct use of a matching saved login without asking for a delivery-mode choice
or separate sign-in confirmation. The agent explicitly requests only the needed
fields with `delivery="direct"`, the exact origin, and the task purpose.
Extension delivery is available when the user explicitly requests a one-time
bridge handle that keeps a password outside agent input. Ambiguous accounts,
missing credentials, and separate access or verification requirements still
need resolution; existing grants and record scopes remain enforced.
The dashboard is read-only. The acting agent uses the CLI/MCP path to fill its
own browser surface and applies the submission policy before an external
submit action.

## Compare the choices

| | Direct delivery | Optional extension delivery |
| --- | --- | --- |
| Agent receives | Approved selected fields | Username plus a short-lived handle |
| Password enters agent input | Yes, only in the authenticated response | No |
| Extra install | None | Chrome extension and loopback bridge |
| Origin check | Grant and broker scope | Grant, broker scope, and active page origin |
| Best for | Agents that are allowed to handle the value | Lower exposure when a local bridge is preferred |
| What still needs a decision | Submission confirmation and external verification | Submission confirmation and external verification |

The extension is optional. Direct delivery is not a fallback for an extension
failure, and extension delivery is not an automatic upgrade from direct mode.
Use direct mode by default. For legacy or remote access requiring a grant,
request the same mode and fields covered by that grant.

## The review-first flow

```mermaid
sequenceDiagram
  participant U as User or acting agent
  participant B as Agent Autofill broker
  participant E as Optional extension
  participant P as Active page

  U->>B: Match origin and username metadata
  B-->>U: Value-free candidates
  U->>B: Request scoped fields (direct by default; existing grant if required)
  alt Direct delivery
    U->>B: request_credential(delivery="direct")
    B-->>U: Approved selected fields
  else Extension delivery
    U->>B: request_credential(delivery="extension")
    B-->>U: One-time origin-bound handle
  U->>E: Optional bridge consumes handle
    E->>B: Consume handle once
    B-->>E: Fill payload for the matching origin
    E->>P: Fill current page; clear handle
  end
  U->>P: Review, submit, and verify if desired
```

Agent Autofill ends at the information handoff. The acting agent may navigate,
type, and review through its own browser without another Agent Autofill prompt
immediately before typing. Routine sign-in to the requested service proceeds
without another prompt, including login to read a financial account. Other
submissions follow the existing confirmation policy; submitting financial
information always requires confirmation. The CLI is the canonical agent-facing surface; the dashboard
explains stored metadata and policy.

For a form label, `match_form_fields` returns a value-free ranked candidate
table with confidence and a focused question when a tie or missing record needs
human context. The acting agent may choose the best candidate or Chrome group
from page context and continue filling; it should ask the user only when the
context is genuinely insufficient. After a confirmed result or a user
correction, `record_form_mapping_feedback` stores the selected field IDs,
origin, purpose, and outcome so a later match can prefer that choice. It never
stores the field value.

## Direct delivery

First inspect safe metadata:

```bash
agent-autofill --db ~/.agent-autofill.sqlite3 \
  credentials list --target-origin https://example.test
```

Preview a site grant and choose direct mode:

```bash
agent-autofill --db ~/.agent-autofill.sqlite3 \
  grant-site codex https://example.test "account setup" \
  --delivery direct
```

The preview shows the matching site, username metadata, field IDs, origin,
purpose, and expiry. It does not display or persist a password. The eventual
`request_credential` call must name `delivery="direct"` and approved field IDs.

## Optional extension delivery

Preview the same site with the lower-exposure handoff:

```bash
agent-autofill --db ~/.agent-autofill.sqlite3 \
  grant-site codex https://example.test "account setup" \
  --delivery extension
```

Start the loopback bridge:

```bash
agent-autofill --db ~/.agent-autofill.sqlite3 fill-server
```

Then load `browser_extension/` as an unpacked extension when the optional bridge
is needed. The [extension guide](../browser_extension/README.md) documents the
transport boundary and recovery messages; the agent still obtains the handle
through the CLI/MCP path.

Pair the browser session to discover approved requests without copying each
handle. The pairing code stays in extension session memory, and the broker
stores its verifier. Pairing lasts eight hours and is limited to one agent;
it does not grant additional credential access.

The bridge checks page suitability before consuming the handle and rechecks
origin at delivery. It consumes each handle at most once, keeps passwords out
of extension storage, and clears attempted handoffs after consumption starts.
Preflight failures leave the unused handle available in the open popup.
It fills username and password controls only; it does not click or submit.

## OTP and other information

The same principle applies to email OTPs, verification links, profile fields,
and financial records: match metadata first, ask for the narrowest operation,
and let the acting agent decide what to do with the successful response. OTPs,
links, tokens, and handles are transient response data and never appear in
audits, listings, diagnostics, or docs.

General email search and message reads use the exact configured mailbox and do
not require a target-domain match. OTP retrieval defaults to target-origin
sender matching. Mailbox-wide OTP selection requires explicit `mailbox-read`;
it is never an automatic fallback when service matching fails.

## Safety checklist

Before a sensitive request, confirm:

- the exact configured mailbox is the intended source;
- the purpose describes the user-visible task;
- the selected field or account is the one you intend to use;
- the delivery mode matches your exposure preference;
- you know who will review and submit the external form.

For full grant rules and legacy operations, see the [operator guide](agent-autofill.md).
