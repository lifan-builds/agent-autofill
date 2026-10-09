"""Read-only Thunderbird OAuth account discovery and verification.

Thunderbird stores OAuth refresh tokens in NSS-encrypted ``logins.json``.
This module decrypts them only in memory, selects the configured IMAP mailbox
read-only, and immediately discards the plaintext references.  It never copies
mail, changes the Thunderbird profile, or writes tokens to Agent Autofill.
"""

from __future__ import annotations

import ast
import base64
import configparser
import ctypes
from ctypes import POINTER, Structure, c_char_p, c_int, c_ubyte, c_uint, c_void_p
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Callable, Iterable
import zipfile

from .google_oauth import GoogleOAuthTokenProvider, MicrosoftOAuthTokenProvider
from .imap_adapter import (
    IMAPAdapterError,
    IMAPAuthenticationError,
    IMAPConfigurationError,
    IMAPConnectionError,
    IMAPEmailAdapter,
    IMAPMailboxError,
)


DEFAULT_PROFILE_ROOT = Path.home() / "Library" / "Thunderbird"
DEFAULT_THUNDERBIRD_APP = Path("/Applications/Thunderbird.app/Contents/MacOS")
GOOGLE_CLIENT_ID = (
    "406964657835-aq8lmia8j95dhl1a2bvharmfk3t1hgqj.apps.googleusercontent.com"
)
MICROSOFT_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
GOOGLE_TOKEN_ENDPOINT = "https://www.googleapis.com/oauth2/v3/token"
MICROSOFT_TOKEN_ENDPOINT = (
    "https://login.microsoftonline.com/common/oauth2/v2.0/token"
)
OAUTH_AUTH_METHOD = 10


class ThunderbirdError(RuntimeError):
    """Base class for safe Thunderbird operator-facing failures."""


class ThunderbirdProfileError(ThunderbirdError):
    """The selected profile is missing or cannot be read safely."""


class ThunderbirdNSSUnavailable(ThunderbirdError):
    """Thunderbird's NSS library could not be loaded or initialized."""


class ThunderbirdCredentialError(ThunderbirdError):
    """A saved Thunderbird login could not be decrypted."""


@dataclass(frozen=True)
class ThunderbirdAccount:
    account_id: str
    server_id: str
    host: str
    username: str
    port: int = 993
    mailbox: str = "INBOX"
    auth_method: int | None = None

    @property
    def provider(self) -> str | None:
        host = self.host.lower()
        if host == "imap.gmail.com":
            return "google"
        if host == "outlook.office365.com":
            return "microsoft"
        return None

    @property
    def masked_username(self) -> str:
        if "@" not in self.username:
            return self.username[:1] + "***"
        local, domain = self.username.split("@", 1)
        return (local[:1] + "***@" + domain) if local else "***@" + domain

    @property
    def source_id(self) -> str:
        return f"thunderbird-{self.account_id}"


@dataclass(frozen=True)
class ThunderbirdAccountResult:
    account_id: str
    username: str
    host: str
    provider: str | None
    status: str
    reason: str | None = None

    def public_dict(self) -> dict[str, Any]:
        account = ThunderbirdAccount(self.account_id, "", self.host, self.username)
        result = {
            "account_id": self.account_id,
            "username": account.masked_username,
            "host": self.host,
            "provider": self.provider,
            "status": self.status,
        }
        if self.reason:
            result["reason"] = self.reason
        return result


@dataclass(frozen=True)
class ThunderbirdTestRun:
    results: tuple[ThunderbirdAccountResult, ...]
    excluded_local_folders: int = 0

    def public_dict(self) -> dict[str, Any]:
        return {
            "accounts": [item.public_dict() for item in self.results],
            "excluded_local_folders": self.excluded_local_folders,
        }


class SECItem(Structure):
    _fields_ = [("type", c_uint), ("data", POINTER(c_ubyte)), ("len", c_uint)]


def parse_prefs(path: str | Path) -> dict[str, object]:
    """Parse simple Thunderbird ``user_pref`` assignments without executing code."""

    try:
        lines = Path(path).expanduser().read_text(errors="replace").splitlines()
    except (OSError, TypeError, ValueError) as exc:
        raise ThunderbirdProfileError("Could not read Thunderbird preferences") from exc
    prefs: dict[str, object] = {}
    for line in lines:
        match = re.match(r'user_pref\("([^"]+)",\s*(.*?)\);', line)
        if not match:
            continue
        try:
            prefs[match.group(1)] = ast.literal_eval(match.group(2))
        except (SyntaxError, ValueError):
            prefs[match.group(1)] = match.group(2)
    return prefs


def discover_profile(root: str | Path | None = None) -> Path:
    """Find a Thunderbird profile without starting Thunderbird."""

    root_path = Path(root).expanduser() if root else DEFAULT_PROFILE_ROOT
    profiles_ini = root_path / "profiles.ini"
    candidates: list[Path] = []
    if profiles_ini.exists():
        parser = configparser.RawConfigParser()
        try:
            parser.read(profiles_ini)
        except (configparser.Error, OSError) as exc:
            raise ThunderbirdProfileError("Could not read Thunderbird profiles") from exc
        for section in parser.sections():
            if not section.lower().startswith("profile"):
                continue
            value = parser.get(section, "Path", fallback="")
            if not value:
                continue
            profile = Path(value)
            candidate = profile if not parser.getboolean(section, "IsRelative", fallback=True) else root_path / profile
            if (candidate / "prefs.js").exists():
                candidates.append(candidate)
    if not candidates:
        candidates = [
            path
            for path in (root_path / "Profiles").glob("*")
            if (path / "prefs.js").exists()
        ]
    if not candidates:
        raise ThunderbirdProfileError("No Thunderbird profile was found")
    candidates.sort(key=lambda path: ("default-release" not in path.name, str(path)))
    return candidates[0]


def list_accounts(profile: str | Path) -> tuple[ThunderbirdAccount, ...]:
    """Return external IMAP metadata while excluding Local Folders."""

    profile_path = Path(profile).expanduser()
    prefs = parse_prefs(profile_path / "prefs.js")
    account_ids = [
        item for item in str(prefs.get("mail.accountmanager.accounts", "")).split(",") if item
    ]
    accounts: list[ThunderbirdAccount] = []
    for account_id in account_ids:
        server_id = prefs.get(f"mail.account.{account_id}.server")
        host = prefs.get(f"mail.server.{server_id}.hostname") if isinstance(server_id, str) else None
        username = prefs.get(f"mail.server.{server_id}.userName") if isinstance(server_id, str) else None
        if not isinstance(server_id, str) or not isinstance(host, str) or host.lower() == "local folders":
            continue
        if not isinstance(username, str) or not username:
            continue
        port = prefs.get(f"mail.server.{server_id}.port", 993)
        if not isinstance(port, int) or isinstance(port, bool):
            port = 993
        auth_method = prefs.get(f"mail.server.{server_id}.authMethod")
        if not isinstance(auth_method, int) or isinstance(auth_method, bool):
            auth_method = None
        accounts.append(
            ThunderbirdAccount(account_id, server_id, host, username, port, "INBOX", auth_method)
        )
    return tuple(accounts)


class _NSS:
    """Minimal ctypes boundary for Thunderbird's profile decryption APIs."""

    def __init__(self, profile: Path, app_dir: str | Path | None = None):
        app = Path(app_dir).expanduser() if app_dir else DEFAULT_THUNDERBIRD_APP
        try:
            ctypes.CDLL(str(app / "libmozglue.dylib"), mode=ctypes.RTLD_GLOBAL)
            self._lib = ctypes.CDLL(str(app / "libnss3.dylib"), mode=ctypes.RTLD_GLOBAL)
            self._lib.NSS_Init.argtypes = [c_char_p]
            self._lib.NSS_Init.restype = c_int
            self._lib.NSS_Shutdown.argtypes = []
            self._lib.NSS_Shutdown.restype = c_int
            self._lib.PK11SDR_Decrypt.argtypes = [POINTER(SECItem), POINTER(SECItem), c_void_p]
            self._lib.PK11SDR_Decrypt.restype = c_int
        except (OSError, AttributeError, TypeError) as exc:
            raise ThunderbirdNSSUnavailable("Thunderbird NSS is unavailable") from exc
        if self._lib.NSS_Init(str(profile).encode()) != 0:
            raise ThunderbirdNSSUnavailable("Could not open Thunderbird profile encryption")
        self._closed = False

    def decrypt(self, encoded: str) -> str:
        try:
            raw = base64.b64decode(encoded, validate=True)
            buf = (c_ubyte * len(raw)).from_buffer_copy(raw)
            incoming = SECItem(0, ctypes.cast(buf, POINTER(c_ubyte)), len(raw))
            outgoing = SECItem()
            if self._lib.PK11SDR_Decrypt(ctypes.byref(incoming), ctypes.byref(outgoing), None) != 0:
                raise ThunderbirdCredentialError("Thunderbird login decryption failed")
            return bytes(outgoing.data[: outgoing.len]).decode()
        except ThunderbirdCredentialError:
            raise
        except (ValueError, UnicodeDecodeError, TypeError, ctypes.ArgumentError) as exc:
            raise ThunderbirdCredentialError("Thunderbird login decryption failed") from exc

    def close(self) -> None:
        if not self._closed:
            self._lib.NSS_Shutdown()
            self._closed = True

    def __enter__(self) -> "_NSS":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()


def _credentials(profile: Path, nss: _NSS) -> dict[str, str]:
    try:
        payload = json.loads((profile / "logins.json").read_text())
        logins = payload["logins"]
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ThunderbirdCredentialError("Thunderbird saved logins are unavailable") from exc
    if not isinstance(logins, list):
        raise ThunderbirdCredentialError("Thunderbird saved logins are invalid")
    result: dict[str, str] = {}
    for login in logins:
        if not isinstance(login, dict):
            continue
        encrypted_user = login.get("encryptedUsername")
        encrypted_password = login.get("encryptedPassword")
        if not isinstance(encrypted_user, str) or not isinstance(encrypted_password, str):
            continue
        username = nss.decrypt(encrypted_user)
        if username not in result:
            result[username] = nss.decrypt(encrypted_password)
    return result


def _google_client_secret(app_dir: str | Path | None = None) -> str | None:
    """Read Thunderbird's built-in client secret at runtime, never from Git."""

    app = Path(app_dir).expanduser() if app_dir else DEFAULT_THUNDERBIRD_APP
    archive = app.parent / "Resources" / "omni.ja"
    try:
        with zipfile.ZipFile(archive) as bundle:
            source = bundle.read("modules/OAuth2Providers.sys.mjs").decode("utf-8", "replace")
    except (OSError, KeyError, zipfile.BadZipFile, UnicodeError):
        return None
    match = re.search(
        r'"accounts\.google\.com"[\s\S]{0,1000}?clientSecret:\s*"([^"]+)"',
        source,
    )
    return match.group(1) if match else None


def _provider_for(account: ThunderbirdAccount, refresh_token: str) -> object | None:
    if account.provider == "google":
        return GoogleOAuthTokenProvider(
            GOOGLE_CLIENT_ID,
            _google_client_secret(),
            refresh_token,
            token_endpoint=GOOGLE_TOKEN_ENDPOINT,
        )
    if account.provider == "microsoft":
        return MicrosoftOAuthTokenProvider(
            MICROSOFT_CLIENT_ID,
            None,
            refresh_token,
            token_endpoint=MICROSOFT_TOKEN_ENDPOINT,
        )
    return None


def _failure_reason(exc: BaseException) -> str:
    if isinstance(exc, IMAPMailboxError):
        return "mailbox_selection_failed"
    if isinstance(exc, IMAPAuthenticationError):
        return "authentication_failed"
    if isinstance(exc, IMAPConnectionError):
        return "connection_failed"
    if isinstance(exc, IMAPConfigurationError):
        return "configuration_failed"
    if isinstance(exc, IMAPAdapterError):
        return "imap_failed"
    return "oauth_or_runtime_failed"


def verify_profile(
    profile: str | Path,
    *,
    account_ids: Iterable[str] | None = None,
    nss_factory: Callable[[Path], _NSS] = _NSS,
    adapter_factory: Callable[..., IMAPEmailAdapter] = IMAPEmailAdapter,
    verified_out: dict[str, tuple[ThunderbirdAccount, object]] | None = None,
) -> ThunderbirdTestRun:
    """Verify selected accounts without returning or persisting credentials.

    ``verified_out`` is an internal import boundary. When supplied, it receives
    only in-memory providers for accounts that passed the check; callers must
    serialize them through the OS Keychain boundary immediately.
    """

    profile_path = Path(profile).expanduser()
    prefs = parse_prefs(profile_path / "prefs.js")
    all_account_ids = [
        item
        for item in str(prefs.get("mail.accountmanager.accounts", "")).split(",")
        if item
    ]
    excluded_local_folders = 0
    for account_id in all_account_ids:
        server_id = prefs.get(f"mail.account.{account_id}.server")
        host = (
            prefs.get(f"mail.server.{server_id}.hostname")
            if isinstance(server_id, str)
            else None
        )
        if isinstance(host, str) and host.lower() == "local folders":
            excluded_local_folders += 1
    accounts: list[ThunderbirdAccount] = list(list_accounts(profile_path))
    selected = set(account_ids) if account_ids is not None else None
    if selected is not None:
        unknown = selected - {item.account_id for item in accounts}
        if unknown:
            raise ThunderbirdProfileError("Unknown Thunderbird account selection")
        accounts = [item for item in accounts if item.account_id in selected]
    results: list[ThunderbirdAccountResult] = []
    with nss_factory(profile_path) as nss:
        credentials = _credentials(profile_path, nss)
        for account in accounts:
            if account.auth_method not in {None, OAUTH_AUTH_METHOD}:
                results.append(ThunderbirdAccountResult(account.account_id, account.username, account.host, account.provider, "SKIP", "unsupported_auth_method"))
                continue
            if account.provider is None:
                results.append(ThunderbirdAccountResult(account.account_id, account.username, account.host, None, "SKIP", "unsupported_oauth_host"))
                continue
            refresh_token = credentials.get(account.username)
            if not refresh_token:
                results.append(ThunderbirdAccountResult(account.account_id, account.username, account.host, account.provider, "FAIL", "no_saved_credential"))
                continue
            try:
                provider = _provider_for(account, refresh_token)
                if provider is None:
                    raise ThunderbirdProfileError("Unsupported Thunderbird OAuth provider")
                adapter = adapter_factory(account.account_id, account.host, account.username, None, account.port, account.mailbox, None, provider)
                adapter.check_connection()
                status, reason = "PASS", None
            except Exception as exc:
                status, reason = "FAIL", _failure_reason(exc)
            results.append(ThunderbirdAccountResult(account.account_id, account.username, account.host, account.provider, status, reason))
            if status == "PASS" and verified_out is not None:
                verified_out[account.account_id] = (account, provider)
        credentials.clear()
    return ThunderbirdTestRun(tuple(results), excluded_local_folders)


def test_profile(profile: str | Path, *, account_ids: Iterable[str] | None = None) -> ThunderbirdTestRun:
    return verify_profile(profile, account_ids=account_ids)


__all__ = [
    "ThunderbirdAccount",
    "ThunderbirdAccountResult",
    "ThunderbirdCredentialError",
    "ThunderbirdError",
    "ThunderbirdNSSUnavailable",
    "ThunderbirdProfileError",
    "ThunderbirdTestRun",
    "discover_profile",
    "list_accounts",
    "parse_prefs",
    "test_profile",
    "verify_profile",
]
