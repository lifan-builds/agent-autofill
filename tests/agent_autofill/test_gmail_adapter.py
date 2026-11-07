import base64
import io
import json
from datetime import UTC, datetime
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from agent_autofill import gmail_adapter
from agent_autofill.gmail_adapter import GmailAdapterError, GmailEmailAdapter
from agent_autofill.google_oauth import GoogleOAuthTokenProvider


def adapter():
    provider = GoogleOAuthTokenProvider("fixture", None, "refresh-fixture",
                                        access_token="access-fixture", expires_at=10**12)
    return GmailEmailAdapter("personal", "owner@example.test", provider)


def message(label="INBOX", age=0):
    return {"labelIds": [label], "internalDate": str(int((datetime.now(UTC).timestamp() - age) * 1000)),
            "raw": base64.urlsafe_b64encode(
                b"From: sender@example.test\r\nSubject: Test\r\nContent-Type: text/html\r\n\r\n<p>Fixture body</p>"
            ).decode().rstrip("=")}


def connect(monkeypatch, item, *, profile="owner@example.test", messages=None):
    calls = []
    def request(path, **params):
        calls.append((path, params))
        if path == "profile":
            return {"emailAddress": profile}
        if path == "labels":
            return {"labels": [{"id": "INBOX", "name": "INBOX"}]}
        if path == "messages":
            return {"messages": [{"id": "abc"}]}
        return messages or message()
    monkeypatch.setattr(item, "_request", request)
    return calls


def test_identity_check_does_not_read_mail_and_rejects_other_account(monkeypatch):
    item = adapter()
    calls = connect(monkeypatch, item, profile="different@example.test")
    with pytest.raises(GmailAdapterError, match="different account"):
        item.check_connection()
    assert [path for path, _ in calls] == ["profile"]
    calls = connect(monkeypatch, item)
    item.check_connection()
    assert [path for path, _ in calls] == ["profile", "labels"]


def test_search_and_get_enforce_mailbox_and_parse_html(monkeypatch):
    item = adapter()
    calls = connect(monkeypatch, item)
    results = item.search("from:sender@example.test")
    assert results[0].body == "Fixture body"
    assert results[0].account_id == "personal"
    assert calls[2][1]["labelIds"] == "INBOX"
    assert item.search("anything", limit=0) == []
    connect(monkeypatch, item, messages=message(label="SENT"))
    with pytest.raises(GmailAdapterError, match="outside"):
        item.get("abc")
    with pytest.raises(GmailAdapterError, match="Invalid"):
        item.get("../profile?token=private")


def test_recent_rejects_old_future_and_invalid_dates(monkeypatch):
    item = adapter()
    for age in (600, -600):
        connect(monkeypatch, item, messages=message(age=age))
        assert item.recent(300) == []
    payload = message()
    payload["internalDate"] = "private-invalid-value"
    connect(monkeypatch, item, messages=payload)
    with pytest.raises(GmailAdapterError) as caught:
        item.get("abc")
    assert "private-invalid-value" not in str(caught.value)


def test_get_only_transport_redacts_errors_and_rejects_redirects(monkeypatch):
    requests = []
    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            assert timeout == 30
            return io.BytesIO(json.dumps({"messages": []}).encode())
    monkeypatch.setattr(gmail_adapter, "build_opener", lambda handler: Opener())
    item = adapter()
    assert item._request("messages", q="private query") == {"messages": []}
    assert requests[0].method == "GET"
    assert urlsplit(requests[0].full_url).hostname == "gmail.googleapis.com"
    assert parse_qs(urlsplit(requests[0].full_url).query)["q"] == ["private query"]
    assert gmail_adapter._NoRedirect().redirect_request(None, None, 302, None, None, "https://evil.test") is None
    def fail(*args, **kwargs):
        raise HTTPError("private-url", 403, "secret-body", {}, None)
    monkeypatch.setattr(Opener, "open", fail)
    with pytest.raises(GmailAdapterError) as caught:
        item._request("profile")
    assert "secret" not in str(caught.value)
    assert "private" not in str(caught.value)


def test_pagination_is_bounded(monkeypatch):
    item = adapter()
    item._label_id = "INBOX"
    pages = []
    def request(path, **params):
        if path != "messages":
            return message()
        pages.append(params)
        return {"messages": [{"id": "abc"}], "nextPageToken": "next"} if len(pages) == 1 else {"messages": [{"id": "def"}]}
    monkeypatch.setattr(item, "_request", request)
    assert [msg.message_id for msg in item.search("", limit=2)] == ["abc", "def"]
    assert pages[1]["maxResults"] == 1
    assert pages[1]["pageToken"] == "next"


def test_saved_api_source_registers_and_matches_exact_recipient(monkeypatch):
    from agent_autofill import cli_connectors, server
    from agent_autofill.service import Broker
    from agent_autofill.storage import Store

    broker = Broker(Store(":memory:", key=b"g" * 32))
    class Keyring:
        def __init__(self):
            self.values = {}
        def get_secret(self, key):
            return self.values[key]
        def set_secret(self, key, value):
            self.values[key] = value
        def delete_secret(self, key):
            self.values.pop(key, None)
    keyring = Keyring()
    monkeypatch.setattr(GmailEmailAdapter, "check_connection", lambda self: None)
    config = cli_connectors._configure_gmail(
        broker, "/tmp/synthetic.sqlite", "personal", "owner@example.test", "",
        oauth_provider=adapter().oauth_provider, keyring=keyring,
    )
    assert config["kind"] == "gmail"
    assert cli_connectors._test_saved_imap(broker, "personal", keyring) == config
    registered = []
    monkeypatch.setattr(broker, "register_email_adapter", lambda *args: registered.append(args))
    server._register_connections(broker, keyring)
    assert isinstance(registered[0][1], GmailEmailAdapter)
    assert broker.match_email_sources("owner@example.test")[0]["source_id"] == "personal"
    assert broker.match_email_sources("another@example.test") == []


def test_failed_account_check_preserves_existing_source(monkeypatch):
    from agent_autofill import cli_connectors
    from agent_autofill.service import Broker
    from agent_autofill.storage import Store

    broker = Broker(Store(":memory:", key=b"h" * 32))
    original = {"source_id": "personal", "kind": "imap", "secret_name": "old"}
    broker.store.put("connection", "personal", original)
    def fail(self):
        raise GmailAdapterError("Google signed in to a different account")
    monkeypatch.setattr(GmailEmailAdapter, "check_connection", fail)
    with pytest.raises(SystemExit, match="different account"):
        cli_connectors._configure_gmail(broker, "/tmp/synthetic.sqlite", "personal",
                                       "owner@example.test", "", oauth_provider=adapter().oauth_provider)
    assert broker.store.get("connection", "personal") == original
