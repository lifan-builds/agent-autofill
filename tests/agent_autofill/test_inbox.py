import re

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
