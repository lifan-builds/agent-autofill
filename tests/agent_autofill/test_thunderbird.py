import json

import pytest

from agent_autofill.thunderbird import (
    list_accounts,
    parse_prefs,
    verify_profile,
)
from agent_autofill import cli
from agent_autofill.service import Broker
from agent_autofill.storage import Store


PREFS = '''
user_pref("mail.accountmanager.accounts", "account1,account2,account3");
user_pref("mail.account.account1.server", "server1");
user_pref("mail.account.account2.server", "server2");
user_pref("mail.account.account3.server", "server3");
user_pref("mail.server.server1.hostname", "imap.gmail.com");
user_pref("mail.server.server1.userName", "user@gmail.com");
user_pref("mail.server.server1.port", 993);
user_pref("mail.server.server1.authMethod", 10);
user_pref("mail.server.server2.hostname", "outlook.office365.com");
user_pref("mail.server.server2.userName", "user@example.test");
user_pref("mail.server.server2.port", 993);
user_pref("mail.server.server2.authMethod", 10);
user_pref("mail.server.server3.hostname", "Local Folders");
user_pref("mail.server.server3.userName", "nobody");
'''


class FakeNSS:
    def __init__(self, _profile):
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def decrypt(self, value):
        return {"enc-user-gmail": "user@gmail.com", "enc-token-gmail": "refresh-gmail"}[value]


class FakeAdapter:
    instances = []

    def __init__(self, *args):
        self.args = args
        self.__class__.instances.append(self)

    def check_connection(self):
        return None


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


def test_parse_and_list_accounts_exclude_local_folders(tmp_path):
    (tmp_path / "prefs.js").write_text(PREFS)
    assert parse_prefs(tmp_path / "prefs.js")["mail.server.server1.port"] == 993
    accounts = list_accounts(tmp_path)
    assert [item.account_id for item in accounts] == ["account1", "account2"]
    assert [item.provider for item in accounts] == ["google", "microsoft"]


def test_verify_profile_uses_read_only_adapter_and_redacts_output(tmp_path, monkeypatch):
    (tmp_path / "prefs.js").write_text(PREFS)
    (tmp_path / "logins.json").write_text(
        json.dumps(
            {
                "logins": [
                    {
                        "encryptedUsername": "enc-user-gmail",
                        "encryptedPassword": "enc-token-gmail",
                    }
                ]
            }
        )
    )
    FakeAdapter.instances = []
    monkeypatch.setattr(
        "agent_autofill.thunderbird._google_client_secret",
        lambda: "fixture-client-secret",
    )
    run = verify_profile(tmp_path, nss_factory=FakeNSS, adapter_factory=FakeAdapter)
    payload = run.public_dict()
    assert payload["accounts"][0] == {
        "account_id": "account1",
        "username": "u***@gmail.com",
        "host": "imap.gmail.com",
        "provider": "google",
        "status": "PASS",
    }
    assert payload["accounts"][1]["reason"] == "no_saved_credential"
    assert "refresh-gmail" not in json.dumps(payload)
    assert FakeAdapter.instances[0].args[5] == "INBOX"
    assert FakeAdapter.instances[0].args[-1].KEYCHAIN_PROVIDER == "google"


def test_thunderbird_import_requires_confirmation_and_persists_only_provider_payload(tmp_path, monkeypatch):
    (tmp_path / "prefs.js").write_text(PREFS)
    (tmp_path / "logins.json").write_text(
        json.dumps(
            {"logins": [{"encryptedUsername": "enc-user-gmail", "encryptedPassword": "enc-token-gmail"}]}
        )
    )
    monkeypatch.setattr("agent_autofill.thunderbird._google_client_secret", lambda: "fixture-client-secret")
    keyring = MemoryKeyring()
    broker = Broker(Store(":memory:", key=b"t" * 32))
    with pytest.raises(SystemExit, match="--confirm-import"):
        cli._import_thunderbird(
            broker,
            "/tmp/test.sqlite",
            tmp_path,
            ["account1"],
            keyring,
            confirm_import=False,
            nss_factory=FakeNSS,
            adapter_factory=FakeAdapter,
        )
    assert keyring.values == {}
    imported = cli._import_thunderbird(
        broker,
        "/tmp/test.sqlite",
        tmp_path,
        ["account1"],
        keyring,
        confirm_import=True,
        nss_factory=FakeNSS,
        adapter_factory=FakeAdapter,
    )
    assert imported[0]["status"] == "IMPORTED"
    secret = next(iter(keyring.values.values()))
    assert b"refresh-gmail" in secret
    assert b"access_token" not in secret
    assert broker.store.get("connection", "thunderbird-account1")["auth_kind"] == "google_oauth"
