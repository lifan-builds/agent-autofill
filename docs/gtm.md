# Go-to-market and competitive brief

**Working position, 9 October 2026.** Agent Autofill is a **local personal
form-and-authentication broker for AI agents**. It gives an agent only the
profile field, verification code, financial field, or credential handoff that
the user approved for one origin and purpose. The user keeps custody of the
vault and decides what happens next.

The launch message is: **“Give your agent the exact information it needs—keep
the rest on your Mac.”** Lead with **macOS first; Linux and Windows coming
soon.** The strongest wedge is an agent that can prepare a real form or login
without receiving a standing password or a complete personal profile.

## Competitive map

| Option | Type | What it proves | Where Agent Autofill can win | Source / observed |
| --- | --- | --- | --- | --- |
| [Bitwarden Agent Access](https://github.com/bitwarden/agent-access) | Direct, open source (Apache-2.0) | Open protocol, CLI/SDK, JIT access, human approval, encrypted tunnel, process/environment injection; examples in Python, JavaScript, and Rust | Broader personal-data plane: profile fields, recent mailbox-read OTPs, Messages, financial records, provenance, and a local review surface | [Repo](https://github.com/bitwarden/agent-access), [announcement](https://bitwarden.com/blog/introducing-agent-access-sdk/), checked 2026-10-09 |
| [1Password Agentic Autofill](https://1password.dev/agentic-autofill/) | Direct, commercial credential UX | The safest credential primitive: agent requests use, user approves, trusted component fills without exposing the password | Neutral local broker for arbitrary agents plus reusable profile, email, OTP, and financial records; direct delivery remains advanced and extension handoff is the default password path | Product research, checked 2026-10-09 |
| xAI Grok Bot Vault (`v0.75.2+`) | Vertically integrated cloud bot vault | Validates the exact 3-pillar taxonomy (Logins, Cards, Contact Info), natural-language `Hints`, and JIT `Secure Form` intake | Local-first custody (no cloud vault), works across any MCP agent (Claude, Codex, Cursor, OpenClaw), per-origin/per-purpose grants, mandatory checkout confirmation gate, direct Keychain Chrome import (no plaintext CSV on disk), and scoped email/iMessage 2FA extraction | Release notes (`v0.75.2` / `v0.75.5`), checked 2026-11-05 |
| [Personal Context Protocol (`personal-vault-mcp`)](https://github.com/personal-context-protocol) | Open-source local profile MCP | Local SQLite MCP for identity/preferences with `allow/ask/deny` | Covers the full browser-agent loop: profile groups + passwords + payment cards + email/iMessage 2FA | Checked 2026-11-05 |
| Agent Vault Protocol (`agentvault`) | Open-source API secret proxy | `vault://` handle substitution for CLI/HTTP env vars | Built for web forms, Chrome onboarding, card suffix matching, and 2FA retrieval rather than backend API keys alone | Checked 2026-11-05 |
| [PersonalizationMCP](https://github.com/YangLiangwei/PersonalizationMCP) | Adjacent open source MCP | A personal-data hub and onboarding/status pattern | Stronger policy plane, encrypted local records, exact grants, provenance, and credential-use boundary | Repository, checked 2026-10-09 |
| [mail-mcp](https://github.com/tecnologicachile/mail-mcp) | Open-source connector | Multi-account IMAP/SMTP/EWS/Graph and OAuth coverage | One policy layer over email plus profile, OTP, credential, and Messages sources; no outbound mail in current scope | Repository, checked 2026-10-09 |
| [imsg](https://github.com/steipete/imsg) | Open-source macOS building block | Local Messages access | Separate read-only, scoped Messages adapter and shared grant/audit model | Repository, checked 2026-10-09 |
| [LangChain Agent Inbox](https://github.com/langchain-ai/agent-inbox) | Open-source approval UI | Human-in-the-loop approval can be a first-class agent surface | A neutral personal inbox that also owns source scope, origin, purpose, expiry, and revocation | Repository, checked 2026-10-09 |
| Composio / Nango / Arcade | Connector and governed-tool platforms | Integration breadth and managed OAuth | Local custody and browser/password/Messages boundary; complementary rather than substitutes | [Composio](https://composio.dev/), [Nango](https://nango.dev/), [Arcade](https://arcade.dev/), checked 2026-10-09 |

Bitwarden is the direct open-source reference. It is a parity target for
approval, scoped access, local/CLI/SDK ergonomics, and secretless handoff—not a
reason to claim identical binaries, tunnel implementation, or OS coverage.
The open-source landscape has useful pieces, but no maintained project found in
the survey combines the full Agent Autofill surface behind one policy layer.

### Parity audit

| Bitwarden Agent Access capability | Agent Autofill status | GTM treatment |
| --- | --- | --- |
| JIT, scoped credential access | Implemented through grants, field-selective reads, and one-time handles | Claim capability parity |
| Human approval | Implemented as review-first CLI/proposal flow; rich request cards are still a gap | Say “review-first”; finish the Inbox card before broad launch |
| Secretless handoff | Implemented through the optional origin-bound Chrome extension path | Make extension delivery the password default |
| Local authenticated transport | Implemented through authenticated JSON-lines/MCP and loopback bridge | Document the stable tool schema |
| Encrypted tunnel between remote agent and broker | Not implemented; current contract is local/loopback | Do not claim Bitwarden tunnel parity |
| Process/environment injection | Not implemented; browser form work uses scoped Agent Autofill data and the acting agent's confirmation policy | Keep out of launch claims |
| Python/JavaScript/Rust SDK examples | Python and JavaScript JSON-lines examples now exist; no Rust SDK | Lead with Python/JavaScript; defer Rust until a real consumer needs it |
| macOS/Linux/Windows binaries | macOS-first package path; Linux and Windows are coming soon | State platform limits in every launch asset |
| Local personal profile, email OTP, Messages, financial records | Additional product surface | Lead with this differentiation |

## Who to target first

1. **Privacy-conscious power users on Mac.** They already use Codex, Claude,
   Cursor, or local MCP servers and repeat travel, recruiting, support, and
   account-setup forms. Their first job is “fill this one thing safely,” not a
   universal assistant.
2. **MCP and agent-tool builders.** They need a small, understandable contract
   for profile fields, OTP, credential use, and provenance without asking users
   to paste secrets into prompts.
3. **Security-minded small teams.** They want a reviewable local companion for
   employee-owned Macs before they consider a hosted control plane.

Do not lead with “AI that can access everything.” Lead with one reviewed task:
“Approve legal name and shipping address for `example.test`, receive a
mailbox-scoped code, and keep the password out of model context.”

## Message hierarchy

| Layer | Message | Evidence in this repository |
| --- | --- | --- |
| Promise | Exact information for one task, under your policy | Origin/purpose/expiry grants and field-selective reads |
| Trust | The vault stays local and sensitive reads are reviewable | Encrypted SQLite, OS credential store, loopback Inbox, audits |
| Extra | More than a password manager | Profile, email OTP, Messages, financial records, provenance |
| Parity | JIT, human-approved, origin-bound credential use | Optional one-time Chrome handoff; direct selected fields |
| Boundary | Agent Autofill stops before external action | No navigation, submit, send, delete, purchase, or acceptance claim |
| Platform | macOS first | Keychain, Chrome, Messages, and current support matrix |

## Launch sequence

### Phase 0: trust and activation (now)

- Publish the install command, [interactive demo](demo/index.html), security
  policy, support matrix, and a short threat model.
- Make the synthetic path complete: `init` → `quickstart` → local Inbox → one
  configured profile/source → exact grant → delivery → revoke.
- Recruit 10–15 Mac users who run local agents. Measure time to first approved
  grant and capture every setup failure.

### Phase 1: design-partner release

- Invite MCP/tool authors and privacy-focused agent users.
- Support one canonical Mac workflow end to end: Chrome profile seed, one
  profile field, one recent mailbox-read OTP, and the extension handoff.
- Publish a 90-second screen recording or use the static demo as the default
  landing-page proof. Any recording must use synthetic data.

### Phase 2: public preview

- Ship signed macOS packaging and a versioned protocol/tool schema.
- Add copy-paste setup snippets for Codex, Claude, and Cursor, plus a Python
  and JavaScript example.
- Publish a comparison page that names Bitwarden and 1Password fairly and
  links to the support matrix.

### Phase 3: expansion

- Only after activation and recovery are reliable: add Linux/Windows support,
  richer connector health, backup/restore and key rotation, then team policy.
- Defer hosted SaaS pricing until local activation, retention, and support
  load justify a service. A free local core plus paid migration, support,
  backups, or connector packages is the safer first commercial hypothesis.

## Acquisition experiments

| Channel | Test | Leading signal | Guardrail |
| --- | --- | --- | --- |
| GitHub | README demo + “macOS first” release post | Demo starts → first grant | No claims beyond support matrix |
| MCP communities | A short “OTP without an OTP oracle” example | Qualified integrations and issue quality | Synthetic data only |
| Privacy/security communities | Threat-model walkthrough and local custody comparison | Design-partner replies | Do not imply audit/certification |
| Developer content | Bitwarden parity map and Python/JS snippets | Repo clones and setup completion | Link every vendor fact to a dated source |
| Product-led referral | Exportable redacted access receipt | Second workflow within 7 days | Never export values, tokens, or handles |

## Activation funnel and targets

Track these events locally or with explicit opt-in analytics; the default
product should not phone home.

| Funnel event | Preview target |
| --- | ---: |
| Demo completed through revoke | 60% of demo starters |
| Fresh install reaches `quickstart` | 70% of installers |
| First exact grant approved | 50% of installers |
| Second workflow within 7 days | 35% of first-grant users |
| First recovery issue resolved without data loss | 90% |
| Sensitive-read policy violation | 0 |

The north-star activation event is **one approved, origin-bound task completed
with no secret copied into agent input**. Measure this before downloads,
stars, or paid conversion.

## Gaps before GTM

These are release blockers or explicit positioning limits, not a reason to
weaken the product boundary:

1. **Distribution:** signed/installable macOS artifact, upgrade/uninstall path,
   and a versioned public protocol are still needed.
2. **Approval UX:** the current Inbox is primarily metadata/review. Add a
   clear request card with agent, source, exact fields, origin, purpose,
   expiry, delivery mode, allow/deny, and revoke. The form-memory skill now
   has a just-in-time missing-field check-in that can promote a stable answer
   into an exact proposal; the Inbox still needs a first-class visual card for
   that proposal before broad launch.
3. **Security verification:** add prompt-injection fixtures for hostile email,
   Messages, and web content; redaction tests for errors, dashboard, traces,
   and screenshots; and replay/wrong-origin/expiry tests.
4. **Recovery:** document encrypted backup/restore and key rotation before
   users trust the vault with more records.
5. **Coverage clarity:** either finish or exclude Outlook, rich MIME and
   attachments, iMessage setup/scoping, Chrome encryption-version support, and
   provider health from headline messaging.
6. **Integration surface:** publish stable MCP/tool schemas and small Python
   and JavaScript examples; make the extension delivery path the recommended
   password path and direct field delivery the advanced option.
7. **Evidence:** produce the synthetic walkthrough and a short screen recording
   before launch; do not use screenshots that contain real account data.

## What to say in ads

Use concrete, verifiable copy:

> Your agent asks for one field. You approve the scope. Agent Autofill returns
> the exact information—or a one-time Mac handoff—and stops before the website
> takes action. macOS first; Linux and Windows coming soon.

Avoid “autonomous autofill,” “zero-risk,” “works everywhere,” “secure by AI,”
and “verified submission.” The product is strongest when its stopping point is
visible.

## Research limits

Competitor capabilities, stars, pricing, and preview status change quickly.
The Bitwarden and open-source observations above are a dated snapshot for
strategy, not a warranty or security audit. Recheck vendor pages immediately
before publishing a comparison or paid campaign.
