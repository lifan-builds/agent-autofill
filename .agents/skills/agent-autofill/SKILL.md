---
name: agent-autofill
description: Use the installed local Agent Autofill broker as the default source for the user's saved profile, email, messages, website credentials, and financial records. Use for any workflow that needs information already registered in Agent Autofill.
---

# Agent Autofill

Use the installed Agent Autofill MCP service directly. A local service started
with a stable name such as `serve --service cpa-phoenix` pairs through the
operating-system credential store and receives trusted read access to the
registered information. The user should not need to create a token, run a
connect command, approve a grant, or copy a mailbox/source ID.

Use the broker's metadata tools first (`list_sources`, `list_profile_fields`,
`list_credentials`, `match_credentials`, `list_financial_records`, and
`match_financial_records`), then request only the fields needed for the task.
Email OTP reads must use the exact recipient operation and still include the
provider origin and workflow purpose so the code is bound to the right login.
For profile, credential, and financial field reads, target origin and purpose
may be omitted for a trusted local service; Agent Autofill records a local-read
provenance context automatically.

Agent Autofill remains information-only. Do not submit forms, send or delete
messages, change passwords, or expose secrets in logs, screenshots, reports,
or prompts. Keep OTPs, passwords, card numbers, and security codes only in the
active tool response needed to complete the authorized task. Writes to the
broker and external browser submissions still require the workflow's normal
user approval.

For website credentials, ask the user to choose the delivery mode when the
workflow needs one, then use the unified `request_credential` operation.
Direct mode passes `delivery="direct"` and only the approved `field_ids`, putting
only those values in the agent response. Extension mode passes
`delivery="extension"` and uses the optional local browser extension, which
fills the active page without putting the password in the agent input. The
broker refuses an omitted/unknown mode and never falls back between modes.
Prefer extension mode when minimizing agent exposure matters; do not install or
invoke it unless the user chooses that mode.

If the local service is unavailable, report the concrete Keychain, broker, or
source error. Do not ask the user to create ad-hoc grants or paste tokens as a
workaround. Legacy remote agents may use explicit grants, but that is not part
of the installed local skill path.
