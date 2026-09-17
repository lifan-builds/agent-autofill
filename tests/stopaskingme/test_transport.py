import pytest
from stopaskingme.server import TOOLS
from stopaskingme.server import _call
from stopaskingme.service import Broker, BrokerError
from stopaskingme.storage import Store


def test_mcp_tool_catalog_has_information_only_surface():
    names = {tool["name"] for tool in TOOLS}
    assert {
        "request_profile_fields",
        "search_email",
        "request_auth_code",
        "search_messages",
        "list_financial_records",
        "write_records",
        "request_credential_fields",
        "request_financial_fields",
    } <= names
    assert not {"get_password", "send_message", "submit_form"} & names
    for name in {"request_credential_fields", "request_financial_fields"}:
        schema = next(tool["inputSchema"] for tool in TOOLS if tool["name"] == name)
        assert "confirmed" not in schema["properties"]


def test_transport_requires_agent_token_for_data_tools():
    broker = Broker(Store(":memory:", key=b"x" * 32))
    broker.put_profile("name", "identity", "Name", "Ada")
    broker.grant("codex", "profile.read", "name", "example.com", "form")
    token = broker.create_agent("codex")
    assert (
        _call(
            broker,
            "request_profile_fields",
            {
                "agent_id": "codex",
                "agent_token": token,
                "field_ids": ["name"],
                "target_origin": "example.com",
                "purpose": "form",
            },
        )["name"]["value"]
        == "Ada"
    )
    with pytest.raises(BrokerError):
        _call(
            broker,
            "request_profile_fields",
            {
                "agent_id": "codex",
                "agent_token": "wrong",
                "field_ids": ["name"],
                "target_origin": "example.com",
                "purpose": "form",
            },
        )


def test_transport_authenticates_record_write_and_scoped_financial_read():
    broker = Broker(Store(":memory:", key=b"t" * 32))
    token = broker.create_agent("codex")
    broker.grant("codex", "financial.write", "primary-card", "example.com", "form")
    broker.grant("codex", "financial.read", "primary-card", "example.com", "form")
    with pytest.raises(BrokerError, match="authentication"):
        _call(
            broker,
            "write_records",
            {
                "agent_id": "codex",
                "target_origin": "example.com",
                "purpose": "form",
                "confirmed": True,
                "entries": [],
            },
        )
    _call(
        broker,
        "write_records",
        {
            "agent_id": "codex",
            "agent_token": token,
            "target_origin": "example.com",
            "purpose": "form",
            "confirmed": True,
            "entries": [
                {
                    "record_type": "financial",
                    "record_id": "primary-card",
                    "kind": "payment_card",
                    "display_name": "Primary card",
                    "values": {"card_number": "4111111111111111"},
                }
            ],
        },
    )
    result = _call(
        broker,
        "request_financial_fields",
        {
            "agent_id": "codex",
            "agent_token": token,
            "record_id": "primary-card",
            "field_ids": ["card_number"],
            "target_origin": "example.com",
            "purpose": "form",
        },
    )
    assert result["card_number"] == "4111111111111111"


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("list_financial_records", {}),
        (
            "request_credential_fields",
            {
                "credential_id": "login",
                "field_ids": ["password"],
                "target_origin": "example.com",
                "purpose": "form",
            },
        ),
        (
            "request_financial_fields",
            {
                "record_id": "card",
                "field_ids": ["card_number"],
                "target_origin": "example.com",
                "purpose": "form",
            },
        ),
    ],
)
def test_new_sensitive_tools_reject_unauthenticated_requests(name, params):
    broker = Broker(Store(":memory:", key=b"u" * 32))
    with pytest.raises(BrokerError, match="authentication"):
        _call(broker, name, {"agent_id": "codex", **params})
