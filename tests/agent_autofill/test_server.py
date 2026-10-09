from agent_autofill import google_oauth
from agent_autofill.server import _register_connections
from agent_autofill.service import Broker
from agent_autofill.storage import Store


class MemoryKeyring:
    def __init__(self, values):
        self.values = values

    def get_secret(self, name):
        return self.values.get(name)

    def set_secret(self, name, value):
        self.values[name] = value


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
        "agent_autofill.imap_adapter.imaplib.IMAP4_SSL",
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


def test_server_persists_rotated_microsoft_refresh_in_the_correct_keychain_item(monkeypatch):
    store = Store(":memory:", key=b"s" * 32)
    keyring = MemoryKeyring({})
    for source_id, provider_type in [
        ("outlook", google_oauth.MicrosoftOAuthTokenProvider),
        ("gmail", google_oauth.GoogleOAuthTokenProvider),
    ]:
        secret_name = f"imap:fixture:{source_id}"
        provider = provider_type("client-id", None, "fixture-original-refresh")
        keyring.values[secret_name] = provider.to_keychain_secret()
        store.put("connection", source_id, {
            "source_id": source_id, "kind": "imap",
            "auth_kind": f"{provider.KEYCHAIN_PROVIDER}_oauth",
            "host": "outlook.office365.com" if source_id == "outlook" else "imap.gmail.com",
            "username": "fixture@example.test", "secret_name": secret_name,
        })
    original_gmail = keyring.values["imap:fixture:gmail"]
    connection = FakeConnection()
    monkeypatch.setattr(
        "agent_autofill.imap_adapter.imaplib.IMAP4_SSL", lambda *_: connection
    )
    monkeypatch.setattr(google_oauth, "_post_form", lambda *_: {
        "access_token": "fixture-access", "refresh_token": "fixture-rotated-refresh"
    })
    broker = Broker(store)
    _register_connections(broker, keyring)
    broker.email_adapters["outlook"].check_connection()
    updated = keyring.values["imap:fixture:outlook"]
    assert b"fixture-rotated-refresh" in updated
    assert b"fixture-access" not in updated
    assert keyring.values["imap:fixture:gmail"] == original_gmail
    assert connection.select_calls == [("INBOX", True)]
