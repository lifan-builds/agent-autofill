from pathlib import Path
from types import SimpleNamespace

from agent_autofill.local import connect_service, default_database_path
from agent_autofill.server import _call
from agent_autofill.storage import KeyringProvider


class MemoryBackend:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


def test_default_database_prefers_existing_home_database(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    expected = tmp_path / ".agent-autofill.sqlite3"
    expected.write_bytes(b"fixture")
    assert default_database_path() == expected


def test_service_pairing_is_idempotent_and_keeps_token_in_keychain(tmp_path):
    backend = MemoryBackend()
    keyring = KeyringProvider(backend=backend)
    db_path = tmp_path / "broker.sqlite3"

    first = connect_service("cpa-phoenix", db_path, keyring)
    first_token = first.token
    first.close()

    second = connect_service("cpa-phoenix", db_path, keyring)
    try:
        assert second.agent_id == "service:cpa-phoenix"
        assert second.token == first_token
        assert second.broker.list_agents() == [
            {
                "agent_id": "service:cpa-phoenix",
                "created_at": second.broker.list_agents()[0]["created_at"],
                "revoked": False,
                "access_mode": "trusted-local-read",
            }
        ]
        assert first_token not in second.broker.store.db.execute(
            "select value from kv where namespace='agent'"
        ).fetchone()[0]
        assert any("vault:v1" in key[1] for key in backend.values)
    finally:
        second.close()


def test_service_pairing_grants_read_access_without_expiring_grants(tmp_path):
    backend = MemoryBackend()
    keyring = KeyringProvider(backend=backend)
    connection = connect_service("calendar", tmp_path / "broker.sqlite3", keyring)
    try:
        connection.broker.put_profile(
            "name",
            "identity",
            "Name",
            "Ada",
            allowed_origins=["https://only.example"],
            allowed_purposes=["only-this-form"],
        )
        connection.broker.put_credential(
            "example-login",
            "Example",
            "https://only.example/login",
            "ada@example.test",
            "secret-password",
            allowed_origins=["https://only.example"],
            allowed_purposes=["only-this-form"],
        )
        connection.broker.put_financial_record(
            "example-record",
            "custom_financial",
            "Example record",
            {"private_value": "secret-value"},
            allowed_origins=["https://only.example"],
            allowed_purposes=["only-this-form"],
        )
        result = _call(
            connection.broker,
            "request_profile_fields",
            {
                "field_ids": ["name"],
            },
            SimpleNamespace(
                agent_id=connection.agent_id,
                token=connection.token,
            ),
        )
        assert result["name"]["value"] == "Ada"
        credential = _call(
            connection.broker,
            "request_credential_fields",
            {
                "credential_id": "example-login",
                "field_ids": ["password"],
            },
            SimpleNamespace(
                agent_id=connection.agent_id,
                token=connection.token,
            ),
        )
        assert credential["password"] == "secret-password"
        financial = _call(
            connection.broker,
            "request_financial_fields",
            {
                "record_id": "example-record",
                "field_ids": ["private_value"],
            },
            SimpleNamespace(
                agent_id=connection.agent_id,
                token=connection.token,
            ),
        )
        assert financial["private_value"] == "secret-value"
        assert connection.broker.list_grants() == []
    finally:
        connection.close()
