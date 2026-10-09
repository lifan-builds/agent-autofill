import base64
import json
import sqlite3

import pytest

from agent_autofill.storage import KeyProvider, KeyringProvider, Store


class FakeKeychain:
    """Small keyring-compatible backend that records access and failures."""

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.get_calls = []
        self.set_calls = []
        self.delete_calls = []
        self.fail_set = False

    def get_password(self, service, account):
        self.get_calls.append((service, account))
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.set_calls.append((service, account, value))
        if self.fail_set:
            raise RuntimeError("synthetic Keychain write failure")
        self.values[(service, account)] = value

    def delete_password(self, service, account):
        self.delete_calls.append((service, account))
        if (service, account) not in self.values:
            raise KeyError("item not found")
        del self.values[(service, account)]


class UnavailableKeychain:
    def get_password(self, service, account):
        raise ValueError("(-50, 'Unknown Error')")


def test_keyring_reports_host_keychain_access_guidance():
    provider = KeyringProvider(backend=UnavailableKeychain())

    with pytest.raises(RuntimeError, match="host process with Keychain access"):
        provider.load_or_create("/tmp/agent-autofill.sqlite3")


class MemoryKeys(KeyProvider):
    def __init__(self):
        self.values = {}

    def load_or_create(self, name):
        return self.values.setdefault(name, b"z" * 32)


def test_keyring_uses_agent_autofill_service_name():
    assert KeyringProvider().service == "agent-autofill"


def test_keyring_migrates_legacy_database_key_into_one_vault():
    raw_key = b"legacy-database-key-32-bytes-long!!"
    backend = FakeKeychain(
        {
            ("agent-autofill", "/tmp/agent-autofill.sqlite3"): base64.urlsafe_b64encode(
                raw_key
            ).decode()
        }
    )
    provider = KeyringProvider(backend=backend)

    assert provider.load_or_create("/tmp/agent-autofill.sqlite3") == raw_key

    vault = backend.values[("agent-autofill", "vault:v1")]
    assert json.loads(vault)["database_keys"]
    assert ("agent-autofill", "/tmp/agent-autofill.sqlite3") not in backend.values
    assert backend.delete_calls == [
        ("agent-autofill", "/tmp/agent-autofill.sqlite3")
    ]


def test_keyring_lazily_migrates_connector_secret():
    secret = b"oauth-refresh-token-fixture"
    backend = FakeKeychain(
        {
            ("agent-autofill", "imap:v1:legacy"): base64.urlsafe_b64encode(
                secret
            ).decode()
        }
    )
    provider = KeyringProvider(backend=backend)

    assert provider.get_secret("imap:v1:legacy") == secret
    assert ("agent-autofill", "imap:v1:legacy") not in backend.values
    stored = json.loads(backend.values[("agent-autofill", "vault:v1")])
    assert stored["secrets"]
    assert backend.delete_calls == [("agent-autofill", "imap:v1:legacy")]


def test_keyring_does_not_delete_legacy_secret_when_vault_write_fails():
    secret = b"password-fixture"
    backend = FakeKeychain(
        {
            ("agent-autofill", "imap:v1:legacy"): base64.urlsafe_b64encode(
                secret
            ).decode()
        }
    )
    provider = KeyringProvider(backend=backend)
    backend.fail_set = True

    with pytest.raises(RuntimeError, match="OS credential store unavailable"):
        provider.get_secret("imap:v1:legacy")

    assert ("agent-autofill", "imap:v1:legacy") in backend.values
    assert backend.delete_calls == []


def test_keyring_caches_vault_for_provider_lifetime():
    backend = FakeKeychain()
    provider = KeyringProvider(backend=backend)

    provider.set_secret("imap:v1:one", b"first")
    assert provider.get_secret("imap:v1:one") == b"first"
    provider.set_secret("imap:v1:two", b"second")

    assert [call for call in backend.get_calls if call[1] == "vault:v1"] == [
        ("agent-autofill", "vault:v1")
    ]


def test_keyring_set_get_delete_round_trip_through_vault():
    backend = FakeKeychain()
    provider = KeyringProvider(backend=backend)

    provider.set_secret("imap:v1:round-trip", b"secret-fixture")
    assert provider.get_secret("imap:v1:round-trip") == b"secret-fixture"
    assert KeyringProvider(backend=backend).get_secret("imap:v1:round-trip") == b"secret-fixture"
    provider.delete_secret("imap:v1:round-trip")
    assert provider.get_secret("imap:v1:round-trip") is None

    stored = backend.values[("agent-autofill", "vault:v1")]
    assert "secret-fixture" not in stored
    assert "imap:v1:round-trip" not in json.loads(stored)["secrets"]


def test_keyring_vault_serialization_does_not_contain_plaintext_secret():
    backend = FakeKeychain()
    provider = KeyringProvider(backend=backend)
    provider.set_secret("imap:v1:opaque", b"super-secret-fixture")

    serialized = backend.values[("agent-autofill", "vault:v1")]
    assert "super-secret-fixture" not in serialized


def test_store_persists_encrypted_values_across_connections(tmp_path):
    keys = MemoryKeys()
    path = tmp_path / "broker.sqlite"
    Store(path, key_provider=keys, key_name="db").put("profile", "x", {"value": "Ada"})
    second = Store(path, key_provider=keys, key_name="db")
    assert second.get("profile", "x") == {"value": "Ada"}
    raw = second.db.execute("select value from kv").fetchone()[0]
    assert "Ada" not in raw


def test_store_purges_legacy_plaintext_otp_keys_on_reopen(tmp_path):
    keys = MemoryKeys()
    path = tmp_path / "broker.sqlite"
    first = Store(path, key_provider=keys, key_name="db")
    first.put("otp-used", "agent:account:123456", {"used_at": "fixture"})
    assert first.db.execute(
        "select count(*) from kv where namespace='otp-used'"
    ).fetchone()[0] == 1

    second = Store(path, key_provider=keys, key_name="db")

    assert second.db.execute(
        "select count(*) from kv where namespace='otp-used'"
    ).fetchone()[0] == 0


def test_store_purges_legacy_otp_keys_when_broker_reuses_open_store():
    from agent_autofill.service import Broker

    store = Store(":memory:", key=b"m" * 32)
    store.put("otp-used", "agent:account:123456", {"used_at": "fixture"})

    Broker(store)

    assert store.db.execute(
        "select count(*) from kv where namespace='otp-used'"
    ).fetchone()[0] == 0


def test_store_audit_redacts_nested_sensitive_keys():
    store = Store(":memory:", key=b"n" * 32)
    store.audit(
        "agent",
        "operation",
        "record",
        "timestamp",
        {
            "safe": "metadata",
            "nested": {
                "password": "synthetic-password",
                "cardNumber": "4111111111111111",
            },
        },
    )
    metadata = store.audits()[0]["metadata"]
    assert metadata == {"safe": "metadata", "nested": {}}


def test_store_apply_changes_rolls_back_puts_and_deletes_on_failure():
    store = Store(":memory:", key=b"a" * 32)
    store.put("financial", "old", {"value": "old"})
    store.db.execute(
        """
        create trigger fail_financial_insert after insert on kv
        when NEW.namespace = 'financial' and NEW.key = 'boom'
        begin select raise(abort, 'synthetic database failure'); end
        """
    )
    store.db.commit()
    try:
        store.apply_changes(
            [
                ("financial", "new", {"value": "new"}),
                ("financial", "boom", {"value": "boom"}),
            ],
            [("financial", "old")],
        )
    except sqlite3.IntegrityError as exc:
        assert "synthetic database failure" in str(exc)
        pass
    else:
        raise AssertionError("apply_changes should propagate the injected failure")

    assert store.get("financial", "old") == {"value": "old"}
    assert store.get("financial", "new") is None


def test_sensitive_records_persist_encrypted_across_store_reopen(tmp_path):
    from agent_autofill.service import Broker

    path = tmp_path / "records.sqlite"
    key = b"p" * 32
    first = Broker(Store(path, key=key))
    first.grant("codex", "credential.write", "login", "example.com", "form")
    first.grant("codex", "financial.write", "card", "example.com", "form")
    first.write_records(
        "codex",
        "example.com",
        "form",
        True,
        [
            {
                "record_type": "credential",
                "record_id": "login",
                "name": "Example",
                "origin": "example.com",
                "username": "ada",
                "password": "synthetic-password",
            },
            {
                "record_type": "financial",
                "record_id": "card",
                "kind": "payment_card",
                "display_name": "Primary card",
                "values": {"card_number": "4111111111111111"},
            },
        ],
    )
    raw = str(first.store.db.execute("select namespace,key,value from kv").fetchall())
    assert "synthetic-password" not in raw and "4111111111111111" not in raw
    first.store.db.close()

    second = Broker(Store(path, key=key))
    assert second.list_credentials()[0]["credential_id"] == "login"
    assert second.list_financial_records()[0]["record_id"] == "card"
    assert "values" not in second.list_financial_records()[0]
