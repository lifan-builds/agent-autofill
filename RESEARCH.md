# Palisade: Agent Information Broker Research

**Research date:** 2026-09-12
**Purpose:** Basis for deciding whether to build or adopt a single agent-facing access point for email, personal information, credential use, and messages.

## Executive conclusion

The market has most of the underlying components, but no mature neutral product appears to combine them behind one user-owned interface for arbitrary external agents such as Codex. The strongest opportunity is a thin **personal authorization and approval plane**—an Agent Inbox or personal access firewall—above specialized providers.

The front door should hold agent identities, capability grants, routing rules, approval state, provenance, audit receipts, and revocation state. It should not become an everything database or collect every reusable secret. Gmail/Outlook tokens, password-manager vaults, Messages databases, and local documents should remain with separate custodians.

## The four product categories

1. **Integration fabrics:** Composio, Nango, Arcade, Pipedream Connect, Zapier MCP, and Pica expose many SaaS integrations through APIs or MCP. They solve connection, OAuth, token refresh, and tool execution. They do not by themselves provide a personal approval plane, Chrome-password access, or a Mac Messages bridge.
2. **Email infrastructure:** Nylas, Aurinko, MailMCP, and Gmail MCP access existing human mailboxes. AgentMail and Nylas Agent Accounts create new mailboxes owned by agents. These are complementary: historical personal context comes from human mail; future agent identity is better served by an agent-owned mailbox.
3. **Credential-use systems:** 1Password Agentic Autofill and 1Password for Claude demonstrate the safer primitive: the agent requests a login, the user approves it, and a trusted component fills the credential without returning the password to the model. This is materially safer than `get_password` tools.
4. **Messaging and context systems:** Beeper provides a local MCP/API path for messaging, including iMessage through a Mac-dependent integration. Mem0, Letta, Khoj, Personal AI, screenpipe, Pieces, and Limitless provide memory, documents, activity history, or context, but not a general personal-access broker.

## Requirement assessment

| Requirement | Best current evidence | Assessment |
|---|---|---|
| Existing Gmail/Outlook/iCloud/IMAP accounts | Nylas, Aurinko, MailMCP, Composio/Nango, Gmail MCP | Shipping, with provider and scope differences |
| Multiple accounts per user | Nylas, Composio, Nango, local Google MCP projects | Shipping |
| Personal documents/context | Connector platforms plus Mem0/Letta/Khoj/screenpipe/Pieces | Shipping, fragmented |
| Secretless website credential use | 1Password for Claude; Agentic Autofill/Browserbase early access | Shipping in constrained paths; no generic neutral broker |
| iMessage/SMS access | Beeper Desktop local MCP/API; Mac permissions required | Shipping, local and device-dependent |
| One API/MCP front door | Composio, Nango, Arcade, Pipedream, Zapier, Pica | Shipping for SaaS; incomplete for the whole personal-data stack |
| Cross-agent, cross-backend approval inbox | LangChain Agent Inbox, product-specific Muse/1Password approvals | Pieces exist; neutral personal consent layer is missing |

## Closest products

### Composio

Best developer-speed starting point for an MVP. It offers many connected accounts, MCP/API access, and managed token isolation. Its managed connected-account tokens are described as encrypted with AES-256-GCM and envelope encryption/KMS and unavailable through ordinary API responses. Human approval for consequential actions remains the application’s responsibility. It does not provide Chrome password retrieval or Messages access.

Source: [composio.dev](https://composio.dev/)

### Nango

Best foundation when control, portability, or self-hosting matters. It provides unified auth/integration infrastructure and MCP/API support. Cloud and self-hosted deployment have different feature availability; raw credential-retrieval paths must be kept behind the gateway and away from the agent.

Source: [nango.dev](https://nango.dev/)

### Arcade

Strong enterprise candidate when governed action execution, identity, and audit matter more than connector breadth. It supports permission-aware tools and deployment choices, but is not a browser-password or Messages broker.

Source: [arcade.dev](https://arcade.dev/)

### Nylas and Aurinko

Useful email-specific data planes. Nylas distinguishes connected human accounts from Agent Accounts, which are new Nylas-hosted mailboxes owned by an agent. Aurinko emphasizes unified mailbox access and pass-through processing. Put either behind the personal gateway; do not expose a powerful provider key directly to an external agent.

Sources: [Nylas](https://www.nylas.com/), [Aurinko](https://aurinko.io/)

### AgentMail

Production-oriented agent-owned email: API, SDK, MCP, threads, attachments, and real-time mechanisms. It is an optional clean-room identity for agents, not a solution for searching a user’s historical Gmail or Outlook.

Source: [AgentMail](https://www.agentmail.to/)

### Beeper

The clearest current messaging backend. Beeper Desktop exposes a local MCP/API for search, retrieval, and message actions and documents clients including Codex and Claude. iMessage support depends on a running Mac and macOS privacy permissions. Treat Beeper as a local backend, never as the central trust boundary.

Source: [Beeper](https://www.beeper.com/)

### 1Password Agentic Autofill

The strongest credential-use pattern found. The agent asks to use a credential for a stated purpose; the user approves; a trusted extension injects the secret into the website; the password and OTP stay out of model context. Current arbitrary-agent support is constrained, and Browserbase integration is described as Early Access. Generic SDK/CLI retrieval is a different and weaker model.

Source: [1Password Agentic Autofill](https://1password.dev/agentic-autofill/)

### Meta Muse

The closest consumer validation of the experience: connectors, email, persistent context, browser actions, a secure credential store, approvals, and audit history. It is a vertically integrated Meta assistant, not a documented neutral API for Codex or other arbitrary agents. 1Password integration and user-keyed Confidential VM capabilities were described as future features in the research.

Source: [Meta AI](https://ai.meta.com/)

### LangChain Agent Inbox

Strategically relevant open-source approval UI for LangGraph interrupts. It is an approval inbox, not an email inbox or personal-data broker, and remains tied to LangGraph deployments.

Source: [github.com/langchain-ai/agent-inbox](https://github.com/langchain-ai/agent-inbox)

## Important security distinctions

The system must distinguish these operations:

- **Retrieval:** return an email, document, message, or inferred context.
- **Credential brokering:** use a downstream token without returning it to the model.
- **Credential use:** authenticate or fill a website session without exposing the password.
- **Action:** draft, send, edit, delete, purchase, or otherwise change external state.

The preferred credential interface is:

```text
request_credential_use(site, account_hint, purpose)
```

It should never be:

```text
get_password(site)
```

Chrome Password Manager is a hard platform boundary. Chromium contains an internal `passwordsPrivate` capability, but it is restricted to component code rather than ordinary third-party extensions. A product should support user-mediated browser sessions or migration to an agent-capable password manager. It should not depend on covert Chrome-password extraction or plaintext CSV exports.

Retrieved email, webpages, documents, and messages are untrusted input. An instruction found in an email cannot change policy or authorize disclosure. Approval should be evaluated by a separate policy layer outside the model making the request.

## Proposed architecture

```text
External agents (Codex, Claude, workflows)
              |
      OAuth 2.1 + MCP/API
              |
  User access gateway / Agent Inbox
  - agent identity
  - capability grants
  - policy and approval queue
  - query routing and redaction
  - provenance and audit receipts
  - revocation and expiry
        /                 \
 Cloud connector plane    Local Mac companion
 Composio/Nango/Nylas      Beeper local MCP/API
 Gmail/Outlook/Drive       selected files
                           credential-use broker
```

The gateway should issue short-lived, scoped capabilities. A grant should encode:

```text
agent + user + purpose + operation + resource subset + destination + lifetime + constraints
```

Example: allow Codex to search personal Gmail receipts for one task, read one matching PDF, request Hilton credential use without seeing the password, and prepare an iMessage that requires a second approval before sending.

## Three paths

### Buy today

Use Composio for the fastest external-agent MVP; Arcade for stronger governed enterprise actions; Nylas/Aurinko for email quality; Beeper for local messaging; 1Password for supported credential-use flows. None delivers the complete neutral hub.

### Assemble a small stack

Build a thin gateway over Composio or Nango, add Nylas where email quality matters, a signed Mac companion for Beeper and selected local files, and a credential-use interface that never returns passwords. Keep the gateway database limited to identities, connection references, policy, approvals, and audit metadata.

### Build the differentiated product

Build the neutral personal authorization operating system: one endpoint for trusted agents, semantic retrieval instead of unrestricted source access, capability grants instead of standing master tokens, an approval inbox, data provenance, cross-provider audit receipts, policy learning, and independent revocation. Integration catalogs are becoming commodities; the defensible layer is authority and consent.

## Recommended MVP boundary

1. Connect multiple Gmail and Outlook accounts and optionally provision an agent-owned mailbox.
2. Add selected cloud documents and explicitly selected local files; avoid ambient capture by default.
3. Expose one OAuth-protected MCP endpoint compatible with Codex.
4. Allow policy-approved read/search operations; require approval for sensitive reveals, sends, destructive actions, and authentication.
5. Define `request_credential_use` from day one, initially supporting only zero-exposure or isolated-browser backends.
6. Add an optional Mac companion for Beeper/iMessage, disabled by default.
7. Store explicit preferences and source references before introducing a large derived-memory index.

## Evidence limits

This document is based on the completed September 12, 2026 Deep Research report and its 51 cited sources. Product counts, prices, availability, and early-access status can change quickly. Vendor security descriptions are vendor claims unless independently audited. The conclusion that no neutral product combines every requirement is an evidence-based market finding, not proof that a private or undocumented implementation does not exist.

## Focused follow-up: local profile + email codes + texts

The most promising direction is narrower than “give an agent access to everything.” It is a **local personal-data vault plus brokered retrieval and action**:

1. The user keeps structured profile data locally for repetitive forms: legal name, addresses, phone numbers, household members, travel preferences, employer details, and selected document references.
2. The agent requests only the fields or operation needed for one form. A local helper fills the browser; the model receives a success/failure result rather than a permanent copy of every field.
3. Email is connected separately for search and for narrowly scoped authentication-code retrieval. The agent receives a code only when the user has approved the specific login or task; the mailbox token remains with the email connector.
4. Texts are exposed through a local Mac bridge, initially read/search only. Sending, deleting, or accessing a new conversation requires a visible approval card.

This is closer to **1Password plus a local identity/profile vault and two message readers** than to a general API marketplace.

### Local user information and form filling

1Password is the closest existing security and UX reference because it already stores identities, secure notes, documents, and login items and can fill browser forms. Its newer agentic flows establish the important rule: the agent may request use of a credential, while a trusted component performs the fill and keeps the secret out of model context. The gap is that 1Password is primarily a password manager and its general arbitrary-agent profile-data API is not the same as a user-owned, local, field-level form broker.

Bitwarden, Proton Pass, Apple Passwords, and similar password managers can cover ordinary password and identity autofill, but their documented agent interfaces and policy semantics are less developed than 1Password’s Agentic Autofill pattern. They should be treated as possible vault backends, not assumed agent brokers.

For a new product, profile data should be a local encrypted record with field-level labels and purpose constraints. A form-fill request should include the requesting agent, target origin, fields, purpose, and lifetime. The local helper should show a diff such as “fill legal name and shipping address on example.com,” permit field-by-field denial, and return only completion metadata. Sensitive values should not be placed in prompts, logs, embeddings, screenshots, or long-lived agent memory.

### Email for context and authentication codes

For existing human mailboxes, Gmail/Microsoft Graph delegated OAuth, Nylas, Aurinko, MailMCP, and Google’s Gmail MCP are the relevant building blocks. They differ in custody and breadth, but all are fundamentally mailbox connectors rather than local personal vaults.

Authentication-code retrieval should be a distinct operation from general search. A safer interface is:

```text
request_auth_code(provider, account_hint, target_origin, purpose, expires_in)
```

The broker should verify that the code is recent, associated with the approved target, and not an arbitrary message selected by model-generated search terms. It should return the code once, redact it from logs, and expire it quickly. A generic `search_email` tool can still be useful for receipts and context, but it should not silently become an OTP oracle. Email content remains untrusted input and must not authorize a password reveal, external send, or permission change.

### Texts and iMessage

Beeper Desktop is the strongest practical current building block because it exposes a local MCP/API for search, retrieval, and message actions and can bridge iMessage through a Mac-dependent integration. Projects such as `imsg` and other local database readers demonstrate technical access, but macOS privacy permissions can be broad and are not equivalent to a narrow per-thread grant.

The local companion should therefore expose separate tools:

```text
search_messages(query, conversation_scope, time_window)
get_message(message_id)
request_message_send(conversation, rendered_text)
```

Read access can be policy-controlled and locally executed. Sending should always produce an approval card with the exact recipient, text, attachments, and source context. The remote agent should receive a short-lived capability or result, never an unrestricted local bearer token. The Mac must make outbound connections to the gateway; the gateway should not require an unauthenticated inbound port.

### What exists today

| Desired experience | Closest current solution | Remaining gap |
|---|---|---|
| Local profile vault + safe browser fill | 1Password identity/autofill; other password-manager identity stores | No neutral arbitrary-agent field broker with purpose and field-level grants |
| Agent uses login without seeing password | 1Password for Claude / Agentic Autofill; constrained Browserbase path | General Codex-compatible broker and broad provider support remain limited |
| Search multiple existing inboxes | Gmail MCP, Microsoft Graph, Nylas, Aurinko, Composio/Nango, MailMCP | Connector trust, semantic scopes, and OTP-specific controls need a separate gateway |
| Retrieve a sign-in code safely | Provider APIs plus a custom broker | Most platforms expose message search, not a verified target-bound OTP primitive |
| Search/read iMessage | Beeper Desktop local MCP/API; local Mac projects | Coarse macOS permissions, Mac availability, and local custody |
| Approve sends and fills in one place | 1Password approvals, LangChain Agent Inbox, Meta Muse approvals | No neutral cross-agent, cross-backend personal approval inbox |

### Refined product thesis

The product should be positioned as a **personal form-and-authentication broker** with an Agent Inbox, rather than a universal data lake. Its core objects are:

- a local encrypted profile vault;
- provider connections for email and messages;
- target-bound, short-lived capability grants;
- an out-of-band approval queue;
- browser/session capabilities for secretless credential use;
- provenance and audit receipts.

The first complete user path should be: “Fill a travel form, retrieve the verification code from the approved mailbox, and show me the final submission before sending.” The agent receives structured results and authenticated-session handles; it does not receive the complete profile, mailbox token, password, or unrestricted Messages access.

The product opportunity remains real because the existing products are optimized around different owners and trust boundaries: 1Password owns secrets, Gmail owns mail, Beeper owns a local messaging bridge, and integration fabrics own SaaS authorization. The missing layer is a user-owned policy and approval surface that composes them for arbitrary agents.

## GitHub survey: closest open-source building blocks

A GitHub repository search on 2026-09-12 found no obvious maintained project that already combines local profile vault, existing multi-account email, safe credential use, and iMessage behind one neutral agent endpoint. The useful projects are specialized:

| Repository | Evidence from GitHub | Fit |
|---|---|---|
| [agentscope-ai/QwenPaw](https://github.com/agentscope-ai/QwenPaw) | Self-hosted personal assistant supporting multiple chat apps and MCP; about 34.8k stars and updated recently | Closest general personal-assistant shell, but no verified vault/OTP/iMessage security plane |
| [Sylinko/Everywhere](https://github.com/Sylinko/Everywhere) | On-screen-aware desktop assistant using MCP tools; about 6.3k stars and recently updated | Useful desktop-agent UX; not a personal-data broker |
| [the-open-agent/openagent](https://github.com/the-open-agent/openagent) | Personal assistant with RAG, computer use, browser use, and coding-agent support; about 5.6k stars | Broad agent runtime; no verified secretless credential or multi-inbox custody model |
| [existence-master/Sentient](https://github.com/existence-master/Sentient) | Open-source personal AI assistant with memory/context/MCP; about 690 stars and recently updated | Potential local context layer, not an authorization gateway |
| [ThinkInAIXYZ/deepchat](https://github.com/ThinkInAIXYZ/deepchat) | Cross-platform personal assistant with MCP; about 6.3k stars | Client surface, not a secure personal access plane |
| [YangLiangwei/PersonalizationMCP](https://github.com/YangLiangwei/PersonalizationMCP) | Explicitly describes a unified personal data hub over MCP | Conceptually close, but small, limited integrations, and not verified for email/passwords/texts |
| [klemensgc/modular-context-obsidian-plugin](https://github.com/klemensgc/modular-context-obsidian-plugin) | Encrypted local-first context with Gmail and Google Calendar multi-account MCP; about 97 stars | Interesting local profile/context pattern; not credential use or Messages |
| [tecnologicachile/mail-mcp](https://github.com/tecnologicachile/mail-mcp) | Rust MCP server for IMAP/SMTP/EWS/Graph, Gmail/Hotmail/Zoho, OAuth2, and multi-account; updated recently | Strong email backend candidate; not local profile, safe OTP primitive, or texts |
| [navbuildz/gmail-mcp-server](https://github.com/navbuildz/gmail-mcp-server) | Multi-account Gmail MCP with read/write/archive/label tools | Useful Gmail connector; needs policy and OTP isolation |
| [cunicopia-dev/gmail-mcp](https://github.com/cunicopia-dev/gmail-mcp) | Multi-account Gmail MCP designed around read and draft, with prompt-injection concerns | Better default posture for a read/draft MVP; still Gmail-only |
| [langchain-ai/agent-inbox](https://github.com/langchain-ai/agent-inbox) | Maintained approval inbox for LangGraph human-in-the-loop interrupts | Best open-source approval-plane starting point; not a data connector |
| [imsg](https://github.com/steipete/imsg) | Local macOS Messages access through the Messages database/automation ecosystem | Useful local text backend; Mac permissions remain broad |

The GitHub evidence reinforces the product thesis: there are personal-assistant shells, email MCP servers, local-context systems, and approval UIs, but they do not converge on a user-owned capability broker. The most practical open-source composition is a local companion built from a maintained email MCP server, a narrow Messages adapter, an encrypted profile store, and an Agent Inbox-style approval service, all behind one policy gateway.
