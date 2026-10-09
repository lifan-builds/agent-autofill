"""Loopback-only bridge for one-time browser credential filling.

The browser helper presents a short-lived handle obtained from the broker.
This process consumes it once and returns the credential only to the local
helper, never to the model-facing MCP response. It does not navigate, click,
or submit forms.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from .service import Broker, BrokerError


def _json_response(handler: BaseHTTPRequestHandler, status: int, body: dict[str, Any], origin: str | None) -> None:
    payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(payload)))
    if origin and origin.startswith("chrome-extension://"):
        handler.send_header("Access-Control-Allow-Origin", origin)
        handler.send_header("Vary", "Origin")
    handler.end_headers()
    handler.wfile.write(payload)


def consume_payload(broker: Broker, payload: bytes) -> tuple[int, dict[str, Any]]:
    """Validate and consume one browser-fill request without logging secrets.

    The returned success body intentionally contains the password because this
    function is called only by the loopback browser helper. Callers must keep
    it on the local fill path and must never forward it to an agent or logger.
    """

    if not isinstance(payload, bytes) or not 1 <= len(payload) <= 8192:
        return 400, {"error": "invalid request"}
    try:
        request = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return 400, {"error": "invalid request"}
    if not isinstance(request, dict):
        return 400, {"error": "invalid request"}
    handle = request.get("handle")
    agent_id = request.get("agent_id")
    target_origin = request.get("target_origin")
    if (
        not isinstance(handle, str)
        or not handle
        or not isinstance(agent_id, str)
        or not agent_id
        or not isinstance(target_origin, str)
        or not target_origin
    ):
        return 400, {"error": "invalid request"}
    try:
        result = broker.consume_credential_handle(
            handle, agent_id, target_origin=target_origin
        )
    except BrokerError as exc:
        return 403, {"error": str(exc)}
    return 200, result


def serve(broker: Broker, host: str = "127.0.0.1", port: int = 8766) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("fill-server only permits loopback hosts")

    class Handler(BaseHTTPRequestHandler):
        def do_OPTIONS(self):
            if self.path != "/v1/consume":
                self.send_error(404)
                return
            origin = self.headers.get("Origin")
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            if origin and origin.startswith("chrome-extension://"):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.end_headers()

        def do_POST(self):
            if self.path != "/v1/consume":
                self.send_error(404)
                return
            origin = self.headers.get("Origin")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length < 1 or length > 8192:
                _json_response(self, 400, {"error": "invalid request"}, origin)
                return
            status, result = consume_payload(broker, self.rfile.read(length))
            _json_response(self, status, result, origin)

        def log_message(self, *_):
            return

    HTTPServer((host, port), Handler).serve_forever()
