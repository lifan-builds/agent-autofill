import pytest
from agent_autofill.server import TOOLS
from agent_autofill.server import _call
from agent_autofill.service import Broker, BrokerError
from agent_autofill.storage import Store


def test_mcp_tool_catalog_has_information_only_surface():
    names = {tool["name"] for tool in TOOLS}
    assert {
        "request_profile_fields",
        "list_profile_groups",
        "match_credentials",
        "search_email",
        "request_auth_code",
        "request_auth_code_for_recipient",
        "match_email_sources",
        "request_verification_link_for_recipient",
        "search_messages",
        "list_financial_records",
        "match_financial_records",
        "write_records",
        "request_credential_fields",
        "request_credential",
        "request_financial_fields",
    } <= names
    assert not {"get_password", "send_message", "submit_form"} & names
    for name in {"request_credential_fields", "request_financial_fields"}:
        schema = next(tool["inputSchema"] for tool in TOOLS if tool["name"] == name)
        assert "confirmed" not in schema["properties"]
    unified = next(tool["inputSchema"] for tool in TOOLS if tool["name"] == "request_credential")
    assert unified["required"] == ["delivery", "credential_id"]
    assert unified["properties"]["delivery"]["enum"] == ["direct", "extension"]
    link_schema = next(
        tool["inputSchema"]
        for tool in TOOLS
        if tool["name"] == "request_verification_link_for_recipient"
    )
    assert link_schema["required"] == ["recipient", "target_origin", "purpose"]


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


def test_bound_local_service_uses_its_keychain_identity_without_request_credentials():
    from types import SimpleNamespace

    broker = Broker(Store(":memory:", key=b"l" * 32))
    broker.put_profile("name", "identity", "Name", "Ada")
    broker.grant("service:cpa-phoenix", "profile.read", "name", "example.com", "form")
    token = broker.create_agent("service:cpa-phoenix")

    result = _call(
        broker,
        "request_profile_fields",
        {
            "field_ids": ["name"],
            "target_origin": "example.com",
            "purpose": "form",
        },
        SimpleNamespace(agent_id="service:cpa-phoenix", token=token),
    )

    assert result["name"]["value"] == "Ada"


def test_transport_matches_credentials_with_auth_and_never_returns_password():
    broker = Broker(Store(":memory:", key=b"m" * 32))
    token = broker.create_agent("codex")
    broker.put_credential(
        "example-login",
        "Example",
        "https://example.test/login",
        "ada@example.test",
        "synthetic-password",
    )

    result = _call(
        broker,
        "match_credentials",
        {
            "agent_id": "codex",
            "agent_token": token,
            "target_origin": "https://example.test/checkout",
            "username_hint": "ADA",
        },
    )

    assert result[0]["credential_id"] == "example-login"
    assert "password" not in result[0]
    assert "synthetic-password" not in str(result)
    with pytest.raises(BrokerError, match="authentication"):
        _call(
            broker,
            "match_credentials",
            {
                "agent_id": "codex",
                "target_origin": "https://example.test",
            },
        )


def test_transport_dispatches_unified_direct_and_extension_with_authentication():
    broker = Broker(Store(":memory:", key=b"z" * 32))
    token = broker.create_agent("codex")
    broker.put_credential(
        "example-login",
        "Example",
        "https://example.test/login",
        "ada@example.test",
        "synthetic-password",
    )
    broker.grant("codex", "credential.read", "example-login", "example.test", "form")
    direct = _call(
        broker,
        "request_credential",
        {
            "agent_id": "codex",
            "agent_token": token,
            "delivery": "direct",
            "credential_id": "example-login",
            "field_ids": ["username"],
            "target_origin": "https://example.test",
            "purpose": "form",
        },
    )
    assert direct["username"] == "ada@example.test"
    assert "password" not in direct

    broker.grant("codex", "credential.use", "example-login", "example.test", "form")
    extension = _call(
        broker,
        "request_credential",
        {
            "agent_id": "codex",
            "agent_token": token,
            "delivery": "extension",
            "credential_id": "example-login",
            "target_origin": "https://example.test",
            "purpose": "form",
            "expires_in": 45,
        },
    )
    assert extension["expires_in"] == 45
    assert "password" not in extension

    with pytest.raises(BrokerError, match="authentication"):
        _call(
            broker,
            "request_credential",
            {
                "agent_id": "codex",
                "delivery": "direct",
                "credential_id": "example-login",
                "field_ids": ["username"],
                "target_origin": "https://example.test",
                "purpose": "form",
            },
        )


def test_transport_dispatches_email_matching_and_scoped_verification_link():
    from datetime import datetime, timezone
    from agent_autofill.models import EmailMessage

    broker = Broker(Store(":memory:", key=b"v" * 32))
    broker.add_source("mail", "email", "Owner")
    broker.store.put(
        "connection",
        "mail",
        {"source_id": "mail", "kind": "imap", "username": "owner@example.test"},
    )
    broker.email["mail"] = [
        EmailMessage(
            "link-1",
            "mail",
            "no-reply@auth.example.test",
            "Verify",
            "https://auth.example.test/verify?token=synthetic-link-token",
            datetime.now(timezone.utc).isoformat(),
        )
    ]
    token = broker.create_agent("codex")
    assert _call(
        broker,
        "match_email_sources",
        {"recipient": "owner@example.test"},
    )[0]["source_id"] == "mail"
    broker.grant(
        "codex",
        "email.verification_link",
        "mail",
        "auth.example.test",
        "verify",
    )
    result = _call(
        broker,
        "request_verification_link_for_recipient",
        {
            "agent_id": "codex",
            "agent_token": token,
            "recipient": "owner@example.test",
            "target_origin": "https://auth.example.test/finish",
            "purpose": "verify",
        },
    )
    assert result["url"].startswith("https://auth.example.test/")
    assert "synthetic-link-token" in result["url"]
    assert "Verify" not in str(result)
    with pytest.raises(BrokerError, match="authentication"):
        _call(
            broker,
            "request_verification_link_for_recipient",
            {
                "agent_id": "codex",
                "recipient": "owner@example.test",
                "target_origin": "auth.example.test",
                "purpose": "verify",
            },
        )


def test_profile_groups_are_available_without_token_but_values_are_not():
    broker = Broker(Store(":memory:", key=b"p" * 32))
    broker.put_profile("first_name", "identity", "First name", "Ada")

    result = _call(broker, "list_profile_groups", {"agent_id": "unknown"})

    assert result[0]["field_ids"] == ["first_name"]
    assert "Ada" not in str(result)


def test_transport_reads_agent_autofill_token_environment_variable(monkeypatch):
    broker = Broker(Store(":memory:", key=b"e" * 32))
    broker.put_profile("name", "identity", "Name", "Ada")
    broker.grant("codex", "profile.read", "name", "example.com", "form")
    monkeypatch.setenv("AGENT_AUTOFILL_AGENT_TOKEN", broker.create_agent("codex"))

    result = _call(
        broker,
        "request_profile_fields",
        {
            "agent_id": "codex",
            "field_ids": ["name"],
            "target_origin": "example.com",
            "purpose": "form",
        },
    )

    assert result["name"]["value"] == "Ada"


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
