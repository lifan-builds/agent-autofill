# CLI cookbook

The CLI is designed to be readable in a terminal first and scriptable where a
value-free JSON projection exists. Set the database path once in a shell:

```bash
export AGENT_AUTOFILL_DB="$HOME/.agent-autofill.sqlite3"
alias af="agent-autofill --db $AGENT_AUTOFILL_DB"
```

The examples below use `af`; run `agent-autofill --help` to see all
subcommands. The installed console script is the single supported entry point.

## A small command map

| Need | Command |
| --- | --- |
| Create or open the encrypted store | `af init` |
| See the next safe action | `af quickstart` |
| Diagnose one task | `af doctor --task profile` (or `login`, `email`, `messages`) |
| Try a synthetic read, handoff, and revocation | `af demo` |
| Review the local inventory | `af inbox` or `af inbox-web` (dashboard only) |
| Add or edit a profile field | `af profile add <field>` / `af profile edit <field>` |
| List safe credential metadata | `af credentials list --target-origin <origin>` |
| List safe financial metadata | `af financial-records` |
| List configured sources | `af sources` |
| Inspect agents and grants | `af agents` / `af grants` |
| Start the MCP transport | `af serve --service <name>` |
| Start the optional fill bridge | `af fill-server` |
| Pair the extension session | `af pair-browser --agent-id <agent> --extension-id <extension>` |

## First run

```bash
af init
af quickstart
af inbox-web
af setup
af doctor
```

The first three commands are enough to inspect the product without adding a
source. `setup` is interactive and review-first; `doctor` can be rerun after
every change. The dashboard is for human review; agents use the CLI/MCP
commands below to match and request information.

## Value-free status for scripts

`quickstart` and `doctor` provide JSON projections that omit profile values,
passwords, connector secrets, tokens, and OTPs:

```bash
af quickstart --json
af doctor --json
```

Use the human-readable form during setup and the JSON form for local status
panels. Do not persist successful sensitive responses in script output.

## Profile and metadata

Add a profile field through a hidden prompt. The value is proposed for review
before it is encrypted:

```bash
af profile add legal_name
af profile edit legal_name
af profile list --category identity --limit 20
```

Use metadata before requesting a credential value:

```bash
af credentials list --target-origin https://example.test
af financial-records --kind payment_card --last4 1234
af financial-records --kind payment_card --query "Business Platinum" --card-suffix 51234 --expiration-year 2031
af email-match owner@example.test
```

`--card-suffix` accepts exactly four or five ending digits and compares them
inside the vault. Results contain masked metadata only. Combine name, suffix,
and expiry when saved cards share the same last four digits; multiple matches
remain ambiguous. Do not request a full card number merely to identify a card
already saved at checkout.

The list and match commands intentionally omit raw values.

## Website credential grants

Preview a matching credential and choose the delivery mode explicitly:

```bash
af grant-site codex https://example.test "account setup" --delivery direct
af grant-site codex https://example.test "account setup" --delivery extension
```

Use `direct` when the acting agent is allowed to receive selected fields. Use
`extension` when a local Chrome handoff should keep the password out of agent
input. The extension is optional; neither mode navigates or submits a page.

To avoid copying handles for each request, generate a pairing code in the
extension and run `pair-browser` with the same database as `fill-server`.
Paste the code only into the hidden local prompt. Pairing lasts eight hours
and exposes only approved requests for that agent and the active site's
origin. Use **Forget pairing** in the extension to clear its session code;
`af revoke-agent <agent>` also blocks future discovery and consumption.

## Email and source health

Configure a read-only IMAP source, test it, and list metadata:

```bash
af email-add personal-mail imap.example.test user@example.test --mailbox INBOX
af email-test personal-mail
af sources
```

Gmail and Thunderbird setup are documented in the [operator guide](agent-autofill.md).
Source secrets are stored in the operating-system credential store. Remove a
source explicitly when it is no longer needed:

```bash
af source-remove personal-mail
```

## Local MCP service

Pair a stable service identity and run the authenticated local transport:

```bash
af connect --service my-agent
af serve --service my-agent
```

The local service uses its Keychain-bound identity for trusted read paths.
Legacy or remote clients can use explicit short-lived grants. Keep any token
outside source control and process listings where practical.

Email OTP requests default to `target-origin`, requiring a target website and
matching its sender domain. `mailbox-read` is an explicit mode for mailbox-wide
selection; never use it as an automatic fallback. General
`search_email` and `get_email_message` calls remain read-only, account-scoped,
and bounded, without a target-domain filter.

`doctor` reports the package version, a source build fingerprint, package path,
and resolved Python executable, including when Keychain cannot open. Compare
these fields between `uv run agent-autofill doctor --json` and the installed
`agent-autofill doctor --json` when behavior differs. Update a local installation
with `uv tool install --force --no-cache .` from the reviewed repository.
If source files were removed, move aside the generated `build/` directory before
reinstalling: setuptools can otherwise retain deleted modules in the package.
After installation, rerun both doctor commands and compare their build fingerprints.

Profile, credential, and financial metadata lists accept `--offset` and
`--limit` (up to 100 per page). Continue at offset 100, 200, and so on until an
empty page. `doctor` inventory counts cover all saved records.

## Optional browser bridge

The browser bridge is separate from the MCP server:

```bash
af fill-server
```

It is loopback-only and accepts one-time origin-bound handles. It is an
optional transport helper, not a second agent UI. Use the [extension
guide](../browser_extension/README.md) for loading the unpacked extension and
understanding the bridge states.

## Human-friendly recovery

The CLI should leave you with one next action. If a command fails:

1. Rerun `af doctor`.
2. Read the `Fix:` line and run that command from a normal host process.
3. Retry with a fresh grant, handle, or connector check when the operation is
   one-time.

Do not retry a one-time OTP or extension handle. Request a fresh response after
the underlying issue is fixed. See [Troubleshooting](troubleshooting.md) for
common cases.
