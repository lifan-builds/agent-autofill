import pytest

from agent_autofill.inbox import build_html, serve
from agent_autofill.service import Broker
from agent_autofill.storage import Store


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
    assert "the CLI performs the information work" in page
    assert "CLI is the agent surface" in page
    assert "Consent and verification boundary" in page
    assert "not a reusable link" in page
    assert "does not navigate, submit, or verify the result after delivery" in page
    assert "dashboard" in page.lower()
    assert "Agent workflows stay in the CLI" in page
    assert "form-filling UI" not in page
