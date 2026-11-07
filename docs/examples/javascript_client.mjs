// Metadata-only Agent Autofill JSON-lines example (Node 18+).
import { spawn } from "node:child_process";

const server = spawn("agent-autofill-server", [
  "--db",
  `${process.env.HOME}/.agent-autofill.sqlite3`,
]);

const request = {
  jsonrpc: "2.0",
  id: 1,
  method: "tools/call",
  params: {
    name: "list_profile_fields",
    arguments: {
      agent_id: "example-agent",
      agent_token: process.env.AGENT_AUTOFILL_AGENT_TOKEN,
      category: "identity",
      limit: 20,
    },
  },
};

server.stdout.setEncoding("utf8");
server.stdout.once("data", (line) => {
  // Metadata-only response. Do not print raw sensitive responses.
  console.log(JSON.stringify(JSON.parse(line), null, 2));
  server.kill();
});
server.stdin.write(`${JSON.stringify(request)}\n`);
