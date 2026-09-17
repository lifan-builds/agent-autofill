from stopaskingme.inbox import build_html
from stopaskingme.service import Broker
from stopaskingme.storage import Store


def test_inbox_page_contains_review_metadata_without_secrets():
    broker = Broker(Store(":memory:", key=b"i" * 32))
    broker.put_credential("c", "Example", "https://example.com", "ada", "secret")
    broker.put_financial_record(
        "card",
        "payment_card",
        "Primary card",
        {"card_number": "4111111111111111", "security_code": "123"},
    )
    page = build_html(broker)
    assert "Agent Inbox" in page and "Example" in page and "Primary card" in page
    assert '"password"' not in page and "4111111111111111" not in page
    assert "security_code" in page and "123" not in page
