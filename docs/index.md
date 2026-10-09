# Agent Autofill docs

<p align="center">
  <strong>✦ A calmer way to give agents the information they need ✦</strong><br>
  <em>Local memory. Selectable exposure. Human-controlled actions.</em>
</p>

Agent Autofill is an information layer for local agent workflows. The docs are
organized around the moment you are in, so you can start small and add sources
when a real task needs them.

> [!IMPORTANT]
> Agent Autofill returns information. The acting agent or user owns browser
> navigation, form submission, and post-action verification.

## Pick a path

<table>
<tr>
<td width="33%"><strong>① I am new</strong><br><a href="getting-started.md">Complete the first safe run</a><br><small>Install, inspect the inbox, and unlock the local vault.</small></td>
<td width="33%"><strong>② My agent needs information</strong><br><a href="access-modes.md">Choose direct or extension delivery</a><br><small>Use the CLI to request information with the exposure you choose.</small></td>
<td width="33%"><strong>③ I am integrating</strong><br><a href="cli.md">Use the CLI cookbook</a><br><small>Find the command and a copy-paste example.</small></td>
</tr>
</table>

## Guides at a glance

| Guide | Use it for | What you will leave with |
| --- | --- | --- |
| [Getting started](getting-started.md) | First install and first review | A value-free inventory and a clear next action |
| [Access modes](access-modes.md) | Credential and form workflows | A deliberate direct or extension handoff |
| [CLI cookbook](cli.md) | Daily operation | Safe status, source, grant, and server commands |
| [Troubleshooting](troubleshooting.md) | Recovery | A diagnosis and an exact retry path |
| [Operator guide](agent-autofill.md) | Full connector and MCP reference | Detailed setup, scopes, and compatibility notes |
| [Extension guide](../browser_extension/README.md) | Optional Chrome handoff | A one-time, origin-bound local fill |
| [Evaluation set](../evals/form_filling/README.md) | Regression scenarios | Repeatable form-filling checks without submission |

## The product in one picture

<p align="center">
  <img src="assets/security-model.svg" alt="Agent Autofill local security model" width="100%">
</p>

The first five steps are Agent Autofill's information path. The dashboard only
shows the safe inventory; the agent uses the CLI/MCP path for the request. The
last step is deliberately outside the broker: an agent or user decides whether
to type, submit, or stop after reviewing the page.

## Reference and contributor docs

- [Package and module guide](../agent_autofill/README.md)
- [Reusable local-consumer skill](../.agents/skills/agent-autofill/SKILL.md)
- [Chrome intake skill](../.agents/skills/agent-autofill-chrome-intake/SKILL.md)
- [Architecture and market notes](../RESEARCH.md)

When writing docs or examples, use synthetic values. Do not include real
passwords, OTPs, mailbox tokens, payment values, or one-time handles.
