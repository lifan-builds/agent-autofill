# Integration examples

These examples use the local JSON-lines/MCP transport and metadata-only calls.
They never print a password, OTP, connector secret, financial value, or agent
token. Keep `AGENT_AUTOFILL_AGENT_TOKEN` in the operating-system secret store
or process environment; do not commit it.

Run the server in another terminal first:

```bash
agent-autofill-server --db ~/.agent-autofill.sqlite3
```

- [Python](python_client.py) — standard-library JSON-lines client.
- [JavaScript](javascript_client.mjs) — Node 18+ JSON-lines client.

Both examples request `list_profile_fields`, which returns metadata only. A
real sensitive read must name the exact fields, origin, purpose, and active
grant, and should use the [access modes](../access-modes.md) guidance.
