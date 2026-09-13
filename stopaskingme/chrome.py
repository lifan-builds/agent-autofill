"""Safe Chrome cold-start import helpers.

Chrome's Login Data password blobs are encrypted with a platform key and are
not a supported third-party API. We therefore read the database read-only for
metadata and accept actual passwords only from a user-triggered Chrome CSV
export. The CSV is encrypted immediately into the broker store and is never
printed or copied by this module.
"""

import csv
import hashlib
import shutil
import sqlite3
import tempfile
import platform
from pathlib import Path
from .service import Broker


class ChromeImportError(RuntimeError):
    pass


def discover_profiles(root: str | Path | None = None) -> list[Path]:
    if root:
        bases = [Path(root)]
    elif platform.system() == "Darwin":
        bases = [Path.home() / "Library/Application Support/Google/Chrome"]
    elif platform.system() == "Windows":
        bases = [Path.home() / "AppData/Local/Google/Chrome/User Data"]
    else:
        bases = [
            Path.home() / ".config/google-chrome",
            Path.home() / ".config/chromium",
        ]
    for base in bases:
        if base.exists():
            profiles = [
                p
                for p in base.iterdir()
                if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile "))
            ]
            return sorted(profiles, key=lambda p: (p.name != "Default", p.name))
    return []


def _snapshot(path: Path):
    if not path.exists():
        raise ChromeImportError(f"Chrome data file not found: {path}")
    tmp = tempfile.NamedTemporaryFile(
        prefix="stopaskingme-chrome-", suffix=".db", delete=False
    )
    tmp.close()
    shutil.copy2(path, tmp.name)
    return Path(tmp.name)


def _columns(db, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"pragma table_info({table})")}


def read_autofill(profile_dir: str | Path) -> list[dict]:
    data_path = Path(profile_dir) / "Web Data"
    if not data_path.exists():
        return []
    snapshot = _snapshot(data_path)
    try:
        db = sqlite3.connect(snapshot)
        if "autofill" not in {
            r[0]
            for r in db.execute("select name from sqlite_master where type='table'")
        }:
            return []
        cols = _columns(db, "autofill")
        if not {"name", "value"}.issubset(cols):
            return []
        count = "count" if "count" in cols else "1"
        return [
            {"name": row[0], "value": row[1], "count": row[2]}
            for row in db.execute(
                f"select name,value,{count} from autofill where value is not null and value != ''"
            )
        ]
    finally:
        try:
            db.close()
        except Exception:
            pass
        snapshot.unlink(missing_ok=True)


def read_login_metadata(profile_dir: str | Path) -> list[dict]:
    data_path = Path(profile_dir) / "Login Data"
    if not data_path.exists():
        return []
    snapshot = _snapshot(data_path)
    try:
        db = sqlite3.connect(snapshot)
        tables = {
            r[0]
            for r in db.execute("select name from sqlite_master where type='table'")
        }
        if "logins" not in tables:
            return []
        cols = _columns(db, "logins")
        selected = [
            c
            for c in ("origin_url", "action_url", "username_value", "date_last_used")
            if c in cols
        ]
        if not selected:
            return []
        query = "select " + ",".join(selected) + " from logins"
        return [
            dict(zip(selected, row))
            | {"has_encrypted_password": "password_value" in cols}
            for row in db.execute(query)
        ]
    finally:
        try:
            db.close()
        except Exception:
            pass
        snapshot.unlink(missing_ok=True)


def import_autofill(profile_dir: str | Path, broker: Broker) -> int:
    count = 0
    for item in read_autofill(profile_dir):
        field_id = "autofill:" + hashlib.sha256(item["name"].encode()).hexdigest()[:20]
        broker.put_profile(
            field_id,
            "chrome-autofill",
            item["name"],
            item["value"],
            sensitivity="sensitive",
        )
        count += 1
    return count


def import_login_metadata(profile_dir: str | Path, broker: Broker) -> int:
    count = 0
    for item in read_login_metadata(profile_dir):
        key = hashlib.sha256(
            (item.get("origin_url") or item.get("action_url") or "").encode()
        ).hexdigest()[:20]
        broker.store.put("chrome-login-metadata", key, item)
        count += 1
    return count


def import_password_csv(csv_path: str | Path, broker: Broker) -> int:
    path = Path(csv_path)
    if not path.exists():
        raise ChromeImportError(f"password CSV not found: {path}")
    count = 0
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            url = (row.get("url") or row.get("URL") or "").strip()
            username = (row.get("username") or row.get("Username") or "").strip()
            password = row.get("password") or row.get("Password") or ""
            name = (row.get("name") or row.get("Name") or url).strip()
            if not url or not password:
                continue
            ident = hashlib.sha256(f"{url}\0{username}".encode()).hexdigest()[:24]
            broker.put_credential(
                ident, name, url, username, password, row.get("note", "")
            )
            count += 1
    return count


def cold_start(
    profile_dir: str | Path, broker: Broker, password_csv: str | Path | None = None
) -> dict:
    if not Path(profile_dir).is_dir():
        raise ChromeImportError(f"Chrome profile directory not found: {profile_dir}")
    result = {
        "autofill_fields": import_autofill(profile_dir, broker),
        "login_metadata": import_login_metadata(profile_dir, broker),
        "passwords": 0,
    }
    if password_csv is not None:
        result["passwords"] = import_password_csv(password_csv, broker)
    return result
