# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Agent Autofill; Section 7 attribution terms in ATTRIBUTION.md.
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

    with pytest.raises(
        RuntimeError,
        match="host process with Keychain access|configure or unlock the operating-system credential store",
    ):
        provider.load_or_create("/tmp/agent-autofill.sqlite3")


class MemoryKeys(KeyProvider):
    def __init__(self):
        self.values = {}

    def load(self, name):
        return self.values.get(name)

    def load_or_create(self, name):
        return self.values.setdefault(name, b"z" * 32)


def test_existing_key_read_never_creates_or_migrates_keychain_entries():
    raw = b"r" * 32
    backend = FakeKeychain({("agent-autofill", "database"): base64.urlsafe_b64encode(raw).decode()})
    provider = KeyringProvider(backend=backend)
    assert provider.load_existing("database") == raw
    with pytest.raises(RuntimeError, match="unavailable"):
        provider.load_existing("missing")
    assert backend.set_calls == backend.delete_calls == []


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


def test_keyring_refreshes_vault_before_each_mutation():
    backend = FakeKeychain()
    provider = KeyringProvider(backend=backend)

    provider.set_secret("imap:v1:one", b"first")
    assert provider.get_secret("imap:v1:one") == b"first"
    provider.set_secret("imap:v1:two", b"second")

    assert [call for call in backend.get_calls if call[1] == "vault:v1"] == [
        ("agent-autofill", "vault:v1"),
        ("agent-autofill", "vault:v1"),
    ]


def test_keyring_merges_updates_from_two_provider_instances():
    backend = FakeKeychain()
    first = KeyringProvider(backend=backend)
    second = KeyringProvider(backend=backend)
    first._load_vault()
    second._load_vault()

    first.set_secret("imap:v1:one", b"first")
    second.set_secret("imap:v1:two", b"second")

    reopened = KeyringProvider(backend=backend)
    assert reopened.get_secret("imap:v1:one") == b"first"
    assert reopened.get_secret("imap:v1:two") == b"second"


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


def test_store_reopens_with_legacy_key_without_migrating_it(tmp_path):
    path = tmp_path / "broker.sqlite"
    key = b"l" * 32
    first = Store(path, key=key)
    first.put("profile", "x", {"value": "Ada"})
    first.db.close()
    backend = FakeKeychain({("agent-autofill", "db"): base64.urlsafe_b64encode(key).decode()})

    reopened = Store(path, key_provider=KeyringProvider(backend=backend), key_name="db")

    assert reopened.get("profile", "x") == {"value": "Ada"}
    assert len(backend.set_calls) == len(backend.delete_calls) == 0
    reopened.db.close()


@pytest.mark.parametrize(
    ("key_state", "error"),
    [("missing", "existing database key unavailable"), ("wrong", "store integrity check failed")],
)
def test_store_refuses_populated_vault_with_missing_or_wrong_key(tmp_path, key_state, error):
    path = tmp_path / "broker.sqlite"
    key = b"k" * 32
    first = Store(path, key=key)
    first.put("profile", "x", {"value": "Ada"})
    first.db.close()
    backend = FakeKeychain()
    if key_state == "wrong":
        backend.values[("agent-autofill", "db")] = base64.urlsafe_b64encode(b"w" * 32).decode()
    provider = KeyringProvider(backend=backend)

    with pytest.raises(ValueError, match=error):
        Store(path, key_provider=provider, key_name="db")

    assert len(backend.set_calls) == len(backend.delete_calls) == 0
    reopened = Store(path, key=key)
    assert reopened.get("profile", "x") == {"value": "Ada"}
    reopened.db.close()


def test_store_refuses_to_provision_a_key_for_a_populated_vault(tmp_path):
    class CreateOnlyKeys:
        def load_or_create(self, name):
            pytest.fail("provisioning must not run for a populated vault")

    path = tmp_path / "broker.sqlite"
    first = Store(path, key=b"f" * 32)
    first.put("profile", "x", {"value": "Ada"})
    first.db.close()

    with pytest.raises(ValueError, match="existing database key unavailable"):
        Store(path, key_provider=CreateOnlyKeys(), key_name="db")


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


def test_store_allows_concurrent_reader_and_writer_connections(tmp_path):
    keys = MemoryKeys()
    path = tmp_path / "broker.sqlite"
    Store(path, key_provider=keys, key_name="db").put("profile", "x", {"value": "Ada"})
    reader = sqlite3.connect(path)
    reader.execute("begin")
    assert reader.execute("select count(*) from kv").fetchone()[0] == 1

    writer = Store(path, key_provider=keys, key_name="db")
    writer.put("profile", "y", {"value": "Grace"})

    reader.rollback()
    reader.close()
    assert writer.get("profile", "y") == {"value": "Grace"}


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


def test_store_rejects_wrong_explicit_key_and_missing_key_without_overwriting(tmp_path):
    path = tmp_path / "vault.sqlite3"
    original_key = b"A" * 32
    first = Store(path, key=original_key)
    first.put("profile", "field", {"value": "preserved"})
    first.db.close()

    with pytest.raises(ValueError, match="store integrity check failed"):
        Store(path, key=b"B" * 32)

    with pytest.raises(ValueError, match="existing database key unavailable"):
        Store(path)

    reopened = Store(path, key=original_key)
    try:
        assert reopened.get("profile", "field") == {"value": "preserved"}
    finally:
        reopened.db.close()


def test_store_verifies_key_check_even_on_empty_store_and_audit_only_legacy_db(tmp_path):
    empty_path = tmp_path / "empty.sqlite3"
    first = Store(empty_path, key=b"E" * 32)
    first.db.close()
    with pytest.raises(ValueError, match="store integrity check failed"):
        Store(empty_path, key=b"F" * 32)

    audit_path = tmp_path / "audit-only.sqlite3"
    audit_store = Store(audit_path, key=b"G" * 32)
    audit_store.audit("agent", "op", "res", "2026-01-01T00:00:00Z", {"safe": "ok"})
    # Simulate a legacy database that has only encrypted audit rows and no store_meta.
    audit_store.db.execute("delete from store_meta")
    audit_store.db.commit()
    audit_store.db.close()

    class RejectCreateKeys(KeyProvider):
        def load(self, name):
            return None

        def load_or_create(self, name):
            pytest.fail("must not provision a replacement key for an audit-only database")

    with pytest.raises(ValueError, match="existing database key unavailable"):
        Store(audit_path, key_provider=RejectCreateKeys(), key_name="db")
    with pytest.raises(ValueError, match="store integrity check failed"):
        Store(audit_path, key=b"H" * 32)
    valid = Store(audit_path, key=b"G" * 32)
    try:
        assert valid.audits()[0]["metadata"] == {"safe": "ok"}
    finally:
        valid.db.close()


def test_keyring_vault_lock_uses_private_user_dir_independent_of_tmpdir_and_rejects_symlinks(
    monkeypatch, tmp_path
):
    import hashlib
    import os
    import stat

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    tmp_a = tmp_path / "tmp-a"
    tmp_b = tmp_path / "tmp-b"
    tmp_a.mkdir()
    tmp_b.mkdir()

    backend = FakeKeychain()
    monkeypatch.setenv("TMPDIR", str(tmp_a))
    KeyringProvider(service="unit-svc", backend=backend).set_secret("k1", b"v1")
    monkeypatch.setenv("TMPDIR", str(tmp_b))
    KeyringProvider(service="unit-svc", backend=backend).set_secret("k2", b"v2")

    assert list(tmp_a.iterdir()) == []
    assert list(tmp_b.iterdir()) == []

    app_dir = home / ".agent-autofill"
    lock_dir = app_dir / "locks"
    lock_name = hashlib.sha256(b"unit-svc").hexdigest()[:24]
    lock_file = lock_dir / f"keyring-{lock_name}.lock"

    assert stat.S_IMODE(os.stat(app_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(lock_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(lock_file).st_mode) == 0o600

    # Replace lock file with symlink; O_NOFOLLOW must fail closed.
    decoy = tmp_path / "decoy"
    decoy.write_text("decoy")
    lock_file.unlink()
    lock_file.symlink_to(decoy)
    with pytest.raises(OSError):
        KeyringProvider(service="unit-svc", backend=backend).set_secret("k3", b"v3")


def test_store_precreates_sqlite_db_with_0600_before_connect_and_rejects_symlinks(
    monkeypatch, tmp_path
):
    import os
    import stat

    from agent_autofill import storage

    db_path = tmp_path / "secure.sqlite3"
    observed_modes = []
    real_connect = storage.sqlite3.connect

    def checking_connect(target, *args, **kwargs):
        if target != ":memory:":
            observed_modes.append(stat.S_IMODE(os.stat(target).st_mode))
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(storage.sqlite3, "connect", checking_connect)
    store = Store(db_path, key=b"S" * 32)
    store.put("profile", "k", {"v": 1})
    store.db.close()

    assert observed_modes == [0o600]
    assert stat.S_IMODE(os.stat(db_path).st_mode) == 0o600

    # Existing overly permissive DB is tightened before sqlite3.connect.
    os.chmod(db_path, 0o644)
    observed_modes.clear()
    reopened = Store(db_path, key=b"S" * 32)
    reopened.db.close()
    assert observed_modes == [0o600]

    # Symlink DB target is rejected via O_NOFOLLOW before sqlite3.connect.
    symlink_path = tmp_path / "link.sqlite3"
    symlink_path.symlink_to(db_path)
    with pytest.raises(OSError):
        Store(symlink_path, key=b"S" * 32)


def test_keyring_backend_errors_are_value_free_in_tracebacks(monkeypatch, tmp_path):
    import traceback

    from agent_autofill.storage import CredentialStoreError

    monkeypatch.setenv("HOME", str(tmp_path))
    canary = "synthetic-backend-secret-99123"

    class LeakyBackend:
        def __init__(self, fail_on):
            self.fail_on = fail_on

        def get_password(self, service, account):
            if self.fail_on == "get":
                raise RuntimeError(canary)
            return None

        def set_password(self, service, account, value):
            if self.fail_on == "set":
                raise RuntimeError(canary)

    for fail_on in ("get", "set"):
        provider = KeyringProvider(service="unit", backend=LeakyBackend(fail_on))
        with pytest.raises(CredentialStoreError) as caught:
            provider.set_secret("connector", b"unit-secret")
        assert canary not in str(caught.value)
        assert canary not in "".join(traceback.format_exception(caught.value))
