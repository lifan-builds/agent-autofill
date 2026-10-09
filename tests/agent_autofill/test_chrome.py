import csv
import sqlite3
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from agent_autofill.chrome import (
    ChromeImportError,
    cold_start,
    import_chrome_password_manager,
    preview_chrome_password_manager,
    sync_chrome_password_manager,
)
from agent_autofill.service import Broker
from agent_autofill.storage import Store


def _profile(tmp_path: Path) -> Path:
    profile = tmp_path / "Default"
    profile.mkdir()
    db = sqlite3.connect(profile / "Web Data")
    db.execute("create table autofill (name text, value text, count integer)")
    db.execute("insert into autofill values ('First name','Ada',3)")
    db.commit()
    db.close()
    db = sqlite3.connect(profile / "Login Data")
    db.execute(
        "create table logins (origin_url text, action_url text, username_value text, password_value blob, date_last_used integer)"
    )
    db.execute(
        "insert into logins values ('https://example.com','https://example.com/login','ada',?,0)",
        (b"encrypted",),
    )
    db.commit()
    db.close()
    return profile


def test_chrome_cold_start_imports_forms_and_only_login_metadata(tmp_path):
    profile = _profile(tmp_path)
    broker = Broker(Store(":memory:", key=b"k" * 32))
    result = cold_start(profile, broker)
    assert result == {"autofill_fields": 1, "login_metadata": 1, "passwords": 0}
    assert broker.list_profile_fields()[0]["display_name"] == "First name"
    assert (
        broker.store.get(
            "chrome-login-metadata",
            next(
                iter(
                    [
                        r["key"]
                        for r in broker.store.db.execute(
                            "select key from kv where namespace='chrome-login-metadata'"
                        )
                    ]
                )
            ),
        )["has_encrypted_password"]
        is True
    )


def test_chrome_password_csv_is_encrypted_and_metadata_only_listing(tmp_path):
    path = tmp_path / "passwords.csv"
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "url", "username", "password"])
        writer.writeheader()
        writer.writerow(
            {
                "name": "Example",
                "url": "https://example.com",
                "username": "ada",
                "password": "secret",
            }
        )
    broker = Broker(Store(":memory:", key=b"k" * 32))
    from agent_autofill.chrome import import_password_csv

    assert import_password_csv(path, broker) == 1
    assert broker.list_credentials()[0]["username"] == "ada"
    assert "secret" not in str(broker.list_credentials())


def test_cold_start_allows_profile_without_optional_chrome_databases(tmp_path):
    profile = tmp_path / "Default"
    profile.mkdir()
    broker = Broker(Store(":memory:", key=b"k" * 32))
    assert cold_start(profile, broker) == {
        "autofill_fields": 0,
        "login_metadata": 0,
        "passwords": 0,
    }


def _encrypt_chrome_password(password: str, safe_storage_password: str) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA1(),
        length=16,
        salt=b"saltysalt",
        iterations=1003,
    )
    key = kdf.derive(safe_storage_password.encode())
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(password.encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).encryptor()
    return b"v10" + encryptor.update(padded) + encryptor.finalize()


def _password_profile(tmp_path: Path, encrypted: bytes) -> Path:
    profile = tmp_path / "Default"
    profile.mkdir()
    db = sqlite3.connect(profile / "Login Data")
    db.execute(
        "create table logins (origin_url text, username_value text, password_value blob)"
    )
    db.execute(
        "insert into logins values (?, ?, ?)",
        ("https://example.com/login", "ada", encrypted),
    )
    db.commit()
    db.close()
    return profile


def test_chrome_password_manager_import_seals_password_and_lists_metadata_only(
    tmp_path,
):
    profile = _password_profile(
        tmp_path, _encrypt_chrome_password("synthetic-password", "safe-key")
    )
    broker = Broker(Store(":memory:", key=b"p" * 32))

    assert (
        import_chrome_password_manager(
            profile, broker, approved=True, safe_storage_password="safe-key"
        )
        == 1
    )
    metadata = broker.list_credentials()[0]
    assert metadata["origin"] == "https://example.com/login"
    assert "synthetic-password" not in str(metadata)
    stored = broker.store.get("credential", metadata["credential_id"])
    assert stored["password"] == "synthetic-password"


def test_chrome_password_manager_import_reads_account_store(tmp_path):
    profile = _password_profile(
        tmp_path, _encrypt_chrome_password("synthetic-password", "safe-key")
    )
    db = sqlite3.connect(profile / "Login Data For Account")
    db.execute(
        "create table logins (origin_url text, username_value text, password_value blob)"
    )
    db.execute(
        "insert into logins values (?, ?, ?)",
        (
            "https://account.example.test/login",
            "account-user",
            _encrypt_chrome_password("account-password", "safe-key"),
        ),
    )
    db.commit()
    db.close()
    broker = Broker(Store(":memory:", key=b"a" * 32))

    assert (
        import_chrome_password_manager(
            profile, broker, approved=True, safe_storage_password="safe-key"
        )
        == 2
    )
    assert len(broker.list_credentials()) == 2


def test_chrome_password_manager_import_requires_explicit_approval(tmp_path):
    profile = _password_profile(
        tmp_path, _encrypt_chrome_password("synthetic-password", "safe-key")
    )
    broker = Broker(Store(":memory:", key=b"q" * 32))

    with pytest.raises(ChromeImportError, match="explicit approval"):
        import_chrome_password_manager(
            profile, broker, safe_storage_password="safe-key"
        )
    assert broker.list_credentials() == []


def test_chrome_password_manager_rejects_unsupported_encryption_without_writes(
    tmp_path,
):
    profile = _password_profile(tmp_path, b"v20unsupported")
    broker = Broker(Store(":memory:", key=b"z" * 32))

    with pytest.raises(ChromeImportError, match="unsupported"):
        import_chrome_password_manager(
            profile, broker, approved=True, safe_storage_password="safe-key"
        )
    assert broker.list_credentials() == []


def _write_password_rows(profile: Path, rows: list[tuple[str, str, bytes]]) -> None:
    db = sqlite3.connect(profile / "Login Data")
    db.execute("delete from logins")
    db.executemany("insert into logins values (?, ?, ?)", rows)
    db.commit()
    db.close()


def test_chrome_password_manager_preview_and_sync_reconcile_without_stale_deletes(
    tmp_path,
):
    profile = _password_profile(
        tmp_path, _encrypt_chrome_password("first-password", "safe-key")
    )
    _write_password_rows(
        profile,
        [
            (
                "https://example.com/login",
                "ada",
                _encrypt_chrome_password("first-password", "safe-key"),
            ),
            (
                "https://old.example.test/login",
                "ada",
                _encrypt_chrome_password("old-password", "safe-key"),
            ),
        ],
    )
    broker = Broker(Store(":memory:", key=b"s" * 32))

    preview = preview_chrome_password_manager(
        profile, broker, safe_storage_password="safe-key"
    )
    assert preview["status"] == "preview"
    assert preview["processed_count"] == preview["unique_count"] == 2
    assert preview["add_count"] == 2
    assert preview["update_count"] == preview["unchanged_count"] == 0
    assert preview["stale_count"] == 0
    assert {item["origin"] for item in preview["added"]} == {
        "https://example.com/login",
        "https://old.example.test/login",
    }
    assert all("password" not in item for item in preview["added"])
    assert preview["updated"] == []
    assert preview["unchanged"] == []
    assert preview["stale"] == []
    assert broker.list_credentials() == []
    assert "first-password" not in str(preview)

    first_sync = sync_chrome_password_manager(
        profile, broker, approved=True, safe_storage_password="safe-key"
    )
    assert first_sync["added"] == 2

    _write_password_rows(
        profile,
        [
            (
                "https://example.com/login",
                "ada",
                _encrypt_chrome_password("updated-password", "safe-key"),
            ),
            (
                "https://new.example.test/login",
                "ada",
                _encrypt_chrome_password("new-password", "safe-key"),
            ),
        ],
    )
    second_preview = preview_chrome_password_manager(
        profile, broker, safe_storage_password="safe-key"
    )
    assert second_preview["add_count"] == 1
    assert second_preview["update_count"] == 1
    assert second_preview["unchanged_count"] == 0
    assert second_preview["stale_count"] == 1

    second_sync = sync_chrome_password_manager(
        profile, broker, approved=True, safe_storage_password="safe-key"
    )
    assert second_sync["removed"] == 0
    assert len(broker.list_credentials()) == 3
    assert broker.store.get("credential", "does-not-exist") is None

    final_sync = sync_chrome_password_manager(
        profile,
        broker,
        approved=True,
        remove_stale=True,
        safe_storage_password="safe-key",
    )
    assert final_sync["removed"] == 1
    origins = {item["origin"] for item in broker.list_credentials()}
    assert "https://old.example.test/login" not in origins
    assert "https://new.example.test/login" in origins
