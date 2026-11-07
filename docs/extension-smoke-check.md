# Installed Chrome extension check

Status: automated broker and popup checks pass; this manual Chrome check is pending.
The browser tool cannot open Chrome's extension manager, so the user must perform
this check. Mocked JavaScript tests do not establish installed Chrome behavior.

From the repository root, run:

```sh
uv run python tests/manual/extension_smoke.py
```

The fixture uses synthetic credentials in memory. It does not open the real
vault, Keychain, mail, or Messages. It serves only on loopback ports 8766 and 8767.
If either port is occupied, stop the fixture and close your existing test bridge
before retrying. Do not terminate an unrelated process.

1. In Chrome's extension manager, load or reload `browser_extension/` and verify
   version **0.1.2**. Open its popup, generate a pairing code, and enter the
   extension ID and code into the fixture's terminal prompts. Do not share the
   code in chat. Set the bridge URL to `http://127.0.0.1:8766` if needed.
2. Open `http://127.0.0.1:8767`, refresh requests, and fill the **normal** request.
   Verify `synthetic@example.test` and `synthetic-only-password` appear in the
   fields. The page has no submission mechanism. Refresh requests again; the
   consumed request must disappear.
3. Select **navigation**, close the popup, and follow the different-origin link.
   Reopen the popup and refresh. No approved request should be offered, and no
   values should be filled. Return to the original origin; the unused request
   should still be available. Automated tests separately exercise navigation
   between preflight and delivery, which is difficult to trigger manually.
4. Within the first 30 seconds, select **expiry**, then wait past its deadline
   before clicking Fill. It must refuse without filling. If it already expired
   before selection, restart the fixture to retry. A refresh must omit it.
5. Fully quit Chrome and reopen it while the fixture is still running. Open the
   popup on the fixture page. Discovery must require pairing again. Restore the
   loopback bridge URL if necessary. Generate a fresh code and restart the
   fixture to verify re-pairing; no previous handles survive the restart.

Record the Chrome version, extension version, and pass/fail for each step.
Use only this synthetic page for screenshots. Stop the fixture with Ctrl-C.
The fixture itself can be checked without a browser using:

```sh
uv run python tests/manual/extension_smoke.py --check
```
