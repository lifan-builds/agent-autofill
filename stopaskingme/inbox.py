"""Small loopback-only review surface for the broker."""

from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from .service import Broker


def build_html(broker: Broker) -> str:
    summary = {
        "sources": broker.list_sources(),
        "profile_fields": broker.list_profile_fields(),
        "credentials": broker.list_credentials(),
        "agents": broker.list_agents(),
        "grants": broker.list_grants(),
        "recent_audit": broker.store.audits()[-50:],
    }
    body = escape(json.dumps(summary, indent=2, default=str))
    return (
        """<!doctype html><meta charset="utf-8"><title>Agent Inbox</title>
    <style>body{font:15px system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem;background:#f6f7f9;color:#202124}pre{background:white;padding:1rem;border-radius:8px;overflow:auto;box-shadow:0 1px 4px #ccd}h1{font-weight:600}</style>
    <h1>Agent Inbox</h1><p>Local review view. Values and secrets are intentionally omitted.</p><pre>"""
        + body
        + "</pre>"
    )


def serve(broker: Broker, host: str = "127.0.0.1", port: int = 8765) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/":
                self.send_error(404)
                return
            payload = build_html(broker).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            return

    HTTPServer((host, port), Handler).serve_forever()
