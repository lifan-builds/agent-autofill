"""Safe Chrome cold-start and Password Manager import helpers.

Structured profile data is read without mutation. Password Manager imports
require explicit approval, use the macOS Chrome Safe Storage key, and seal
decrypted values immediately in the broker; plaintext passwords never enter
metadata listings, audits, or diagnostics.
"""

import csv
import hashlib
import hmac
import platform
import re
import shutil
import sqlite3
import subprocess
import tempfile
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


def default_profile(root: str | Path | None = None) -> Path:
    """Resolve Chrome's single supported profile: ``Default``.

    ``root`` may be either the Chrome user-data directory or an explicit
    ``Default`` directory.  A named secondary profile is rejected instead of
    being silently used.  This keeps onboarding deterministic and prevents a
    profile containing another person's data from being imported by accident.
    """

    if root is not None:
        candidate = Path(root).expanduser()
        if candidate.name == "Default":
            profile = candidate
        else:
            if candidate.name.startswith("Profile "):
                raise ChromeImportError(
                    "Only Chrome's Default profile is supported; select the Default profile"
                )
            profile = candidate / "Default"
    elif platform.system() == "Darwin":
        profile = Path.home() / "Library/Application Support/Google/Chrome/Default"
    elif platform.system() == "Windows":
        profile = Path.home() / "AppData/Local/Google/Chrome/User Data/Default"
    else:
        profile = Path.home() / ".config/google-chrome/Default"
        if not profile.is_dir():
            profile = Path.home() / ".config/chromium/Default"
    if not profile.is_dir():
        raise ChromeImportError(
            "Chrome Default profile was not found; open Chrome once and retry"
        )
    return profile


def _require_default_profile(profile_dir: str | Path) -> Path:
    """Validate an explicit profile path before touching any Chrome data."""

    profile = Path(profile_dir).expanduser()
    if not (profile.name == "Default" or re.fullmatch(r"Profile \d+", profile.name)):
        raise ChromeImportError(
            "Only Chrome's Default or numbered Profile directories are supported; select Default or Profile N"
        )
    if not profile.is_dir():
        raise ChromeImportError(f"Chrome Default profile directory not found: {profile}")
    return profile


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
    with tempfile.NamedTemporaryFile(
        prefix="agent-autofill-chrome-", suffix=".db", delete=False
    ) as tmp:
        snapshot = Path(tmp.name)
    try:
        shutil.copy2(path, snapshot)
    except OSError:
        snapshot.unlink(missing_ok=True)
        raise
    return snapshot


def _columns(db, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"pragma table_info({table})")}


def read_autofill(profile_dir: str | Path) -> list[dict]:
    profile_dir = _require_default_profile(profile_dir)
    data_path = profile_dir / "Web Data"
    if not data_path.exists():
        return []
    snapshot = _snapshot(data_path)
    db = None
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
            if db is not None:
                db.close()
        finally:
            snapshot.unlink(missing_ok=True)


def read_login_metadata(profile_dir: str | Path) -> list[dict]:
    profile_dir = _require_default_profile(profile_dir)
    data_path = profile_dir / "Login Data"
    if not data_path.exists():
        return []
    snapshot = _snapshot(data_path)
    db = None
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
            if db is not None:
                db.close()
        finally:
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

    profile_path = _require_default_profile(profile_dir)
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
                    _ = parsed.port
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
        if isinstance(password, str) and hmac.compare_digest(
            password.encode("utf-8"), item["password"].encode("utf-8")
        ):
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


def _detect_card_network(digits: str) -> str | None:
    if digits.startswith("4"):
        return "Visa"
    if len(digits) >= 2 and (
        "51" <= digits[:2] <= "55"
        or (len(digits) >= 4 and "2221" <= digits[:4] <= "2720")
    ):
        return "Mastercard"
    if digits.startswith(("34", "37")):
        return "American Express"
    if digits.startswith(("6011", "65")) or (
        len(digits) >= 3 and "644" <= digits[:3] <= "649"
    ):
        return "Discover"
    return None


def _chrome_card_record_id(source_profile_id: str, guid: str) -> str:
    return "chrome_card_" + _opaque_hash_id(source_profile_id, guid)


def _read_chrome_payment_cards(
    profile_dir: str | Path,
    safe_storage_password: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """Read local encrypted Chrome cards in memory plus masked cloud-card metadata."""

    profile_path = _require_default_profile(profile_dir)
    data_path = profile_path / "Web Data"
    if not data_path.exists():
        return ([], [])
    snapshot = _snapshot(data_path)
    db: sqlite3.Connection | None = None
    local_cards: list[dict] = []
    masked_cloud_cards: list[dict] = []
    source_profile_id = _profile_source_id(profile_dir)
    try:
        db = sqlite3.connect(snapshot)
        tables = {
            row[0]
            for row in db.execute(
                "select name from sqlite_master where type='table'"
            )
        }
        cvc_by_guid: dict[str, bytes] = {}
        if "local_stored_cvc" in tables:
            cvc_cols = _columns(db, "local_stored_cvc")
            if {"guid", "value_encrypted"}.issubset(cvc_cols):
                for guid, enc_cvc in db.execute(
                    "select guid, value_encrypted from local_stored_cvc "
                    "where guid is not null and value_encrypted is not null"
                ).fetchall():
                    if isinstance(guid, str) and isinstance(
                        enc_cvc, (bytes, memoryview)
                    ):
                        cvc_by_guid[guid] = (
                            enc_cvc.tobytes()
                            if isinstance(enc_cvc, memoryview)
                            else enc_cvc
                        )

        postal_by_guid: dict[str, str] = {}
        if "address_type_tokens" in tables:
            at_cols = _columns(db, "address_type_tokens")
            if {"guid", "type", "value"}.issubset(at_cols):
                for addr_guid, raw_postal in db.execute(
                    "select guid, value from address_type_tokens "
                    "where type = 35 and guid is not null and value is not null"
                ).fetchall():
                    if (
                        isinstance(addr_guid, str)
                        and isinstance(raw_postal, str)
                        and addr_guid.strip()
                        and raw_postal.strip()
                    ):
                        postal_by_guid[addr_guid.strip()] = raw_postal.strip()

        if "credit_cards" in tables:
            cols = _columns(db, "credit_cards")
            required = {
                "guid",
                "name_on_card",
                "expiration_month",
                "expiration_year",
                "card_number_encrypted",
            }
            if required.issubset(cols):
                nick_expr = "nickname" if "nickname" in cols else "''"
                bill_expr = (
                    "billing_address_id" if "billing_address_id" in cols else "''"
                )
                rows = db.execute(
                    f"select guid, name_on_card, expiration_month, expiration_year, "
                    f"card_number_encrypted, {nick_expr}, {bill_expr} from credit_cards "
                    f"where card_number_encrypted is not null "
                    f"and length(card_number_encrypted) > 3"
                ).fetchall()
                if rows:
                    key = _macos_chrome_key(
                        safe_storage_password
                        if safe_storage_password is not None
                        else _macos_safe_storage_password()
                    )
                    for (
                        guid,
                        name_on_card,
                        exp_month,
                        exp_year,
                        enc_number,
                        nickname,
                        billing_addr_id,
                    ) in rows:
                        if not isinstance(guid, str) or not guid.strip():
                            continue
                        raw_pan = _decrypt_macos_password(enc_number, key)
                        digits = re.sub(r"[\s-]+", "", raw_pan)
                        if not re.fullmatch(r"\d{12,19}", digits):
                            continue
                        month_int = int(exp_month) if exp_month is not None else 0
                        year_int = int(exp_year) if exp_year is not None else 0
                        if not (1 <= month_int <= 12 and 2000 <= year_int <= 2199):
                            continue
                        month_str = f"{month_int:02d}"
                        year_str = f"{year_int:04d}"
                        cvc_str: str | None = None
                        if guid in cvc_by_guid and len(cvc_by_guid[guid]) > 3:
                            decrypted_cvc = _decrypt_macos_password(
                                cvc_by_guid[guid], key
                            ).strip()
                            if re.fullmatch(r"\d{3,4}", decrypted_cvc):
                                cvc_str = decrypted_cvc
                        clean_nick = (
                            nickname.strip()
                            if isinstance(nickname, str) and nickname.strip()
                            else None
                        )
                        billing_postal_code = (
                            postal_by_guid.get(billing_addr_id.strip())
                            if isinstance(billing_addr_id, str)
                            and billing_addr_id.strip()
                            else None
                        )
                        network = _detect_card_network(digits)
                        display_name = clean_nick or (
                            f"{network or 'Card'} ending in {digits[-4:]}"
                        )
                        local_cards.append(
                            {
                                "record_id": _chrome_card_record_id(
                                    source_profile_id, guid
                                ),
                                "guid": guid,
                                "display_name": display_name,
                                "nickname": clean_nick,
                                "cardholder_name": (
                                    name_on_card.strip()
                                    if isinstance(name_on_card, str)
                                    and name_on_card.strip()
                                    else None
                                ),
                                "billing_postal_code": billing_postal_code,
                                "card_number": digits,
                                "last4": digits[-4:],
                                "expiration_month": month_str,
                                "expiration_year": year_str,
                                "security_code": cvc_str,
                                "network": network,
                                "source_profile_id": source_profile_id,
                            }
                        )

        if "masked_credit_cards" in tables:
            mcols = _columns(db, "masked_credit_cards")
            if {"last_four", "exp_month", "exp_year"}.issubset(mcols):
                net_col = "network" if "network" in mcols else "''"
                nick_col = "nickname" if "nickname" in mcols else "''"
                issuer_col = "card_issuer_id" if "card_issuer_id" in mcols else "''"
                bank_col = "bank_name" if "bank_name" in mcols else "''"
                prod_col = (
                    "product_description" if "product_description" in mcols else "''"
                )
                for last4, em, ey, net, nick, issuer, bank_name, prod_desc in db.execute(
                    f"select last_four, exp_month, exp_year, {net_col}, "
                    f"{nick_col}, {issuer_col}, {bank_col}, {prod_col} "
                    f"from masked_credit_cards"
                ).fetchall():
                    if not isinstance(last4, str) or not re.fullmatch(
                        r"\d{4}", last4.strip()
                    ):
                        continue
                    clean_nick_m = (
                        nick.strip()
                        if isinstance(nick, str) and nick.strip()
                        else (
                            prod_desc.strip()
                            if isinstance(prod_desc, str) and prod_desc.strip()
                            else None
                        )
                    )
                    clean_issuer_m = (
                        bank_name.strip()
                        if isinstance(bank_name, str) and bank_name.strip()
                        else (
                            issuer.strip()
                            if isinstance(issuer, str) and issuer.strip()
                            else None
                        )
                    )
                    masked_cloud_cards.append(
                        {
                            "last4": last4.strip(),
                            "expiration_month": (
                                f"{int(em):02d}"
                                if em is not None and 1 <= int(em) <= 12
                                else None
                            ),
                            "expiration_year": (
                                f"{int(ey):04d}"
                                if ey is not None and 2000 <= int(ey) <= 2199
                                else None
                            ),
                            "network": (
                                net.strip()
                                if isinstance(net, str) and net.strip()
                                else None
                            ),
                            "nickname": clean_nick_m,
                            "issuer": clean_issuer_m,
                            "importable": False,
                            "reason": "Google Pay cloud-synced card stores only masked metadata locally; add once via agent-autofill payment-card-add",
                        }
                    )
    except ChromeImportError:
        raise
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise ChromeImportError(
            "Chrome Web Data could not be read; close Chrome and retry"
        ) from exc
    finally:
        if db is not None:
            db.close()
        snapshot.unlink(missing_ok=True)
    return (local_cards, masked_cloud_cards)


def preview_chrome_payment_cards(
    profile_dir: str | Path,
    broker: Broker,
    *,
    safe_storage_password: str | None = None,
) -> dict:
    """Return a value-free preview of local Chrome payment cards and cloud-masked cards."""

    local_cards, masked_cloud_cards = _read_chrome_payment_cards(
        profile_dir, safe_storage_password
    )
    added = []
    updated = []
    unchanged = []
    for item in local_cards:
        rid = item["record_id"]
        summary = {
            "record_id": rid,
            "display_name": item["display_name"],
            "nickname": item["nickname"],
            "network": item["network"],
            "last4": item["last4"],
            "expiration_month": item["expiration_month"],
            "expiration_year": item["expiration_year"],
            "has_security_code": bool(item["security_code"]),
            "has_billing_postal_code": bool(item.get("billing_postal_code")),
        }
        stored = broker.store.get("financial", rid)
        if stored is None:
            added.append(summary)
            continue
        stored_values = stored.get("values") if isinstance(stored, dict) else {}
        if not isinstance(stored_values, dict):
            stored_values = {}
        same_pan = hmac.compare_digest(
            str(stored_values.get("card_number", "")).encode("utf-8"),
            item["card_number"].encode("utf-8"),
        )
        same_exp = (
            stored_values.get("expiration_month") == item["expiration_month"]
            and stored_values.get("expiration_year") == item["expiration_year"]
        )
        same_cvc = (
            item["security_code"] is None
            or stored_values.get("security_code") == item["security_code"]
        )
        same_postal = (
            not item.get("billing_postal_code")
            or stored_values.get("billing_postal_code")
            == item["billing_postal_code"]
        )
        if same_pan and same_exp and same_cvc and same_postal:
            unchanged.append(summary)
        else:
            updated.append(summary)
    return {
        "status": "preview",
        "local_card_count": len(local_cards),
        "add_count": len(added),
        "update_count": len(updated),
        "unchanged_count": len(unchanged),
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
        "masked_cloud_card_count": len(masked_cloud_cards),
        "masked_cloud_cards": masked_cloud_cards,
    }


def _clean_card_meta_arg(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if re.search(r"\d{8,}", cleaned):
        raise ChromeImportError(f"{label} must not contain raw account numbers")
    return cleaned


def sync_chrome_payment_cards(
    profile_dir: str | Path,
    broker: Broker,
    *,
    approved: bool = False,
    record_ids: list[str] | None = None,
    default_record_id: str | None = None,
    set_default: bool = False,
    hint: str | None = None,
    institution: str | None = None,
    display_name: str | None = None,
    safe_storage_password: str | None = None,
) -> dict:
    """Import approved local Chrome payment cards directly into the encrypted vault."""

    if not approved:
        raise ChromeImportError(
            "Chrome payment card sync requires explicit approval"
        )
    clean_hint = _clean_card_meta_arg(hint, "card hint")
    clean_institution = _clean_card_meta_arg(institution, "card institution")
    clean_display_name = _clean_card_meta_arg(display_name, "card display name")
    clean_default_selector = (
        default_record_id.strip()
        if isinstance(default_record_id, str) and default_record_id.strip()
        else None
    )

    local_cards, masked_cloud_cards = _read_chrome_payment_cards(
        profile_dir, safe_storage_password
    )
    selected_ids = set(record_ids) if record_ids is not None else None
    selected_cards = [
        item
        for item in local_cards
        if selected_ids is None or item["record_id"] in selected_ids
    ]
    if selected_ids is not None:
        found_ids = {item["record_id"] for item in selected_cards}
        if selected_ids - found_ids:
            raise ChromeImportError(
                "Requested --record-id was not found in local Chrome payment cards"
            )

    default_target_rid: str | None = None
    if clean_default_selector is not None:
        selector_cf = clean_default_selector.casefold()
        matched_cards = [
            item
            for item in selected_cards
            if item["record_id"] == clean_default_selector
            or (
                item["nickname"] is not None
                and item["nickname"].casefold() == selector_cf
            )
            or (
                bool(re.fullmatch(r"\d{4}", clean_default_selector))
                and item["last4"] == clean_default_selector
            )
        ]
        if not matched_cards:
            raise ChromeImportError(
                "No imported Chrome payment card matched --default-card; "
                "use --default --hint <alias> for a single local card or pass --record-id from --preview"
            )
        if len(matched_cards) > 1:
            raise ChromeImportError(
                "Multiple Chrome payment cards matched --default-card; specify --record-id from --preview"
            )
        default_target_rid = matched_cards[0]["record_id"]
    elif set_default:
        if not selected_cards:
            raise ChromeImportError(
                "No local Chrome payment cards found to mark as default"
            )
        if len(selected_cards) > 1:
            raise ChromeImportError(
                "Multiple local Chrome payment cards found; pass --record-id or --default-card to choose the default card"
            )
        default_target_rid = selected_cards[0]["record_id"]

    override_target_rid: str | None = default_target_rid
    if (
        clean_hint is not None
        or clean_institution is not None
        or clean_display_name is not None
    ):
        if override_target_rid is None:
            if len(selected_cards) == 1:
                override_target_rid = selected_cards[0]["record_id"]
            elif len(selected_cards) > 1:
                raise ChromeImportError(
                    "Multiple local Chrome payment cards found; pass --record-id or --default-card when setting --hint, --institution, or --display-name"
                )

    added_count = 0
    updated_count = 0
    unchanged_count = 0

    for item in selected_cards:
        rid = item["record_id"]
        stored = broker.store.get("financial", rid)
        mark_default = rid == default_target_rid
        apply_overrides = rid == override_target_rid
        if stored is not None and isinstance(stored, dict):
            stored_defaults = broker._financial_defaults(rid, stored)
            preserve_default = mark_default or bool(stored_defaults.get("is_default"))
            preserve_origins = list(stored_defaults.get("allowed_origins", []))
            preserve_purposes = list(stored_defaults.get("allowed_purposes", []))
            existing_hints = dict(stored_defaults.get("matching_hints", {}))
            stored_values = dict(stored_defaults.get("values", {}))
            existing_display_name = str(
                stored_defaults.get("display_name") or item["display_name"]
            )
        else:
            preserve_default = mark_default
            preserve_origins = []
            preserve_purposes = []
            existing_hints = {}
            stored_values = {}
            existing_display_name = item["display_name"]

        values = dict(stored_values)
        values["card_number"] = item["card_number"]
        values["expiration_month"] = item["expiration_month"]
        values["expiration_year"] = item["expiration_year"]
        if item["security_code"]:
            values["security_code"] = item["security_code"]
        if item["cardholder_name"]:
            values["cardholder_name"] = item["cardholder_name"]
        if item.get("billing_postal_code"):
            values["billing_postal_code"] = item["billing_postal_code"]

        hints: dict[str, str] = {}
        for hk in ("hint", "aliases", "institution", "nickname", "network"):
            if hk in existing_hints and existing_hints[hk]:
                hints[hk] = existing_hints[hk]
        if item["network"]:
            hints["network"] = item["network"]
        if item["nickname"]:
            hints["nickname"] = item["nickname"]
            hints.setdefault("hint", item["nickname"])
        if apply_overrides:
            if clean_hint is not None:
                hints["hint"] = clean_hint
            if clean_institution is not None:
                hints["institution"] = clean_institution
        effective_display_name = (
            clean_display_name
            if (apply_overrides and clean_display_name is not None)
            else existing_display_name
        )

        if stored is not None:
            same_vals = (
                hmac.compare_digest(
                    str(stored_values.get("card_number", "")).encode("utf-8"),
                    item["card_number"].encode("utf-8"),
                )
                and stored_values.get("expiration_month") == item["expiration_month"]
                and stored_values.get("expiration_year") == item["expiration_year"]
                and (
                    not item["security_code"]
                    or stored_values.get("security_code") == item["security_code"]
                )
                and (
                    not item.get("billing_postal_code")
                    or stored_values.get("billing_postal_code")
                    == item["billing_postal_code"]
                )
                and bool(stored.get("is_default")) == preserve_default
                and existing_display_name == effective_display_name
                and existing_hints == hints
            )
            if same_vals:
                unchanged_count += 1
                continue
            updated_count += 1
        else:
            added_count += 1

        broker.put_financial_record(
            record_id=rid,
            kind="payment_card",
            display_name=effective_display_name,
            values=values,
            matching_hints=hints or None,
            allowed_origins=preserve_origins,
            allowed_purposes=preserve_purposes,
            provenance={
                "source": "chrome-payment-cards",
                "source_profile_id": item["source_profile_id"],
            },
            is_default=preserve_default,
        )

    return {
        "status": "complete",
        "local_card_count": len(local_cards),
        "added": added_count,
        "updated": updated_count,
        "unchanged": unchanged_count,
        "masked_cloud_card_count": len(masked_cloud_cards),
    }


def import_autofill(profile_dir: str | Path, broker: Broker) -> int:
    profile_dir = _require_default_profile(profile_dir)
    count = 0
    for item in read_autofill(profile_dir):
        # Keep the historical identifier stable.  Only hashes that happen to
        # contain a long digit run need chunking for the broker's financial
        # number guard.
        digest = hashlib.sha256(item["name"].encode()).hexdigest()[:20]
        suffix = digest if not re.search(r"\d{8,}", digest) else _opaque_hash_id(item["name"])
        field_id = "autofill:" + suffix
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
    profile_dir = _require_default_profile(profile_dir)
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
    profile_dir = _require_default_profile(profile_dir)
    result = {
        "autofill_fields": import_autofill(profile_dir, broker),
        "login_metadata": import_login_metadata(profile_dir, broker),
        "passwords": 0,
    }
    if password_csv is not None:
        result["passwords"] = import_password_csv(password_csv, broker)
    return result
