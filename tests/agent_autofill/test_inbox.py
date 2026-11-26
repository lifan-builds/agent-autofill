# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Agent Autofill; Section 7 attribution terms in ATTRIBUTION.md.
import re
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from queue import Queue
from threading import Thread

import pytest

from agent_autofill.inbox import _grant_counts, build_html, make_server, serve
from agent_autofill.service import Broker
from agent_autofill.storage import Store


def test_grant_counts_preserve_invalid_expiry_and_revocation_handling():
    now = datetime.now(UTC)
    grants = [
        {"expires_at": (now + timedelta(hours=1)).replace(tzinfo=None).isoformat()},
        {"expires_at": (now + timedelta(days=2)).isoformat()},
        {"expires_at": (now - timedelta(hours=1)).isoformat()},
        {"expires_at": "invalid"},
        {},
        {"revoked": True, "expires_at": "invalid"},
    ]
    assert _grant_counts(grants) == {
        "active": 2, "revoked": 1, "expired": 3, "expiring_24h": 1,
    }


def test_inbox_page_contains_review_metadata_without_secrets():
    broker = Broker(Store(":memory:", key=b"i" * 32))
    broker.put_credential("c", "Example", "https://example.com", "ada", "secret")
    broker.put_financial_record(
        "card",
        "payment_card",
        "Primary card",
        {"card_number": "4111111111111111", "security_code": "fixture-cvc"},
    )
    page = build_html(broker)
    assert "Agent Inbox" in page and "Example" in page and "Primary card" in page
    assert "Sources" in page and "Credentials" in page and "Financial records" in page
    assert '"password"' not in page and "4111111111111111" not in page
    assert "security_code" in page and "fixture-cvc" not in page


def test_inbox_server_rejects_non_loopback_hosts():
    broker = Broker(Store(":memory:", key=b"h" * 32))

    with pytest.raises(ValueError, match="loopback"):
        serve(broker, host="0.0.0.0")


def test_inbox_http_requires_loopback_host_and_capability():
    ready = Queue()

    def run():
        store = Store(":memory:", key=b"h" * 32)
        server, capability = make_server(Broker(store), port=0)
        with server:
            ready.put((server, capability))
            server.serve_forever()
        store.db.close()

    thread = Thread(target=run, daemon=True)
    thread.start()
    server, capability = ready.get(timeout=5)
    port = server.server_port

    def request(path="/", headers=None):
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request("GET", path, headers=headers or {})
            with connection.getresponse() as response:
                return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    try:
        assert request()[0] == 403
        assert request("/?token=%C3%A9")[0] == 403
        assert request(headers={"X-Inbox-Token": "invalid"})[0] == 403
        for host in (f"foreign.test:{port}", "127.0.0.1:0", "127.0.0.1"):
            status, _, _ = request(headers={"Host": host, "X-Inbox-Token": capability})
            assert status == 403

        headers = {"X-Inbox-Token": capability}
        status, body, security_headers = request(headers=headers)
        assert status == 200 and b"Agent Inbox" in body
        assert security_headers["Cache-Control"] == "no-store"
        assert security_headers["Referrer-Policy"] == "no-referrer"
        assert security_headers["X-Content-Type-Options"] == "nosniff"
        assert security_headers["Content-Security-Policy"] == (
            "default-src 'none'; style-src 'unsafe-inline'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        status, _, _ = request("/missing", headers=headers)
        assert status == 404

        for host in ("127.0.0.1", "localhost", "[::1]"):
            status, _, _ = request(f"/?token={capability}", {"Host": f"{host}:{port}"})
            assert status == 200
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_inbox_overview_shows_profile_fields_and_attention_without_values():
    broker = Broker(Store(":memory:", key=b"p" * 32))
    broker.put_profile("legal_name", "identity", "Legal name", "private-value")

    page = build_html(broker)

    assert "Profile fields" in page
    assert "Legal name" in page
    assert "private-value" not in page
    assert "allowed_origins" in page
    assert "Needs attention" in page
    assert "Direct CLI delivery" in page
    assert "Optional bridge delivery" in page
    assert "LLM-guided record matching" in page
    assert "Rank · clarify · remember" in page
    assert "Value-free table" in page
    assert "the CLI performs the information work" in page
    assert "CLI is the agent surface" in page
    assert "Consent and verification boundary" in page
    assert "not a reusable link" in page
    assert "does not navigate, submit, or verify the result after delivery" in page
    assert "dashboard" in page.lower()
    assert "Agent workflows stay in the CLI" in page
    assert "form-filling UI" not in page


def test_dashboard_counts_all_records_and_labels_bounded_previews():
    broker = Broker(Store(":memory:", key=b"c" * 32))
    for index in range(105):
        key = f"synthetic_{index:03}"
        broker.put_profile(key, "contact", key, "synthetic-private-value")
        broker.put_credential(key, key, "https://example.test", "synthetic-user", "synthetic-password")
        broker.put_financial_record(key, "custom_financial", key, {"reference": "synthetic-private-financial"})
        broker.add_source(key, "email", "Synthetic mailbox")
    page = build_html(broker)
    for title in ("Sources", "Profile fields", "Credentials", "Financial metadata"):
        count = re.search(f'aria-label="{title}".*?card-count">(\\d+)', page)
        assert count and int(count[1]) == 105
    assert page.count("Showing 100 of 105") == 3
    assert "--offset 100" in page
    assert '"metadata_preview_limit": 100' in page.replace("&quot;", '"')
    for secret in ("synthetic-private-value", "synthetic-password", "synthetic-private-financial"):
        assert secret not in page


def test_dashboard_attention_navigation_and_complete_document():
    page = build_html(Broker(Store(":memory:", key=b"u" * 32)))
    assert page.index("Needs attention") < page.index("What is stored")
    for anchor in ("inventory", "next-task", "access"):
        assert f'href="#{anchor}"' in page
        assert f'id="{anchor}"' in page
    assert page.endswith("</body></html>")
