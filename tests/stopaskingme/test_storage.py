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
