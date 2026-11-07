# Getting started

> **Platform:** macOS first. Linux and Windows support are coming soon.

Want to see the flow first? Open the [synthetic interactive demo](demo/index.html).
It is dependency-free and never reads your local data.

Choose one first task after installation:

| I want to… | Start here | What you need |
| --- | --- | --- |
| Reuse an address or other ordinary field | `agent-autofill doctor --task profile` | One saved field and an active agent |
| Fill an existing website login | `agent-autofill doctor --task login` | One credential, an authorized request, and the optional extension for browser delivery |
| Retrieve email or a recent code | `agent-autofill doctor --task email` | The selected read-only mailbox and an active agent |
| Read selected Messages | `agent-autofill doctor --task messages` | The selected local Messages source and an active agent |

Readiness checks local prerequisites; it does not approve access or prove a
website accepted anything. Add only the data needed for the selected task.
Expired grants and revoked identities remain visible as history.

Run `agent-autofill demo` to exercise an authenticated profile read, a one-time
credential handoff, and revocation using a synthetic memory-only broker. This
checks the same tool dispatch boundary an agent uses without connecting to
your real vault or filling a browser page.

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
<td width="50%"><strong>Needs attention</strong><br>Unavailable configured sources or a missing active agent. Optional data and normal access expiry are not setup errors.</td>
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

The wizard can configure an agent identity, read-only email or Messages
sources, and Chrome's `Default` profile. Skip any source you do not need yet;
the local store remains usable. The Chrome intake keeps ambiguous groups held
until the acting agent asks its classification questions and you approve the
exact rows to save.

For the recommended ambiguity-aware Chrome seed, use the dedicated
[Chrome seed onboarding guide](chrome-seed-onboarding.md). It keeps source
groups intact, asks the agent to consult the classification guide, and holds
unclear fields for a human answer before the final save review.

For a selected Chrome profile, keep the intake review-first:

> Help me bootstrap Agent Autofill from my selected Chrome profile. Organize
> the saved autofill names, contact details, and addresses, ask me about
> ambiguities, then show all proposed changes for final approval before saving
> anything. Separately ask whether I want saved passwords imported to the local
> encrypted vault only.

The [Chrome intake skill](../.agents/skills/agent-autofill-chrome-intake/SKILL.md)
keeps password import separate and opt-in. It never handles payments, OTPs, or
arbitrary autocomplete history.

## When a form needs a new field

When correcting a saved choice, choose **use once** for today's form,
**remember for this site and purpose** for future matching, or **replace the
saved value** to review an update to the reusable record. Remembering a choice
does not silently overwrite the record. The agent announces a remembered choice
when reusing it, so you can correct it before submission.

The form-memory skill uses a just-in-time check-in so the agent does not ask
the same question twice. It asks for the missing value and offers **use once**,
**save to Agent Autofill**, or **skip**. Once the value is supplied,
the agent shows one compact card containing the field id, category, display
name, exact value, source, sensitivity, reusable saved-record scope, current
origin/purpose scope, confidence, and ambiguity. If needed, the same card
includes the exact narrow `profile.write` scope for this save. **Save to Agent
Autofill** is the single approval for both the reusable row and that grant; the
agent obtains the grant and sends only that entry to `write_records` with
`confirmed: true`, without a second proposal prompt.

The saved record is available for later authorized use at other sites and
purposes; each read still requires the consuming workflow's matching checks.
Never save an OTP, password, payment value, token, handle, CAPTCHA answer,
session value, or inferred page value through this path. Saving the field does
not approve the final form submission; the acting agent still applies the
submission policy immediately before clicking submit.

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
