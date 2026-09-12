import csv, sqlite3
from pathlib import Path
from palisade.chrome import cold_start, read_login_metadata, read_autofill
from palisade.service import Broker
from palisade.storage import Store


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
    from palisade.chrome import import_password_csv

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
