import sqlite3

from stopaskingme.storage import KeyProvider, Store


class MemoryKeys(KeyProvider):
    def __init__(self):
        self.values = {}

    def load_or_create(self, name):
        return self.values.setdefault(name, b"z" * 32)


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
    from stopaskingme.service import Broker

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
    from stopaskingme.service import Broker

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
