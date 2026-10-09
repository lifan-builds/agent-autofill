import io
import json

import pytest

from agent_autofill.fill_server import _json_response, consume_payload, serve
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
