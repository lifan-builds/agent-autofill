# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Agent Autofill; Section 7 attribution terms in ATTRIBUTION.md.
import io
import json
import socket
import threading
import time
from http.client import HTTPConnection
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from agent_autofill.fill_server import (
    _json_response,
    consume_payload,
    make_server,
    pair_browser,
    pending_payload,
    serve,
)
from agent_autofill.service import Broker
from agent_autofill.storage import Store

_EXTENSION_ID = "a" * 32
_ORIGIN = "chrome-extension://" + _EXTENSION_ID
_PAIRING_TOKEN = "b" * 64


def _fill_fixture(*, pair: bool = True):
    broker = Broker(Store(":memory:", key=b"f" * 32))
    broker.create_agent("fill-helper")
    broker.put_credential(
        "example-login",
        "Example",
        "https://example.test/login",
        "ada@example.test",
        "synthetic-password",
    )
    broker.grant(
        "fill-helper",
        "credential.use",
        "example-login",
        "https://example.test",
        "login",
    )
    request = broker.request_credential_use(
        "fill-helper", "example-login", "https://example.test", "login"
    )
    if pair:
        pair_browser(broker, "fill-helper", _EXTENSION_ID, _PAIRING_TOKEN)
    return broker, request["handle"]


def test_fill_server_rejects_invalid_requests_without_consuming_handle():
    broker, handle = _fill_fixture()

    status, body = consume_payload(broker, b"{}", _ORIGIN)

    assert status == 400
    assert body["error"] == "invalid request"
    status, body = consume_payload(
        broker,
        json.dumps(
            {
                "pairing_token": _PAIRING_TOKEN,
                "handle": handle,
                "agent_id": "fill-helper",
                "target_origin": "https://example.test",
            }
        ).encode(),
        _ORIGIN,
    )
    assert status == 200
    assert body["password"] == "synthetic-password"


def test_fill_server_checks_origin_before_one_time_consumption():
    broker, handle = _fill_fixture()

    status, body = consume_payload(
        broker,
        json.dumps(
            {
                "pairing_token": _PAIRING_TOKEN,
                "handle": handle,
                "agent_id": "fill-helper",
                "target_origin": "https://evil.test",
            }
        ).encode(),
        _ORIGIN,
    )

    assert status == 403
    assert "synthetic-password" not in str(body)
    status, body = consume_payload(
        broker,
        json.dumps(
            {
                "pairing_token": _PAIRING_TOKEN,
                "handle": handle,
                "agent_id": "fill-helper",
                "target_origin": "https://example.test",
            }
        ).encode(),
        _ORIGIN,
    )
    assert status == 200
    assert body["username"] == "ada@example.test"

    status, body = consume_payload(
        broker,
        json.dumps(
            {
                "pairing_token": _PAIRING_TOKEN,
                "handle": handle,
                "agent_id": "fill-helper",
                "target_origin": "https://example.test",
            }
        ).encode(),
        _ORIGIN,
    )
    assert status == 403
    assert "already consumed" in body["error"]


def test_fill_server_is_loopback_only():
    broker = Broker(Store(":memory:", key=b"l" * 32))
    with pytest.raises(ValueError, match="loopback"):
        serve(broker, host="0.0.0.0")


class _ResponseStub:
    def __init__(self):
        self.headers = {}
        self.status = None
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers[name] = value

    def end_headers(self):
        pass


def test_fill_server_response_is_no_store_and_scoped_to_extension_origin():
    response = _ResponseStub()

    _json_response(
        response,
        200,
        {"username": "ada", "password": "synthetic-password"},
        "chrome-extension://fixture",
    )

    assert response.status == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert (
        response.headers["Access-Control-Allow-Origin"]
        == "chrome-extension://fixture"
    )
    assert json.loads(response.wfile.getvalue())["password"] == "synthetic-password"


def test_paired_discovery_requires_session_agent_and_matching_site():
    broker, handle = _fill_fixture(pair=False)
    extension_id = "a" * 32
    token = "b" * 64
    origin = "chrome-extension://" + extension_id
    payload = json.dumps({"pairing_token": token, "target_origin": "https://example.test"}).encode()
    assert pending_payload(broker, payload, origin)[0] == 403
    pair_browser(broker, "fill-helper", extension_id, token)
    assert pending_payload(broker, payload, "https://evil.test")[0] == 403
    wrong_token = json.dumps({"pairing_token": "c" * 64, "target_origin": "https://example.test"}).encode()
    assert pending_payload(broker, wrong_token, origin)[0] == 403
    wrong_site = json.dumps({"pairing_token": token, "target_origin": "https://evil.test"}).encode()
    assert pending_payload(broker, wrong_site, origin) == (200, {"requests": []})
    status, body = pending_payload(broker, payload, origin)
    assert status == 200 and body["requests"][0]["handle"] == handle
    assert body["requests"][0]["label"] == "Example"
    assert "synthetic-password" not in json.dumps(body)
    assert token not in str(broker.store.get("browser-pairing", extension_id))
    broker.revoke_agent("fill-helper")
    assert pending_payload(broker, payload, origin)[0] == 403
    status, body = consume_payload(
        broker,
        json.dumps({
            "pairing_token": token,
            "handle": handle,
            "agent_id": "fill-helper",
            "target_origin": "https://example.test",
        }).encode(),
        origin,
    )
    assert status == 403
    assert "synthetic-password" not in str(body)


def test_pairing_expiry_and_handle_consumption_remove_pending_requests():
    from datetime import UTC, datetime, timedelta

    broker, handle = _fill_fixture(pair=False)
    extension_id, token = "a" * 32, "b" * 64
    origin = "chrome-extension://" + extension_id
    pair_browser(broker, "fill-helper", extension_id, token)
    payload = json.dumps({"pairing_token": token, "target_origin": "https://example.test"}).encode()
    broker.consume_credential_handle(handle, "fill-helper", "https://example.test")
    assert pending_payload(broker, payload, origin) == (200, {"requests": []})
    pairing = broker.store.get("browser-pairing", extension_id)
    pairing["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    broker.store.put("browser-pairing", extension_id, pairing)
    assert pending_payload(broker, payload, origin)[0] == 403
    assert handle not in json.dumps(broker.store.audits())


def test_scope_changes_block_pending_discovery_and_consumption():
    broker, handle = _fill_fixture(pair=False)
    extension_id, token = "a" * 32, "b" * 64
    origin = "chrome-extension://" + extension_id
    pair_browser(broker, "fill-helper", extension_id, token)
    credential = broker.store.get("credential", "example-login")
    credential["allowed_purposes"] = ["different purpose"]
    broker.store.put("credential", "example-login", credential)
    payload = json.dumps({"pairing_token": token, "target_origin": "https://example.test"}).encode()
    assert pending_payload(broker, payload, origin) == (
        200, {"requests": []},
    )
    status, body = consume_payload(
        broker,
        json.dumps({
            "pairing_token": token,
            "handle": handle,
            "agent_id": "fill-helper",
            "target_origin": "https://example.test",
        }).encode(),
        origin,
    )
    assert status == 403
    assert "synthetic-password" not in str(body)


def test_consume_requires_live_paired_extension_origin_and_token():
    from datetime import UTC, datetime, timedelta

    broker, handle = _fill_fixture()
    base = {
        "pairing_token": _PAIRING_TOKEN,
        "handle": handle,
        "agent_id": "fill-helper",
        "target_origin": "https://example.test",
    }

    # Missing/invalid extension origin, missing/wrong token, wrong extension ID, mismatched agent_id.
    bad_cases = [
        (base, None),
        (base, "https://example.test"),
        (base, "chrome-extension://" + "c" * 32),
        ({k: v for k, v in base.items() if k != "pairing_token"}, _ORIGIN),
        ({**base, "pairing_token": "0" * 64}, _ORIGIN),
        ({**base, "agent_id": "other-agent"}, _ORIGIN),
    ]
    for req, origin in bad_cases:
        status, body = consume_payload(broker, json.dumps(req).encode(), origin)
        assert status in {400, 403}
        assert "synthetic-password" not in str(body)

    # Expired pairing must not consume handle.
    saved = broker.store.get("browser-pairing", _EXTENSION_ID)
    broker.store.put(
        "browser-pairing",
        _EXTENSION_ID,
        {**saved, "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
    )
    status, body = consume_payload(broker, json.dumps(base).encode(), _ORIGIN)
    assert status == 403
    assert "synthetic-password" not in str(body)
    broker.store.put("browser-pairing", _EXTENSION_ID, saved)

    # Original handle is still unconsumed and succeeds with paired extension + omitted agent_id.
    req_without_agent = {k: v for k, v in base.items() if k != "agent_id"}
    status, body = consume_payload(broker, json.dumps(req_without_agent).encode(), _ORIGIN)
    assert status == 200
    assert body["password"] == "synthetic-password"

    # Revoked agent must be rejected at pairing check before touching a fresh handle.
    fresh = broker.request_credential_use(
        "fill-helper", "example-login", "https://example.test", "login"
    )["handle"]
    broker.revoke_agent("fill-helper")
    status, body = consume_payload(
        broker,
        json.dumps({**base, "handle": fresh}).encode(),
        _ORIGIN,
    )
    assert status == 403
    assert body.get("code") == "revoked"
    assert "synthetic-password" not in str(body)


def test_fill_server_threading_avoids_head_of_line_blocking_and_times_out_stalled_client():
    broker, handle = _fill_fixture()
    server = make_server(broker, "127.0.0.1", 0, request_timeout=0.4)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stalled = socket.create_connection(("127.0.0.1", port), timeout=2.0)
    try:
        # Send incomplete POST body so this connection stalls.
        stalled.sendall(
            f"POST /v1/consume HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n".encode()
            + b"Content-Type: application/json\r\n"
            b"Content-Length: 100\r\n"
            b"Origin: " + _ORIGIN.encode() + b"\r\n\r\n{"
        )

        # While stalled is open, unpaired consume is rejected immediately without blocking.
        started = time.monotonic()
        unpaired_req = Request(
            f"http://127.0.0.1:{port}/v1/consume",
            data=json.dumps({
                "handle": handle,
                "agent_id": "fill-helper",
                "target_origin": "https://example.test",
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as denied:
            urlopen(unpaired_req, timeout=1.0)
        assert denied.value.code == 403

        # Valid paired consume also completes immediately without waiting for stalled client.
        paired_req = Request(
            f"http://127.0.0.1:{port}/v1/consume",
            data=json.dumps({
                "pairing_token": _PAIRING_TOKEN,
                "handle": handle,
                "agent_id": "fill-helper",
                "target_origin": "https://example.test",
            }).encode(),
            headers={"Content-Type": "application/json; charset=utf-8", "Origin": _ORIGIN},
            method="POST",
        )
        with urlopen(paired_req, timeout=1.0) as response:
            assert response.status == 200
            body = json.loads(response.read())
            assert body["password"] == "synthetic-password"
        elapsed = time.monotonic() - started
        assert elapsed < 0.35

        # Stalled client hits the server request timeout (408) instead of hanging indefinitely.
        stalled.settimeout(1.5)
        stalled_response = stalled.recv(4096)
        assert b"408" in stalled_response
    finally:
        stalled.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def test_bridge_validates_authority_and_media_type_before_reading_body():
    broker, _ = _fill_fixture()
    with make_server(broker, port=0, request_timeout=0.5) as server:
        port = server.server_port
        thread = threading.Thread(target=server.serve_forever)
        thread.start()

        def status(method, path, hosts, media_types):
            conn = HTTPConnection("127.0.0.1", port, timeout=2)
            try:
                conn.putrequest(method, path, skip_host=True)
                for host in hosts:
                    conn.putheader("Host", host)
                for media_type in media_types:
                    conn.putheader("Content-Type", media_type)
                conn.putheader("Content-Length", "2")
                conn.endheaders()
                return conn.getresponse().status
            finally:
                conn.close()

        try:
            local = f"localhost:{port}"
            for path in ("/v1/consume", "/v1/pending"):
                for hosts in ([], ["other.test"], ["localhost:0"], [local, local]):
                    for method in ("POST", "OPTIONS"):
                        assert status(method, path, hosts, ["application/json"]) == 403
                for types in ([], ["text/plain"], ["application/json", "text/plain"]):
                    assert status("POST", path, [local], types) == 415
            for host in ("LOCALHOST", "127.0.0.1", "[::1]"):
                assert status("OPTIONS", "/v1/consume", [f"{host}:{port}"], []) == 204
        finally:
            server.shutdown()
            thread.join()
