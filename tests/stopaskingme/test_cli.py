import pytest

from stopaskingme import cli
from stopaskingme.imap_adapter import IMAPAuthenticationError
from stopaskingme.service import Broker
from stopaskingme.storage import Store


class MemoryKeyring:
    def __init__(self):
        self.values = {}
        self.deleted = []

    def set_secret(self, name, value):
        self.values[name] = value

    def get_secret(self, name):
        return self.values.get(name)

    def delete_secret(self, name):
        self.deleted.append(name)
        self.values.pop(name, None)


class FakeAdapter:
    instances: list["FakeAdapter"] = []
    check_error = None

    def __init__(self, *args):
        self.args = args
        self.__class__.instances.append(self)

    def check_connection(self):
        if self.__class__.check_error:
            raise self.__class__.check_error


class FakeOAuthProvider:
    def __init__(self):
        self.access_token = "access-token-fixture"

    def get_access_token(self):
        return self.access_token

    def to_keychain_secret(self):
        return (
            b'{"version":1,"provider":"google","client_id":"client-id",'
            b'"refresh_token":"refresh-token-fixture"}'
        )


class ExtraFieldOAuthProvider(FakeOAuthProvider):
    def to_keychain_secret(self):
        return (
            b'{"version":1,"provider":"google","client_id":"client-id",'
            b'"refresh_token":"refresh-token-fixture",'
            b'"access_token":"access-token-fixture"}'
        )


def _broker():
    return Broker(Store(":memory:", key=b"c" * 32))


def test_configure_imap_checks_before_persisting_and_uses_versioned_key(monkeypatch):
    monkeypatch.setattr(cli, "IMAPEmailAdapter", FakeAdapter)
    FakeAdapter.instances = []
    FakeAdapter.check_error = None
    broker = _broker()
    keyring = MemoryKeyring()

    config = cli._configure_imap(
        broker,
        "/tmp/stopaskingme.sqlite",
        "personal",
        "imap.example.test",
        "user@example.test",
        993,
        "INBOX",
        "fixture-password",
        keyring=keyring,
    )

    assert len(FakeAdapter.instances) == 1
    assert config["auth_kind"] == "password"
    assert config["secret_name"].startswith("imap:v1:")
    assert keyring.values[config["secret_name"]] == b"fixture-password"
    assert broker.store.get("connection", "personal") == config
    assert broker.store.get("source", "personal")["kind"] == "email"
    raw = broker.store.db.execute("select value from kv").fetchall()
    assert "fixture-password" not in str(raw)


def test_configure_imap_replacement_cleans_old_secret_after_commit(monkeypatch):
    monkeypatch.setattr(cli, "IMAPEmailAdapter", FakeAdapter)
    FakeAdapter.check_error = None
    broker = _broker()
    keyring = MemoryKeyring()
    first = cli._configure_imap(
        broker,
        "/tmp/stopaskingme.sqlite",
        "personal",
        "imap.example.test",
        "user@example.test",
        993,
        "INBOX",
        "first-fixture-password",
        keyring=keyring,
    )
    second = cli._configure_imap(
        broker,
        "/tmp/stopaskingme.sqlite",
        "personal",
        "imap.other.example.test",
        "user@example.test",
        993,
        "INBOX",
        "second-fixture-password",
        keyring=keyring,
    )

    assert first["secret_name"] != second["secret_name"]
    assert first["secret_name"] in keyring.deleted
    assert first["secret_name"] not in keyring.values
    assert keyring.values[second["secret_name"]] == b"second-fixture-password"
    assert broker.store.get("connection", "personal")["host"] == "imap.other.example.test"


def test_configure_imap_failed_check_does_not_touch_saved_source(monkeypatch):
    monkeypatch.setattr(cli, "IMAPEmailAdapter", FakeAdapter)
    FakeAdapter.check_error = IMAPAuthenticationError("IMAP authentication failed")
    broker = _broker()
    broker.store.put(
        "connection",
        "personal",
        {"source_id": "personal", "kind": "imap", "secret_name": "old-name"},
    )
    keyring = MemoryKeyring()
    keyring.values["old-name"] = b"old-fixture-password"

    with pytest.raises(SystemExit, match="IMAP authentication failed"):
        cli._configure_imap(
            broker,
            "/tmp/stopaskingme.sqlite",
            "personal",
            "imap.example.test",
            "user@example.test",
            993,
            "INBOX",
            "new-fixture-password",
            keyring=keyring,
        )

    assert broker.store.get("connection", "personal")["secret_name"] == "old-name"
    assert keyring.values == {"old-name": b"old-fixture-password"}


def test_email_test_reconnects_saved_secret_without_returning_it(monkeypatch):
    monkeypatch.setattr(cli, "IMAPEmailAdapter", FakeAdapter)
    FakeAdapter.check_error = None
    broker = _broker()
    keyring = MemoryKeyring()
    config = cli._configure_imap(
        broker,
        "/tmp/stopaskingme.sqlite",
        "personal",
        "imap.example.test",
        "user@example.test",
        993,
        "INBOX",
        "fixture-password",
        keyring=keyring,
    )

    tested = cli._test_saved_imap(broker, "personal", keyring)

    assert tested == config
    assert "fixture-password" not in str(tested)


def test_configure_gmail_verifies_before_saving_refresh_state(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "IMAPEmailAdapter", FakeAdapter)
    FakeAdapter.instances = []
    FakeAdapter.check_error = None
    client_path = tmp_path / "desktop-client.json"
    client_path.write_text(
        '{"installed":{"client_id":"client-id","client_secret":"client-secret"}}'
    )
    broker = _broker()
    keyring = MemoryKeyring()
    provider = FakeOAuthProvider()

    config = cli._configure_gmail(
        broker,
        str(tmp_path / "broker.sqlite"),
        "personal",
        "user@gmail.test",
        client_path,
        keyring=keyring,
        oauth_provider=provider,
    )

    assert config["auth_kind"] == "google_oauth"
    assert config["host"] == "imap.gmail.com"
    assert len(FakeAdapter.instances) == 1
    assert FakeAdapter.instances[0].args[-1] is provider
    saved = keyring.values[config["secret_name"]]
    assert b"refresh-token-fixture" in saved
    assert b"access-token-fixture" not in saved
    assert broker.store.get("connection", "personal") == config


def test_configure_gmail_authorization_failure_does_not_save(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "IMAPEmailAdapter", FakeAdapter)
    FakeAdapter.instances = []
    FakeAdapter.check_error = None
    client_path = tmp_path / "desktop-client.json"
    client_path.write_text('{"installed":{"client_id":"client-id"}}')
    broker = _broker()
    keyring = MemoryKeyring()

    def fail(_client):
        from stopaskingme.google_oauth import GoogleOAuthError

        raise GoogleOAuthError("access-token-fixture leaked by provider")

    with pytest.raises(SystemExit, match="Google OAuth setup failed") as caught:
        cli._configure_gmail(
            broker,
            str(tmp_path / "broker.sqlite"),
            "personal",
            "user@gmail.test",
            client_path,
            keyring=keyring,
            authorize_fn=fail,
        )

    assert "access-token-fixture" not in str(caught.value)
    assert broker.store.get("connection", "personal") is None
    assert keyring.values == {}


def test_oauth_keychain_serializer_is_validated_and_canonicalized():
    provider = FakeOAuthProvider()
    saved = cli._oauth_keychain_secret(provider)
    assert saved == (
        b'{"client_id":"client-id","provider":"google",'
        b'"refresh_token":"refresh-token-fixture","version":1}'
    )

    with pytest.raises(SystemExit, match="invalid refresh state"):
        cli._oauth_keychain_secret(ExtraFieldOAuthProvider())
