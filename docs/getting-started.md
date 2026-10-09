# Getting started

This path gets you from a clean checkout to a local, value-free inventory. It
does not require a mailbox, browser extension, or website credential.

## 1. Install and initialize

Agent Autofill supports Python 3.11 or newer. From the repository root, install
the command with `uv`:

```bash
uv tool install .
agent-autofill --db ~/.agent-autofill.sqlite3 init
```

`pipx install .` is an equivalent isolated install. The package exposes the
`agent-autofill` and `agent-autofill-server` commands, so the normal workflow
does not require a Python module invocation.

The database key is held by the operating-system credential store. The SQLite
file contains encrypted values and safe metadata; connector passwords and OAuth
payloads stay in the OS store.

## 2. Inspect before importing anything

```bash
agent-autofill --db ~/.agent-autofill.sqlite3 quickstart
agent-autofill --db ~/.agent-autofill.sqlite3 inbox-web
```

`quickstart` prints a value-free readiness summary. `inbox-web` starts a
loopback-only review page. The first screen is intentionally simple:

<table>
<tr>
<td width="50%"><strong>Stored</strong><br>Counts and metadata for profile fields, sources, credentials, financial records, agents, and grants.</td>
<td width="50%"><strong>Needs attention</strong><br>Keychain, source, expired-grant, or pairing checks that have a concrete next action.</td>
</tr>
</table>

The page does not reveal profile values, passwords, connector secrets, tokens,
or OTPs. Advanced disclosures expose metadata and redacted audit records only.
It is a dashboard for human review, not a form-filling UI. Agents continue
through the CLI/MCP commands after this check.

## 3. Run the guided setup

When the inventory looks right, run:

```bash
agent-autofill --db ~/.agent-autofill.sqlite3 setup
```

The wizard can help you add reviewed profile fields, import supported Chrome
metadata, configure read-only email or Messages sources, and create an agent
identity. It shows value-free proposals before writes. Skip any source you do
not need yet; the local store remains usable.

For a selected Chrome profile, keep the intake review-first:

> Help me bootstrap Agent Autofill from my selected Chrome profile. Organize
> the saved names, contact details, and addresses, ask me about ambiguities,
> then show all proposed changes for final approval before saving anything.

The [Chrome intake skill](../.agents/skills/agent-autofill-chrome-intake/SKILL.md)
excludes passwords, payments, OTPs, and arbitrary autocomplete history.

## 4. Check readiness and choose an agent

```bash
agent-autofill --db ~/.agent-autofill.sqlite3 doctor
agent-autofill --db ~/.agent-autofill.sqlite3 agents
```

If you need a new authenticated identity, create it once:

```bash
agent-autofill --db ~/.agent-autofill.sqlite3 agent-create codex
```

Keep the one-time token in your local secret manager or process environment.
Do not paste it into a repository, screenshot, issue, or documentation.

Keychain-paired local services can use trusted read paths. Legacy or remote
clients can use short-lived, origin-and-purpose-scoped grants. The [operator
guide](agent-autofill.md) has the complete grant reference.

## 5. Add only what the next task needs

Use metadata-first commands to see what is available:

```bash
agent-autofill --db ~/.agent-autofill.sqlite3 \
  profile list --category identity --limit 20

agent-autofill --db ~/.agent-autofill.sqlite3 \
  credentials list --target-origin https://example.test

agent-autofill --db ~/.agent-autofill.sqlite3 \
  sources
```

Then choose a delivery mode in [Access modes](access-modes.md). A value is
returned only after authentication, an exact scope, and the requested field or
operation are all present.

## Keychain recovery

If a command reports that the OS credential store is unavailable:

1. Open **Keychain Access** and unlock the `login` keychain.
2. Run `doctor` from a normal host process, not a restricted worker.
3. Follow the concrete `Fix:` command it prints.

```bash
agent-autofill --db ~/.agent-autofill.sqlite3 doctor
```

Do not move the database key, connector secret, service token, or OAuth
payload into a file or environment variable to bypass this check. See
[Troubleshooting](troubleshooting.md) for permission and migration cases.

## Next step

- Need to fill a login? Read [Access modes](access-modes.md).
- Need a command? Open the [CLI cookbook](cli.md).
- Need to repair setup? Open [Troubleshooting](troubleshooting.md).
