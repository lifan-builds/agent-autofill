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
