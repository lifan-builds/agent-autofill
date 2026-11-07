# Agent Autofill optional bridge

For the synthetic installed-browser validation steps, see
[the extension smoke check](../docs/extension-smoke-check.md).

> **Platform:** macOS first. Linux and Windows support are coming soon.

The Agent Autofill dashboard is read-only, and the CLI/MCP path is the
canonical agent-facing interface. This optional Chrome component is only a
local transport helper for the lower-exposure credential mode; it is not a
second dashboard or a general form-filling UI.

1. Install Agent Autofill with `uv tool install .` (or `pipx install .`) from
   the repository root, then start the loopback bridge:

   ```bash
   agent-autofill \
     --db ~/.agent-autofill.sqlite3 fill-server
   ```

2. Open `chrome://extensions`, enable Developer mode, choose **Load unpacked**,
   and select this `browser_extension/` directory.
3. Open **Pair this browser session** in the popup and generate a pairing code.
   Run the displayed `agent-autofill pair-browser --agent-id YOUR_AGENT_ID
   --extension-id EXTENSION_ID` command, using the same database as the bridge.
   Paste the pairing code into its hidden terminal prompt, never into chat or
   command-line arguments. Pairing permits discovery of that agent's already
   approved login requests for eight hours. It creates no new credential grant.
4. On the target page, select **Refresh requests**, choose the account and
   purpose, and select **Fill selected login**. The agent still requests
   extension delivery through CLI/MCP; routine handle copying is unnecessary.
   The manual handle option remains available without pairing.

The pairing secret exists only in Chrome's extension session memory; the
broker stores an encrypted verifier. Restarting the browser or selecting
**Forget pairing on this browser** removes the local secret. Pair again to
replace the server verifier, or revoke the agent to stop its pending requests.
No pairing secret or handoff is saved in extension persistent storage.

The approved exact-origin and purpose grant is the consent boundary. The
one-time handle is a scoped verification handoff, not a reusable verification
link or proof that the site accepted the value. Review the populated page and
submit it yourself.

The extension checks for a visible, unambiguous existing-account login before
consuming a handle, then verifies origin and fields again at delivery. Hidden,
multiple-password, and password-creation forms require manual recovery. On a
username-first page, a paired request fills only the username and leaves the
handle unused; continue on the site and refresh at the password step.

Unused manual handles remain only in the open popup after preflight failures.
Once consumption starts, the popup clears them even on timeout because delivery
may have occurred. Request a fresh handoff in that case. The broker retains
unconsumed handoffs encrypted for paired discovery, rejects them after expiry,
and clears the handoff when consumed; they are never
included in audits or diagnostics. The extension never navigates, clicks, or
submits. Review the populated page before submitting.
