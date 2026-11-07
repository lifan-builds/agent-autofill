import json

import pytest

from agent_autofill import cli, storage
from agent_autofill.status import _keychain_status, _startup_failure_snapshot
from agent_autofill.storage import CredentialStoreError, KeyringProvider


class DeniedBackend:
    def __init__(self, status):
        self.status = status
        self.writes = []

    def get_password(self, *_):
        try:
            raise OSError(self.status, "synthetic-private-backend-content")
        except OSError as error:
            raise ValueError("synthetic-private-wrapper") from error

    def set_password(self, *args):
        self.writes.append(args)


@pytest.mark.parametrize(("status", "code", "hint"), [
    (-25293, "keychain_auth_failed", "does not mean the Keychain is locked"),
    (-25308, "keychain_interaction_unavailable", "interactive logged-in"),
    (-128, "keychain_request_cancelled", "Retry when ready"),
    (-50, "credential_store_unavailable", "host process with Keychain access"),
])
def test_keychain_failure_is_classified_without_leaking_or_replacing_key(monkeypatch, status, code, hint):
    monkeypatch.setattr(storage.sys, "platform", "darwin")
    backend = DeniedBackend(status)
    with pytest.raises(CredentialStoreError) as caught:
        KeyringProvider(backend=backend).load_or_create("synthetic-database")
    assert caught.value.code == code
    assert hint in str(caught.value)
    snapshot = _startup_failure_snapshot(caught.value)
    assert snapshot["keychain"]["code"] == code
    assert hint in snapshot["next_action"]
    assert "synthetic-private" not in json.dumps(snapshot)
    assert backend.writes == []
    assert snapshot["inventory_status"] == "unknown"
    assert all(value is None for value in snapshot["stored"].values())
    assert snapshot["sources"]["configured"] is None
    assert snapshot["agents"]["configured"] is None
    assert snapshot["grants"]["active"] is None


def test_live_status_preserves_authentication_category(monkeypatch):
    monkeypatch.setattr(storage.sys, "platform", "darwin")
    broker = type("Broker", (), {"_keyring_provider": KeyringProvider(backend=DeniedBackend(-25293))})()
    status = _keychain_status(broker)
    assert status["code"] == "keychain_auth_failed"
    assert "synthetic-private" not in json.dumps(status)


def test_unknown_error_text_is_not_interpreted_as_osstatus(monkeypatch):
    monkeypatch.setattr(storage.sys, "platform", "darwin")
    error = CredentialStoreError(ValueError("-25293 synthetic-private-data"))
    assert error.code == "credential_store_unavailable"
    assert "synthetic-private" not in str(error)


@pytest.mark.parametrize("command", ["doctor", "start"])
def test_cli_does_not_report_empty_inventory_or_generic_fix(monkeypatch, capsys, command):
    monkeypatch.setattr(storage.sys, "platform", "darwin")

    def unavailable(_path):
        raise CredentialStoreError(OSError(-25293, "synthetic-private-data"))

    monkeypatch.setattr(cli, "_broker", unavailable)
    assert cli.main([command]) == 0
    output = capsys.readouterr().out
    assert "Inventory: unknown" in output
    assert "0 profile fields" not in output
    assert "None profile fields" not in output
    assert "Fix: Unlock" not in output
    assert "synthetic-private" not in output
