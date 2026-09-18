# Suggest Reusable Form Records Skill

## Goal

Give agents a repository-local StopAskingMe skill that notices stable values
encountered during form-filling work, compares them with existing profile
records, and proposes additions or updates for explicit user approval.

## Confirmed Current Behavior

- Profile fields are encrypted through `Store` and include a field id,
  category, display name, value, sensitivity, allowed origins, and allowed
  purposes.
- The CLI can set and list profile fields, while MCP can list metadata and read
  explicitly granted values. There is no suggestion model, approval workflow,
  or MCP profile-write operation.
- Official OpenAI documentation says Codex discovers repository skills under
  `.agents/skills`, implicitly matches them through the frontmatter
  description, and loads `SKILL.md` only when selected.

## Requirements

- Create one focused, implicitly discoverable repository skill under
  `.agents/skills` for StopAskingMe-assisted form memory.
- Trigger when an agent is preparing or reviewing form values and has stable
  user-provided information that could reduce future questions.
- Cover broad Chrome-style autofill categories: identity, contact, address,
  payment/financial, credentials, and other stable fields that have a clear
  record type.
- Use LLM semantic matching between form labels and saved-record metadata, but
  surface confidence, ambiguity, and source context instead of guessing.
- Support stable user-verified fields beyond Chrome's built-in taxonomy when
  they can be represented with category, sensitivity, provenance, and
  origin/purpose restrictions.
- Read existing field metadata before suggesting changes so proposals are
  deduplicated and classified as add, update, or no-op.
- Present a reviewable proposal containing field id/category, display name,
  source context, sensitivity, and optional origin/purpose restrictions. Show
  ordinary values exactly; show masked set/change previews for credentials and
  payment/financial values.
- Never silently persist a proposal. A user must explicitly approve the exact
  entries to save.
- After explicit chat approval, the agent may persist only the approved diff
  through one `write_records` MCP call. The broker must authenticate the agent
  and enforce the matching type-specific write grant for every entry, bound to
  the intended origin and purpose.
- Do not propose OTPs, access/refresh tokens, session cookies, credential
  handles, or inferred values that the user did not provide or verify. OTPs
  and session artifacts are never savable; passwords and
  payment/financial values may be proposed only as dedicated encrypted record
  types with explicit approval, never as ordinary profile fields.
- The skill may request raw saved values through scoped MCP tools when the
  consuming agent needs them for its own workflow. It must treat them as
  transient response data and never propose re-saving, echoing, logging, or
  retaining them in skill artifacts.
- Retrieval does not require a separate chat approval after agent
  authentication and grant authorization succeed. The consuming agent owns
  confirmation before typing or submitting values in a browser.
- Keep the skill concise and instruction-first; add scripts/references only if
  a concrete repeated mechanism requires them.

## Acceptance Criteria

- [x] Codex can implicitly select the skill for a realistic form-filling prompt
      and avoid selecting it for unrelated profile or email work.
- [x] Given new stable user-provided values, the skill produces a structured
      add/update proposal and identifies unchanged records as no-ops.
- [x] Given Chrome-style form labels, the skill maps supported categories and
      marks low-confidence or conflicting matches for review.
- [x] No record changes before explicit approval of the exact proposal.
- [x] The MCP write is denied when authentication, the applicable write grant,
      origin/purpose binding, `confirmed: true`, or a valid entry payload is
      missing.
- [x] Approved additions/updates are encrypted locally, survive a new process,
      and appear in metadata listings without exposing values.
- [x] Rejected, ignored, duplicate, transient-secret, and unverified-inference
      cases leave saved records unchanged.
- [x] OTPs are never proposed for storage; approved credentials and
      payment/financial values are routed to their dedicated encrypted record
      types rather than profile storage.
- [x] Credential/payment retrieval is available only through authenticated,
      narrowly scoped MCP operations with an active read grant; it adds no
      per-use approval step, and denied requests return no raw value.
- [x] The skill treats returned credentials/payment values as transient and
      does not copy them into suggestions, logs, files, or saved records.
- [x] The skill passes `quick_validate.py` and realistic forward tests in an
      isolated workspace, and broker/CLI changes pass project tests and Ruff.

## Out of Scope

- Reading values from the screen, controlling a browser, typing into or
  submitting forms; those capabilities belong to the consuming agent.
- Automatically deciding that a sensitive inferred value is true.
- Plugin packaging or public distribution; the first version is repository
  local.
