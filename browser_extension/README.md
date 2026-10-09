# Agent Autofill optional bridge

The Agent Autofill dashboard is read-only, and the CLI/MCP path is the
canonical agent-facing interface. This optional Chrome component is only a
local transport helper for the lower-exposure credential mode; it is not a
second dashboard or a general form-filling UI.

1. Start the loopback bridge from the repository root:

   ```bash
   PYTHONPATH=. python -m agent_autofill.cli \
     --db ~/.agent-autofill.sqlite3 fill-server
   ```

2. Open `chrome://extensions`, enable Developer mode, choose **Load unpacked**,
   and select this `browser_extension/` directory.
3. On the target HTTPS page, the optional bridge popup can show the active
   origin and bridge availability and accept the one-time handle returned by
   an approved `credential.use` request. The acting agent obtains that handle
   through the CLI/MCP path; review the origin before allowing the bridge to
   consume it.

The approved exact-origin and purpose grant is the consent boundary. The
one-time handle is a scoped verification handoff, not a reusable verification
link or proof that the site accepted the value. Review the populated page and
submit it yourself.

The bridge checks the current page origin before consuming the handle and
returns it only once. The extension fills username/password inputs and emits
normal `input`/`change` events. It clears the handle after every attempt and
never stores it. It never navigates, clicks, or submits the page. Review the
populated form and submit it yourself.
