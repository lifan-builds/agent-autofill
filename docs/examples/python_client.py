"""Metadata-only Agent Autofill JSON-lines example."""

import json
import os
import subprocess

request = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
        "name": "list_profile_fields",
        "arguments": {
            "agent_id": "example-agent",
            "agent_token": os.environ["AGENT_AUTOFILL_AGENT_TOKEN"],
            "category": "identity",
            "limit": 20,
        },
    },
}

server = subprocess.Popen(
    ["agent-autofill-server", "--db", os.path.expanduser("~/.agent-autofill.sqlite3")],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True,
)
assert server.stdin is not None
assert server.stdout is not None
server.stdin.write(json.dumps(request) + "\n")
server.stdin.flush()
response = json.loads(server.stdout.readline())
server.terminate()

# This is a metadata-only response. Do not print raw sensitive responses.
print(json.dumps(response, indent=2))
