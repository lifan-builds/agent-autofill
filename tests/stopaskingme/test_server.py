from stopaskingme import google_oauth
from stopaskingme.server import _register_connections
from stopaskingme.service import Broker
from stopaskingme.storage import Store


class MemoryKeyring:
    def __init__(self, values):
        self.values = values

    def get_secret(self, name):
        return self.values.get(name)


class FakeConnection:
    def __init__(self):
        self.auth_calls = []
        self.select_calls = []
        self.logout_calls = 0

    def authenticate(self, mechanism, callback):
        self.auth_calls.append((mechanism, callback(None)))
        return "OK", []

    def select(self, mailbox, readonly=False):
        self.select_calls.append((mailbox, readonly))
        return "OK", []

    def logout(self):
        self.logout_calls += 1
        return "BYE", []


def test_server_reconstructs_gmail_provider_and_refreshes_in_fresh_process(monkeypatch):
    store = Store(":memory:", key=b"s" * 32)
    provider = google_oauth.GoogleOAuthTokenProvider(
        "client-id",
        "client-secret",
        "refresh-token-fixture",
    )
    secret_name = "imap:v1:fixture"
    store.put(
        "connection",
        "personal",
        {
            "source_id": "personal",
            "kind": "imap",
            "auth_kind": "google_oauth",
            "label": "Personal Gmail",
            "host": "imap.gmail.com",
            "username": "user@gmail.test",
            "port": 993,
            "mailbox": "INBOX",
            "secret_name": secret_name,
        },
    )
    store.put("source", "personal", {"source_id": "personal", "kind": "email", "label": "Personal Gmail"})
    keyring = MemoryKeyring({secret_name: provider.to_keychain_secret()})
    broker = Broker(store)
    connection = FakeConnection()
    monkeypatch.setattr(
        "stopaskingme.imap_adapter.imaplib.IMAP4_SSL",
        lambda host, port: connection,
    )
    refresh_calls = []

    def refresh(endpoint, fields, timeout):
        refresh_calls.append((endpoint, dict(fields), timeout))
        return {"access_token": "refreshed-access-token", "expires_in": 3600}

    monkeypatch.setattr(google_oauth, "_post_form", refresh)
    _register_connections(broker, keyring)

    adapter = broker.email_adapters["personal"]
    adapter.check_connection()
    assert refresh_calls[0][1]["grant_type"] == "refresh_token"
    assert refresh_calls[0][1]["refresh_token"] == "refresh-token-fixture"
    assert connection.auth_calls == [
        (
            "XOAUTH2",
            b"user=user@gmail.test\x01auth=Bearer refreshed-access-token\x01\x01",
        )
    ]
    assert connection.select_calls == [("INBOX", True)]
    assert connection.logout_calls == 1
