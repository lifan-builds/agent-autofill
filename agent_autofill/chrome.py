"""Safe Chrome cold-start and Password Manager import helpers.

Structured profile data is read without mutation. Password Manager imports
require explicit approval, use the macOS Chrome Safe Storage key, and seal
decrypted values immediately in the broker; plaintext passwords never enter
metadata listings, audits, or diagnostics.
"""

import csv
import hashlib
import hmac
import shutil
import sqlite3
import subprocess
import tempfile
import platform
import re
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .service import Broker


class ChromeImportError(RuntimeError):
    pass


_CHROME_SAFE_STORAGE_SERVICE = "Chrome Safe Storage"
_CHROME_SAFE_STORAGE_ACCOUNT = "Chrome"
_CHROME_PASSWORD_PREFIXES = (b"v10", b"v11")


def _opaque_hash_id(*parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:24]
    return "_".join(digest[index : index + 4] for index in range(0, 24, 4))


def _credential_name(hostname: str) -> str:
    return hostname if not re.search(r"\d{8,}", hostname) else "Chrome login"


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
        prefix="agent-autofill-chrome-", suffix=".db", delete=False
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


def _macos_safe_storage_password() -> str:
    """Read Chrome's encryption secret from the macOS credential store."""

    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                [
                    "security",
                    "find-generic-password",
                    "-s",
                    _CHROME_SAFE_STORAGE_SERVICE,
                    "-a",
                    _CHROME_SAFE_STORAGE_ACCOUNT,
                    "-w",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ChromeImportError(
                "Chrome Safe Storage is unavailable; unlock macOS Keychain and retry"
            ) from exc
        value = result.stdout.rstrip("\r\n") if result.returncode == 0 else ""
        if value:
            return value
        raise ChromeImportError(
            "Chrome Safe Storage is unavailable; unlock macOS Keychain and retry"
        )

    try:
        import keyring

        keychain_value = keyring.get_password(
            _CHROME_SAFE_STORAGE_SERVICE, _CHROME_SAFE_STORAGE_ACCOUNT
        )
    except Exception as exc:
        raise ChromeImportError(
            "Chrome Safe Storage is unavailable; unlock macOS Keychain and retry"
        ) from exc
    if not isinstance(keychain_value, str) or not keychain_value:
        raise ChromeImportError(
            "Chrome Safe Storage is unavailable; unlock macOS Keychain and retry"
        )
    return keychain_value


def _macos_chrome_key(safe_storage_password: str) -> bytes:
    """Derive the legacy macOS Chrome Login Data AES key."""

    if not isinstance(safe_storage_password, str) or not safe_storage_password:
        raise ChromeImportError("Chrome Safe Storage returned an invalid key")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA1(),
        length=16,
        salt=b"saltysalt",
        iterations=1003,
    )
    return kdf.derive(safe_storage_password.encode("utf-8"))


def _decrypt_macos_password(value: object, key: bytes) -> str:
    """Decrypt a supported Chrome v10/v11 Login Data password value."""

    if isinstance(value, memoryview):
        value = value.tobytes()
    if not isinstance(value, bytes) or not value.startswith(_CHROME_PASSWORD_PREFIXES):
        raise ChromeImportError(
            "Chrome password encryption is unsupported; export passwords from Chrome and retry"
        )
    ciphertext = value[3:]
    if not ciphertext or len(ciphertext) % 16:
        raise ChromeImportError("Chrome password data is malformed")
    try:
        decryptor = Cipher(
            algorithms.AES(key), modes.CBC(b" " * 16)
        ).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
        plaintext = unpadder.update(padded) + unpadder.finalize()
        result = plaintext.decode("utf-8")
    except Exception as exc:
        raise ChromeImportError(
            "Chrome password data could not be decrypted with Safe Storage"
        ) from exc
    if not result:
        raise ChromeImportError("Chrome password data is empty")
    return result


def _profile_source_id(profile_dir: str | Path) -> str:
    digest = hashlib.sha256(
        ("chrome-profile\0" + str(Path(profile_dir).resolve())).encode("utf-8")
    ).hexdigest()[:24]
    return "chrome_profile_" + "_".join(digest[i : i + 4] for i in range(0, 24, 4))


def _read_chrome_passwords(
    profile_dir: str | Path, safe_storage_password: str | None = None
) -> list[dict]:
    """Read and decrypt Chrome passwords into short-lived process memory.

    The caller must write each result immediately to the encrypted broker. The
    returned dictionaries are never suitable for metadata listings or audit
    rows because they contain a plaintext password.
    """

    profile_path = Path(profile_dir)
    data_paths = [
        profile_path / "Login Data",
        profile_path / "Login Data For Account",
    ]
    data_paths = [path for path in data_paths if path.exists()]
    if not data_paths:
        return []
    key = _macos_chrome_key(
        safe_storage_password
        if safe_storage_password is not None
        else _macos_safe_storage_password()
    )
    result = []
    source_profile_id = _profile_source_id(profile_dir)
    for data_path in data_paths:
        snapshot = _snapshot(data_path)
        db: sqlite3.Connection | None = None
        try:
            db = sqlite3.connect(snapshot)
            tables = {
                row[0]
                for row in db.execute(
                    "select name from sqlite_master where type='table'"
                )
            }
            if "logins" not in tables:
                continue
            columns = _columns(db, "logins")
            required = {"origin_url", "username_value", "password_value"}
            if not required.issubset(columns):
                raise ChromeImportError(
                    "Chrome Login Data schema is unsupported; export passwords from Chrome and retry"
                )
            rows = db.execute(
                "select origin_url, username_value, password_value from logins "
                "where origin_url is not null and origin_url != '' "
                "and password_value is not null and length(password_value) > 3"
            ).fetchall()
            for origin_url, username, encrypted in rows:
                if not isinstance(origin_url, str) or not origin_url.strip():
                    continue
                try:
                    parsed = urlparse(origin_url)
                    hostname = parsed.hostname
                    parsed.port
                except ValueError:
                    continue
                if parsed.scheme not in {"http", "https"} or not hostname:
                    continue
                result.append(
                    {
                        "origin": origin_url,
                        "hostname": hostname.lower(),
                        "username": username if isinstance(username, str) else "",
                        "password": _decrypt_macos_password(encrypted, key),
                        "source_profile_id": source_profile_id,
                    }
                )
        except ChromeImportError:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise ChromeImportError(
                "Chrome Login Data could not be read; close Chrome and retry"
            ) from exc
        finally:
            if db is not None:
                db.close()
            snapshot.unlink(missing_ok=True)
    return result


def import_chrome_password_manager(
    profile_dir: str | Path,
    broker: Broker,
    *,
    approved: bool = False,
    safe_storage_password: str | None = None,
) -> int:
    """Import Chrome Password Manager entries after explicit local approval.

    Passwords are decrypted only in memory, then sealed by ``Broker``. The
    broker exposes them later only through its existing authenticated,
    origin/purpose-scoped credential operations.
    """

    result = sync_chrome_password_manager(
        profile_dir,
        broker,
        approved=approved,
        safe_storage_password=safe_storage_password,
    )
    return result["added"] + result["updated"]


def _chrome_credential_id(item: dict) -> str:
    return _opaque_hash_id(item["origin"], item["username"])


def _chrome_password_plan(profile_dir: str | Path, broker: Broker, items: list[dict]) -> dict:
    source_profile_id = _profile_source_id(profile_dir)
    source_by_id = {_chrome_credential_id(item): item for item in items}
    existing = {}
    for metadata in broker.list_credentials():
        provenance = metadata.get("provenance")
        if not isinstance(provenance, dict):
            continue
        if (
            provenance.get("source") == "chrome-password-manager"
            and provenance.get("source_profile_id") == source_profile_id
        ):
            existing[metadata["credential_id"]] = metadata
    added = []
    updated = []
    unchanged = []
    for credential_id, item in source_by_id.items():
        stored = broker.store.get("credential", credential_id)
        if stored is None:
            added.append(credential_id)
            continue
        password = stored.get("password", "")
        if isinstance(password, str) and hmac.compare_digest(password, item["password"]):
            unchanged.append(credential_id)
        else:
            updated.append(credential_id)
    stale = sorted(set(existing) - set(source_by_id))
    return {
        "source_profile_id": source_profile_id,
        "processed_count": len(items),
        "unique_count": len(source_by_id),
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
        "stale": stale,
    }


def preview_chrome_password_manager(
    profile_dir: str | Path,
    broker: Broker,
    *,
    safe_storage_password: str | None = None,
) -> dict:
    """Return a value-free Chrome credential reconciliation preview."""

    items = _read_chrome_passwords(profile_dir, safe_storage_password)
    plan = _chrome_password_plan(profile_dir, broker, items)
    source_by_id = {_chrome_credential_id(item): item for item in items}
    existing_by_id = {}
    for metadata in broker.list_credentials():
        provenance = metadata.get("provenance")
        if not isinstance(provenance, dict):
            continue
        if (
            provenance.get("source") == "chrome-password-manager"
            and provenance.get("source_profile_id") == plan["source_profile_id"]
        ):
            existing_by_id[metadata["credential_id"]] = metadata

    def metadata_for(credential_id: str) -> dict:
        item = source_by_id.get(credential_id) or existing_by_id.get(credential_id)
        if not item:
            return {"credential_id": credential_id}
        return {
            "credential_id": credential_id,
            "name": item.get("name")
            or _credential_name(item.get("hostname", "Chrome login")),
            "origin": item.get("origin", ""),
            "username": item.get("username", ""),
        }

    return {
        "status": "preview",
        "processed_count": plan["processed_count"],
        "unique_count": plan["unique_count"],
        "add_count": len(plan["added"]),
        "update_count": len(plan["updated"]),
        "unchanged_count": len(plan["unchanged"]),
        "stale_count": len(plan["stale"]),
        "added": [metadata_for(credential_id) for credential_id in plan["added"]],
        "updated": [
            metadata_for(credential_id) for credential_id in plan["updated"]
        ],
        "unchanged": [
            metadata_for(credential_id) for credential_id in plan["unchanged"]
        ],
        "stale": [metadata_for(credential_id) for credential_id in plan["stale"]],
    }


def sync_chrome_password_manager(
    profile_dir: str | Path,
    broker: Broker,
    *,
    approved: bool = False,
    remove_stale: bool = False,
    safe_storage_password: str | None = None,
) -> dict:
    """Reconcile Chrome credentials after explicit approval.

    Stale records are reported by default and removed only when the caller
    separately opts into ``remove_stale``.
    """

    if not approved:
        raise ChromeImportError(
            "Chrome Password Manager sync requires explicit approval"
        )
    items = _read_chrome_passwords(profile_dir, safe_storage_password)
    plan = _chrome_password_plan(profile_dir, broker, items)
    source_profile_id = plan["source_profile_id"]
    source_by_id = {_chrome_credential_id(item): item for item in items}
    for credential_id in plan["added"] + plan["updated"]:
        item = source_by_id[credential_id]
        broker.put_credential(
            credential_id,
            _credential_name(item["hostname"]),
            item["origin"],
            item["username"],
            item["password"],
            allowed_origins=[item["origin"]],
            provenance={
                "source": "chrome-password-manager",
                "source_profile_id": source_profile_id,
            },
        )
    removed = 0
    if remove_stale:
        for credential_id in plan["stale"]:
            broker.delete_record("credential", credential_id)
            removed += 1
    return {
        "status": "complete",
        "processed_count": plan["processed_count"],
        "unique_count": plan["unique_count"],
        "added": len(plan["added"]),
        "updated": len(plan["updated"]),
        "unchanged": len(plan["unchanged"]),
        "stale": len(plan["stale"]),
        "removed": removed,
    }


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
            ident = _opaque_hash_id(url, username)
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
