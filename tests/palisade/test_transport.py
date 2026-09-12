import pytest
from palisade.server import TOOLS
from palisade.server import _call
from palisade.service import Broker, BrokerError
from palisade.storage import Store


def test_mcp_tool_catalog_has_information_only_surface():
    names = {tool["name"] for tool in TOOLS}
    assert {
        "request_profile_fields",
        "search_email",
        "request_auth_code",
        "search_messages",
    } <= names
    assert not {"get_password", "send_message", "submit_form"} & names


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
