from palisade.inbox import build_html
from palisade.service import Broker
from palisade.storage import Store


def test_inbox_page_contains_review_metadata_without_secrets():
    broker = Broker(Store(":memory:", key=b"i" * 32))
    broker.put_credential("c", "Example", "https://example.com", "ada", "secret")
    page = build_html(broker)
    assert "Agent Inbox" in page and "Example" in page and '"password"' not in page
