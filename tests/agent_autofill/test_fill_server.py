import io
import json

import pytest

from agent_autofill.fill_server import (
    _json_response,
    consume_payload,
    pair_browser,
    pending_payload,
    serve,
)
from agent_autofill.service import Broker
from agent_autofill.storage import Store


def _fill_fixture():
    broker = Broker(Store(":memory:", key=b"f" * 32))
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
    return broker, request["handle"]


def test_fill_server_rejects_invalid_requests_without_consuming_handle():
    broker, handle = _fill_fixture()

    status, body = consume_payload(broker, b"{}")

    assert status == 400
    assert body == {"error": "invalid request"}
    status, body = consume_payload(
        broker,
        json.dumps(
            {
                "handle": handle,
                "agent_id": "fill-helper",
                "target_origin": "https://example.test",
            }
        ).encode(),
    )
    assert status == 200
    assert body["password"] == "synthetic-password"


def test_fill_server_checks_origin_before_one_time_consumption():
    broker, handle = _fill_fixture()

    status, body = consume_payload(
        broker,
        json.dumps(
            {
                "handle": handle,
                "agent_id": "fill-helper",
                "target_origin": "https://evil.test",
            }
        ).encode(),
    )

    assert status == 403
    assert "synthetic-password" not in str(body)
    status, body = consume_payload(
        broker,
        json.dumps(
            {
                "handle": handle,
                "agent_id": "fill-helper",
                "target_origin": "https://example.test",
            }
        ).encode(),
    )
    assert status == 200
    assert body["username"] == "ada@example.test"

    status, body = consume_payload(
        broker,
        json.dumps(
            {
                "handle": handle,
                "agent_id": "fill-helper",
                "target_origin": "https://example.test",
            }
        ).encode(),
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
    broker, handle = _fill_fixture()
    broker.create_agent("fill-helper")
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
    status, body = consume_payload(broker, json.dumps({
        "handle": handle, "agent_id": "fill-helper", "target_origin": "https://example.test",
    }).encode())
    assert status == 403
    assert "synthetic-password" not in str(body)


def test_pairing_expiry_and_handle_consumption_remove_pending_requests():
    from datetime import UTC, datetime, timedelta

    broker, handle = _fill_fixture()
    broker.create_agent("fill-helper")
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
    broker, handle = _fill_fixture()
    broker.create_agent("fill-helper")
    extension_id, token = "a" * 32, "b" * 64
    pair_browser(broker, "fill-helper", extension_id, token)
    credential = broker.store.get("credential", "example-login")
    credential["allowed_purposes"] = ["different purpose"]
    broker.store.put("credential", "example-login", credential)
    payload = json.dumps({"pairing_token": token, "target_origin": "https://example.test"}).encode()
    assert pending_payload(broker, payload, "chrome-extension://" + extension_id) == (
        200, {"requests": []},
    )
    status, body = consume_payload(broker, json.dumps({
        "handle": handle, "agent_id": "fill-helper", "target_origin": "https://example.test",
    }).encode())
    assert status == 403
    assert "synthetic-password" not in str(body)
