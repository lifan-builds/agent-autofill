# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Agent Autofill; Section 7 attribution terms in ATTRIBUTION.md.
import csv
import sqlite3
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from agent_autofill import chrome
from agent_autofill.chrome import (
    ChromeImportError,
    cold_start,
    default_profile,
    import_chrome_password_manager,
    preview_chrome_password_manager,
    preview_chrome_payment_cards,
    sync_chrome_password_manager,
    sync_chrome_payment_cards,
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


def test_default_profile_resolver_never_falls_back_to_secondary_profile(tmp_path):
    root = tmp_path / "Chrome"
    (root / "Default").mkdir(parents=True)
    (root / "Profile 1").mkdir()
    assert default_profile(root) == root / "Default"
    with pytest.raises(ChromeImportError, match="Default profile"):
        default_profile(root / "Profile 1")


def test_default_profile_resolver_reports_missing_default(tmp_path):
    with pytest.raises(ChromeImportError, match="Default profile was not found"):
        default_profile(tmp_path / "Chrome")


def test_explicit_numbered_profile_supported_for_cold_start_and_password_sync(tmp_path):
    profile = tmp_path / "Profile 1"
    profile.mkdir()
    db = sqlite3.connect(profile / "Web Data")
    db.execute("create table autofill (name text, value text, count integer)")
    db.execute("insert into autofill values ('First name','Grace',2)")
    db.commit()
    db.close()
    encrypted = _encrypt_chrome_password("profile1-password", "safe-key")
    db = sqlite3.connect(profile / "Login Data")
    db.execute(
        "create table logins (origin_url text, action_url text, username_value text, password_value blob, date_last_used integer)"
    )
    db.execute(
        "insert into logins values ('https://work.example.com','https://work.example.com/login','grace',?,0)",
        (encrypted,),
    )
    db.commit()
    db.close()

    broker = Broker(Store(":memory:", key=b"m" * 32))
    assert cold_start(profile, broker) == {
        "autofill_fields": 1,
        "login_metadata": 1,
        "passwords": 0,
    }
    sync = sync_chrome_password_manager(
        profile, broker, approved=True, safe_storage_password="safe-key"
    )
    assert sync["added"] == 1
    assert broker.list_credentials()[0]["origin"] == "https://work.example.com"


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


def test_snapshot_removes_partial_copy_on_failure(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    source.write_bytes(b"synthetic-data")
    monkeypatch.setattr(chrome.tempfile, "tempdir", str(tmp_path))

    def fail_copy(_source, destination):
        destination.write_bytes(b"partial-synthetic-data")
        raise OSError("synthetic copy failure")

    monkeypatch.setattr(chrome.shutil, "copy2", fail_copy)
    with pytest.raises(OSError, match="synthetic copy failure"):
        chrome._snapshot(source)
    assert not list(tmp_path.glob("agent-autofill-chrome-*.db"))
    assert source.read_bytes() == b"synthetic-data"


@pytest.mark.parametrize("reader", [chrome.read_autofill, chrome.read_login_metadata])
def test_snapshot_removed_when_database_cannot_open(tmp_path, monkeypatch, reader):
    profile = _profile(tmp_path)
    monkeypatch.setattr(chrome.tempfile, "tempdir", str(tmp_path))

    def fail_connect(_path):
        raise sqlite3.OperationalError("synthetic open failure")

    monkeypatch.setattr(chrome.sqlite3, "connect", fail_connect)
    with pytest.raises(sqlite3.OperationalError, match="synthetic open failure"):
        reader(profile)
    assert not list(tmp_path.glob("agent-autofill-chrome-*.db"))


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


def test_chrome_password_manager_preview_handles_non_ascii_passwords(tmp_path):
    profile = _password_profile(
        tmp_path, _encrypt_chrome_password("pässwörd-🔑", "safe-key")
    )
    broker = Broker(Store(":memory:", key=b"u" * 32))
    import_chrome_password_manager(
        profile, broker, approved=True, safe_storage_password="safe-key"
    )
    preview = preview_chrome_password_manager(
        profile, broker, safe_storage_password="safe-key"
    )
    assert preview["unchanged_count"] == 1
    assert preview["update_count"] == 0


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


def test_chrome_payment_cards_preview_and_sync_with_default_c1vx_and_cloud_masked(
    tmp_path,
):
    profile = tmp_path / "Default"
    profile.mkdir()
    enc_card = _encrypt_chrome_password("4111111111114242", "safe-key")
    enc_cvc = _encrypt_chrome_password("987", "safe-key")
    db = sqlite3.connect(profile / "Web Data")
    db.execute(
        """
        create table credit_cards (
            guid text primary key,
            name_on_card text,
            expiration_month integer,
            expiration_year integer,
            card_number_encrypted blob,
            nickname text
        )
        """
    )
    db.execute(
        "create table local_stored_cvc (guid text primary key, value_encrypted blob)"
    )
    db.execute(
        """
        create table masked_credit_cards (
            id text primary key,
            network text,
            last_four text,
            exp_month integer,
            exp_year integer,
            nickname text,
            card_issuer_id text
        )
        """
    )
    db.execute(
        "insert into credit_cards values (?, ?, ?, ?, ?, ?)",
        ("guid-c1vx", "Ada Lovelace", 8, 2029, enc_card, "C1vx"),
    )
    db.execute(
        "insert into local_stored_cvc values (?, ?)",
        ("guid-c1vx", enc_cvc),
    )
    db.execute(
        "insert into masked_credit_cards values (?, ?, ?, ?, ?, ?, ?)",
        ("cloud-1", "VISA", "9999", 11, 2030, "Capital One Venture X", "capitalone"),
    )
    db.commit()
    db.close()

    broker = Broker(Store(":memory:", key=b"p" * 32))
    preview = preview_chrome_payment_cards(
        profile, broker, safe_storage_password="safe-key"
    )
    assert preview["local_card_count"] == 1
    assert preview["add_count"] == 1
    assert preview["masked_cloud_card_count"] == 1
    assert "4111111111114242" not in str(preview)
    assert "987" not in str(preview)
    assert preview["added"][0]["last4"] == "4242"
    assert preview["added"][0]["nickname"] == "C1vx"
    assert preview["added"][0]["has_security_code"] is True
    assert preview["masked_cloud_cards"][0]["importable"] is False

    with pytest.raises(ChromeImportError, match="explicit approval"):
        sync_chrome_payment_cards(
            profile, broker, approved=False, safe_storage_password="safe-key"
        )

    sync = sync_chrome_payment_cards(
        profile,
        broker,
        approved=True,
        default_record_id="C1vx",
        safe_storage_password="safe-key",
    )
    assert sync["added"] == 1
    assert "4111111111114242" not in str(sync)
    assert "987" not in str(sync)

    listed = broker.list_financial_records(query="C1vx")
    assert len(listed) == 1
    assert listed[0]["is_default"] is True
    assert listed[0]["allowed_origins"] == []
    assert listed[0]["matching_hints"]["last4"] == "4242"
    assert "4111111111114242" not in str(listed)

    broker.create_agent("service:test")
    broker.enable_trusted_local("service:test")
    read = broker.request_financial_fields(
        "service:test",
        listed[0]["record_id"],
        ["card_number", "security_code", "expiration_month", "expiration_year"],
        target_origin="https://aca.aaamembershipoffers.com",
        purpose="membership-purchase",
    )
    assert read["card_number"] == "4111111111114242"
    assert read["security_code"] == "987"
    assert read["expiration_month"] == "08"
    assert read["expiration_year"] == "2029"


def test_chrome_payment_cards_empty_nickname_with_billing_postal_code_and_cli_flags(
    tmp_path, monkeypatch
):
    from agent_autofill import cli

    profile = tmp_path / "Default"
    profile.mkdir()
    enc_card = _encrypt_chrome_password("4111111111118888", "safe-key")
    enc_cvc = _encrypt_chrome_password("654", "safe-key")
    db = sqlite3.connect(profile / "Web Data")
    db.execute(
        """
        create table credit_cards (
            guid text primary key,
            name_on_card text,
            expiration_month integer,
            expiration_year integer,
            card_number_encrypted blob,
            billing_address_id text,
            nickname text
        )
        """
    )
    db.execute(
        "create table local_stored_cvc (guid text primary key, value_encrypted blob)"
    )
    db.execute(
        "create table address_type_tokens (guid text, type integer, value text)"
    )
    db.execute(
        "insert into credit_cards values (?, ?, ?, ?, ?, ?, ?)",
        ("guid-empty-nick", "Ada Lovelace", 5, 2031, enc_card, "addr-guid-1", ""),
    )
    db.execute("insert into local_stored_cvc values (?, ?)", ("guid-empty-nick", enc_cvc))
    db.execute(
        "insert into address_type_tokens values (?, ?, ?)",
        ("addr-guid-1", 35, "94107"),
    )
    db.commit()
    db.close()

    broker = Broker(Store(":memory:", key=b"q" * 32))
    preview = preview_chrome_payment_cards(
        profile, broker, safe_storage_password="safe-key"
    )
    assert preview["added"][0]["nickname"] is None
    assert preview["added"][0]["has_billing_postal_code"] is True
    assert "94107" not in str(preview)
    assert "4111111111118888" not in str(preview)

    # Unmatched --default-card raises an explicit error instead of silently skipping default
    with pytest.raises(ChromeImportError, match="No imported Chrome payment card matched"):
        sync_chrome_payment_cards(
            profile,
            broker,
            approved=True,
            default_record_id="C1vx",
            safe_storage_password="safe-key",
        )

    # CLI --default --hint C1vx --institution "Capital One" --display-name "Capital One Venture X"
    monkeypatch.setattr(cli, "_broker", lambda _path: broker)
    monkeypatch.setattr(
        "agent_autofill.chrome._macos_safe_storage_password", lambda: "safe-key"
    )
    assert (
        cli.main(
            [
                "--db",
                "unused.sqlite",
                "chrome",
                "payment-cards",
                "--profile-dir",
                str(profile),
                "--confirm-import",
                "--default",
                "--hint",
                "C1vx",
                "--institution",
                "Capital One",
                "--display-name",
                "Capital One Venture X",
            ]
        )
        == 0
    )

    cards = broker.match_financial_records(query="C1vx")
    assert len(cards) == 1
    assert cards[0]["is_default"] is True
    assert cards[0]["display_name"] == "Capital One Venture X"
    assert cards[0]["matching_hints"]["hint"] == "C1vx"
    assert cards[0]["matching_hints"]["institution"] == "Capital One"

    broker.create_agent("service:test")
    broker.enable_trusted_local("service:test")
    read = broker.request_financial_fields(
        "service:test",
        cards[0]["record_id"],
        ["card_number", "security_code", "billing_postal_code"],
        target_origin="https://aca.aaamembershipoffers.com",
        purpose="membership-purchase",
    )
    assert read["card_number"] == "4111111111118888"
    assert read["security_code"] == "654"
    assert read["billing_postal_code"] == "94107"


