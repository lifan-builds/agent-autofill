# Access and delivery modes

Agent Autofill makes the exposure choice visible before a sensitive credential
read. The agent uses the CLI/MCP path to request approved fields directly, or
to request a one-time bridge handle that keeps a password outside agent input.
The dashboard is read-only and does not fill forms.

## Compare the choices

| | Direct delivery | Optional extension delivery |
| --- | --- | --- |
| Agent receives | Approved selected fields | Username plus a short-lived handle |
| Password enters agent input | Yes, only in the authenticated response | No |
| Extra install | None | Chrome extension and loopback bridge |
| Origin check | Grant and broker scope | Grant, broker scope, and active page origin |
| Best for | Agents that are allowed to handle the value | Lower exposure when a local bridge is preferred |
| What still needs a decision | Browser typing, submission, and external verification | Browser submission and external verification |

The extension is optional. Direct delivery is not a fallback for an extension
failure, and extension delivery is not an automatic upgrade from direct mode.
Choose one mode in the grant preview and request that same mode explicitly.

## The review-first flow

```mermaid
sequenceDiagram
  participant U as User or acting agent
  participant B as Agent Autofill broker
  participant E as Optional extension
  participant P as Active page

  U->>B: Match origin and username metadata
  B-->>U: Value-free candidates
  U->>B: Approve exact origin, purpose, fields, and mode
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

Agent Autofill ends at the information handoff. It does not navigate, click,
submit, or assert that an external site accepted the form. The CLI is the
canonical agent-facing surface; the dashboard only explains stored metadata
and policy.

## Direct delivery

First inspect safe metadata:

```bash
./scripts/agent-autofill --db ~/.agent-autofill.sqlite3 \
  credentials list --target-origin https://example.test
```

Preview a site grant and choose direct mode:

```bash
./scripts/agent-autofill --db ~/.agent-autofill.sqlite3 \
  grant-site codex https://example.test "account setup" \
  --delivery direct
```

The preview shows the matching site, username metadata, field IDs, origin,
purpose, and expiry. It does not display or persist a password. The eventual
`request_credential` call must name `delivery="direct"` and approved field IDs.

## Optional extension delivery

Preview the same site with the lower-exposure handoff:

```bash
./scripts/agent-autofill --db ~/.agent-autofill.sqlite3 \
  grant-site codex https://example.test "account setup" \
  --delivery extension
```

Start the loopback bridge:

```bash
./scripts/agent-autofill --db ~/.agent-autofill.sqlite3 fill-server
```

Then load `browser_extension/` as an unpacked extension when the optional bridge
is needed. The [extension guide](../browser_extension/README.md) documents the
transport boundary and recovery messages; the agent still obtains the handle
through the CLI/MCP path.

The bridge checks the page origin before consuming the handle. It consumes each
handle at most once, sends no secret to extension storage, and clears the
handle after the attempt. It fills username and password controls only; it
does not click or submit.

## OTP and other information

The same principle applies to email OTPs, verification links, profile fields,
and financial records: match metadata first, ask for the narrowest operation,
and let the acting agent decide what to do with the successful response. OTPs,
links, tokens, and handles are transient response data and never appear in
audits, listings, diagnostics, or docs.

## Safety checklist

Before a sensitive request, confirm:

- the target origin is exact and expected;
- the purpose describes the user-visible task;
- the selected field or account is the one you intend to use;
- the delivery mode matches your exposure preference;
- you know who will review and submit the external form.

For full grant rules and legacy operations, see the [operator guide](agent-autofill.md).
