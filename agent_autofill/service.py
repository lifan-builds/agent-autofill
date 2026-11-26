# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Agent Autofill; Section 7 attribution terms in ATTRIBUTION.md.
import hashlib
import hmac
import ipaddress
import re
import secrets
from datetime import UTC, datetime, timedelta
from email.utils import getaddresses
from itertools import islice
from typing import Any
from urllib.parse import urlparse

from .form_memory import (
    _CHROME_FIELD_SEMANTICS,
    build_group_summary,
    masked_recipient_initials,
    normalize_form_label,
    profile_group_id,
    safe_country_code,
    safe_state_code,
)
from .form_memory import (
    match_form_fields as _match_form_fields,
)
from .messages_adapter import _sender_matches_filter
from .models import EmailMessage, Grant, TextMessage, now_iso
from .storage import Store


class BrokerError(Exception):
    pass


class AuthorizationError(BrokerError):
    pass


class CredentialUseError(BrokerError):
    pass


class AuthCodeError(BrokerError):
    """Value-free selection failure and a concrete recovery action."""

    def __init__(self, reason: str, message: str, next_action: str):
        self.reason = reason
        self.next_action = next_action
        super().__init__(f"{message}. {next_action}")


_RECORD_TYPES = {"profile", "credential", "financial"}
_FINANCIAL_KINDS = {
    "payment_card",
    "bank_account",
    "financial_account",
    "custom_financial",
}
_MAX_BATCH_RECORDS = 100
_MAX_RECORD_ID_LENGTH = 128
_MAX_FIELD_ID_LENGTH = 128
_MAX_TEXT_LENGTH = 4096
_MAX_SCOPE_ITEMS = 100
_FORM_MAPPING_NAMESPACE = "form-mapping"
_FORM_MAPPING_OUTCOMES = {"accepted", "corrected"}
_EMAIL_GRANT_OPERATIONS = {
    "email.search",
    "email.read",
    "email.otp",
    "email.verification_link",
}
_TRUSTED_LOCAL_READ_OPERATIONS = {
    "profile.read",
    "credential.read",
    "credential.use",
    "financial.read",
    *_EMAIL_GRANT_OPERATIONS,
    "messages.search",
    "messages.list",
    "messages.read",
    "messages.otp",
}
_OTP_ORIGIN_POLICIES = {"target-origin", "mailbox-read"}
_MESSAGES_OTP_ORIGIN_POLICIES = {"target-origin", "messages-read"}
# Exact aliases documented by each service's verification-email domain.
_OTP_SENDER_DOMAINS = {
    "accountscenter.facebook.com": {"facebookmail.com", "email.meta.com", "account.meta.com"},
    "auth.openai.com": {"tm.openai.com", "tm1.openai.com"},
    "chatgpt.com": {"tm.openai.com", "tm1.openai.com"},
    "facebook.com": {"facebookmail.com", "email.meta.com", "account.meta.com"},
    "m.facebook.com": {"facebookmail.com", "email.meta.com", "account.meta.com"},
    "www.facebook.com": {"facebookmail.com", "email.meta.com", "account.meta.com"},
}
_SMS_OTP_BRAND_ALIASES = {
    "accounts.google.com": {"google"},
    "amazon.com": {"amazon", "aws"},
    "americanexpress.com": {"amex", "american express"},
    "apple.com": {"apple", "icloud"},
    "auth.openai.com": {"openai", "chatgpt"},
    "aws.amazon.com": {"amazon", "aws"},
    "bankofamerica.com": {"bank of america", "bofa"},
    "capitalone.com": {"capital one"},
    "chase.com": {"chase", "jpmorgan"},
    "chatgpt.com": {"openai", "chatgpt"},
    "citi.com": {"citi", "citibank"},
    "citibank.com": {"citi", "citibank"},
    "facebook.com": {"facebook", "meta"},
    "fb.com": {"facebook", "meta"},
    "google.com": {"google"},
    "icloud.com": {"apple", "icloud"},
    "instagram.com": {"instagram", "meta"},
    "live.com": {"microsoft"},
    "microsoft.com": {"microsoft"},
    "microsoftonline.com": {"microsoft"},
    "office.com": {"microsoft"},
    "openai.com": {"openai", "chatgpt"},
    "paypal.com": {"paypal"},
    "twitter.com": {"twitter"},
    "wellsfargo.com": {"wells fargo"},
    "x.com": {"twitter"},
}
_SMS_OTP_BRAND_STOPWORDS = {
    "and",
    "app",
    "auth",
    "code",
    "com",
    "for",
    "login",
    "mail",
    "net",
    "not",
    "one",
    "org",
    "otp",
    "pin",
    "sms",
    "sso",
    "the",
    "use",
    "web",
    "you",
    "your",
}
_FORBIDDEN_TRANSIENT_PARTS = {
    "access_token",
    "auth_code",
    "captcha",
    "cookie",
    "csrf",
    "credential_handle",
    "handle",
    "login_code",
    "one_time",
    "otp",
    "refresh_token",
    "session",
    "token",
    "verification_code",
}
_FORBIDDEN_SENSITIVE_PARTS = {
    "account_number",
    "card_number",
    "credential",
    "cvc",
    "cvv",
    "financial",
    "handle",
    "password",
    "payment",
    "routing_number",
    "security_code",
}
_FORBIDDEN_PROVENANCE_PARTS = _FORBIDDEN_TRANSIENT_PARTS | {
    "account_number",
    "card_number",
    "cvc",
    "cvv",
    "number",
    "password",
    "raw",
    "routing_number",
    "secret",
    "security_code",
    "value",
    "values",
}
_FORBIDDEN_PROFILE_CATEGORIES = {
    "credential",
    "financial",
    "handle",
    "otp",
    "password",
    "payment",
    "session",
    "token",
    "cookie",
}
_VERIFICATION_LINK_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_VERIFICATION_LINK_TRAILING = ".,;:!?)]}>'"
_AUTH_CODE_VALUE = r"(?<!\d)(?:G-)?(\d{4,8}|\d{3}[-\s]\d{3})(?!\d)"
_AUTH_CODE_LABEL = r"(?:one[\s-]*time|verification|confirmation|security|authentication|login|sign[\s-]*in|2fa|mfa|temporary|access)?[\s-]*(?:code|passcode|otp|pin)"
_AUTH_CODE_WEBOTP = re.compile(
    r"(?:^|\n)\s*@([A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)\s+#(\d{4,8})(?!\d)",
    re.IGNORECASE,
)
_AUTH_CODE_AFTER_LABEL = re.compile(
    rf"\b{_AUTH_CODE_LABEL}\b(?:(?![.!?]\s)[^0-9]){{0,40}}{_AUTH_CODE_VALUE}",
    re.IGNORECASE,
)
_AUTH_CODE_BEFORE_LABEL = re.compile(
    rf"{_AUTH_CODE_VALUE}(?:\s+is\s+(?:your\s+)?(?:[A-Za-z][A-Za-z0-9 ._-]{{0,50}}?\s+)?|[^A-Za-z0-9]{{0,40}}){_AUTH_CODE_LABEL}\b",
    re.IGNORECASE,
)
_AUTH_CODE_BRANDED = re.compile(
    rf"\byour\s+[A-Za-z][A-Za-z0-9 ._-]{{0,80}}?\b(?:code|passcode|otp|pin)\b(?:(?![.!?]\s)[^0-9]){{0,200}}{_AUTH_CODE_VALUE}",
    re.IGNORECASE,
)
_AUTH_CODE_ACTION = re.compile(
    rf"\b(?:use|enter|input)\s+{_AUTH_CODE_VALUE}\s+(?:to\s+(?:verify|confirm|sign[\s-]*in|log[\s-]*in|authenticate|complete)|as\s+your)\b",
    re.IGNORECASE,
)
_AUTH_CODE_CJK = re.compile(
    r"(?:验证码|驗證碼|动态密码|動態密碼|校验码|校驗碼|認証コード|確認コード|인증번호|확인코드)[^0-9]{0,20}(?<!\d)(\d{4,8})(?!\d)"
)
_GOOGLE_SMS_PREFIX_PATTERN = re.compile(r"(?<![A-Za-z0-9])G-(\d{4,8})(?!\d)")
_CALENDAR_DATE_PATTERN = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\.?\s+\d{1,2},?\s+(?:19|20)\d{2}\b",
    re.IGNORECASE,
)
_SAFE_HINT_KEYS = {
    "account_last4",
    "aliases",
    "country",
    "currency",
    "expiration_month",
    "expiration_year",
    "hint",
    "institution",
    "last4",
    "network",
    "nickname",
}
_FINANCIAL_RESERVED_FIELDS = {
    "allowed_origins",
    "allowed_purposes",
    "created_at",
    "display_name",
    "is_default",
    "kind",
    "matching_hints",
    "provenance",
    "record_id",
    "sensitivity",
    "updated_at",
    "values",
}
_CREDENTIAL_FIELDS = ("name", "origin", "username", "password", "note")
_PROFILE_METADATA_FIELDS = (
    "field_id",
    "category",
    "display_name",
    "sensitivity",
    "allowed_origins",
    "allowed_purposes",
    "provenance",
    "updated_at",
)
_RECORD_ENTRY_KEYS = {
    "profile": {
        "record_type",
        "record_id",
        "field_id",
        "category",
        "display_name",
        "value",
        "sensitivity",
        "allowed_origins",
        "allowed_purposes",
        "provenance",
    },
    "credential": {
        "record_type",
        "record_id",
        "credential_id",
        "name",
        "origin",
        "username",
        "password",
        "note",
        "sensitivity",
        "allowed_origins",
        "allowed_purposes",
        "provenance",
    },
    "financial": {
        "record_type",
        "record_id",
        "kind",
        "display_name",
        "values",
        "matching_hints",
        "is_default",
        "sensitivity",
        "allowed_origins",
        "allowed_purposes",
        "provenance",
    },
}
_SCOPE_PATCH_KEYS = {
    "profile": {
        "mode",
        "record_type",
        "record_id",
        "field_id",
        "group_id",
        "category",
        "display_name",
        "sensitivity",
        "allowed_origins",
        "allowed_purposes",
        "group_label",
        "is_primary",
        "provenance",
    },
    "credential": {
        "mode",
        "record_type",
        "record_id",
        "credential_id",
        "name",
        "origin",
        "username",
        "note",
        "allowed_origins",
        "allowed_purposes",
        "provenance",
    },
    "financial": {
        "mode",
        "record_type",
        "record_id",
        "display_name",
        "matching_hints",
        "is_default",
        "allowed_origins",
        "allowed_purposes",
        "provenance",
    },
}
_FORBIDDEN_SCOPE_PATCH_SECRET_KEYS = {
    "value",
    "values",
    "password",
    "card_number",
    "security_code",
    "additional_security_code",
    "cvv",
    "cvc",
    "account_number",
    "bank_account_number",
    "routing_number",
}
_MULTI_LABEL_PUBLIC_SUFFIXES = {
    "co.uk",
    "org.uk",
    "gov.uk",
    "ac.uk",
    "me.uk",
    "com.au",
    "net.au",
    "org.au",
    "edu.au",
    "gov.au",
    "co.nz",
    "org.nz",
    "govt.nz",
    "co.jp",
    "ne.jp",
    "or.jp",
    "ac.jp",
    "go.jp",
    "com.br",
    "org.br",
    "com.cn",
    "net.cn",
    "org.cn",
    "gov.cn",
    "com.hk",
    "com.sg",
    "com.tw",
    "co.kr",
    "co.in",
    "net.in",
    "org.in",
    "co.za",
    "com.mx",
    "com.ar",
    "com.tr",
    "co.il",
}
_SHARED_HOSTING_SUFFIXES = {
    "github.io",
    "gitlab.io",
    "bitbucket.io",
    "vercel.app",
    "netlify.app",
    "pages.dev",
    "workers.dev",
    "herokuapp.com",
    "appspot.com",
    "firebaseapp.com",
    "web.app",
    "cloudfront.net",
    "azurewebsites.net",
    "onrender.com",
    "fly.dev",
    "ngrok.io",
    "ngrok-free.app",
    "trycloudflare.com",
    "surge.sh",
}
_SSO_SUBDOMAIN_PREFIXES = {
    "auth",
    "login",
    "id",
    "sso",
    "signin",
    "accounts",
    "account",
    "identity",
    "oauth",
}


def _contains_raw_financial_number(value: str) -> bool:
    """Detect common full-number forms without treating safe last-four hints as raw."""

    return bool(
        re.search(r"\d{8,}", value)
        or re.search(
            r"(?<![A-Za-z0-9])(?:\d[\s-]*){12,}(?![A-Za-z0-9])", value
        )
    )


def _normalize_payment_card_number(value: Any) -> str:
    """Normalize a payment-card number without ever including it in errors."""

    if not isinstance(value, str):
        raise BrokerError("card number must be a string")
    result = re.sub(r"[\s-]+", "", value)
    if not re.fullmatch(r"\d{12,19}", result):
        raise BrokerError("card number must contain 12 to 19 digits")
    return result


def _normalize_card_expiration_month(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"(?:0?[1-9]|1[0-2])", value):
        raise BrokerError("expiration month must be 1 to 12")
    return str(int(value)).zfill(2)


def _normalize_card_expiration_year(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}", value):
        raise BrokerError("expiration year must contain four digits")
    return value


def _normalize_card_security_code(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{3,4}", value):
        raise BrokerError("security code must contain 3 or 4 digits")
    return value


def _normalize_additional_security_code(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{3}", value):
        raise BrokerError("additional security code must contain 3 digits")
    return value


def _normal_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise BrokerError(f"{label} must be a string")
    if _forbidden_transient_name(value):
        raise BrokerError(f"{label} cannot store transient authentication material")
    result = value.strip().lower()
    if not result or len(result) > _MAX_RECORD_ID_LENGTH:
        raise BrokerError(f"{label} must be 1 to {_MAX_RECORD_ID_LENGTH} characters")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]*", result):
        raise BrokerError(f"{label} contains unsupported characters")
    if re.search(r"\d{8,}", result):
        raise BrokerError(f"{label} must not contain a raw financial number")
    return result


def _normal_field_id(value: Any, label: str = "field id") -> str:
    if not isinstance(value, str):
        raise BrokerError(f"{label} must be a string")
    if _forbidden_transient_name(value):
        raise BrokerError(f"{label} cannot store transient authentication material")
    result = value.strip().lower()
    if not result or len(result) > _MAX_FIELD_ID_LENGTH:
        raise BrokerError(f"{label} must be 1 to {_MAX_FIELD_ID_LENGTH} characters")
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", result):
        raise BrokerError(f"{label} contains unsupported characters")
    if re.search(r"\d{8,}", result):
        raise BrokerError(f"{label} must not contain a raw financial number")
    return result


def _normal_text(
    value: Any,
    label: str,
    *,
    required: bool = True,
    strip: bool = True,
) -> str:
    if not isinstance(value, str):
        raise BrokerError(f"{label} must be a string")
    result = value.strip() if strip else value
    if required and not result:
        raise BrokerError(f"{label} is required")
    if len(result) > _MAX_TEXT_LENGTH:
        raise BrokerError(f"{label} is too long")
    return result


def _normalize_snake_case(value: str) -> str:
    return re.sub(
        r"[^A-Za-z0-9]+",
        "_",
        re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value),
    ).lower().strip("_")


def _matches_forbidden_parts(value: str, forbidden_parts: set[str]) -> bool:
    normalized = _normalize_snake_case(value)
    if not normalized:
        return False
    parts = normalized.split("_")
    for forbidden in forbidden_parts:
        tokens = forbidden.split("_")
        if len(tokens) == 1:
            if tokens[0] in parts:
                return True
            continue
        if any(
            parts[index : index + len(tokens)] == tokens
            for index in range(len(parts) - len(tokens) + 1)
        ):
            return True
    return False


def _forbidden_transient_name(value: str) -> bool:
    return _matches_forbidden_parts(value, _FORBIDDEN_TRANSIENT_PARTS)


def _forbidden_sensitive_name(value: str) -> bool:
    return _matches_forbidden_parts(value, _FORBIDDEN_SENSITIVE_PARTS)


def _validate_no_transient_name(value: str, label: str) -> None:
    if _forbidden_transient_name(value):
        raise BrokerError(f"{label} cannot store transient authentication material")


def _normalize_origin(value: Any, label: str = "origin") -> str:
    result = _normal_text(value, label)
    parsed = urlparse(result if "://" in result else "//" + result)
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise BrokerError(f"{label} is invalid") from exc
    if not hostname or any(ch.isspace() for ch in hostname):
        raise BrokerError(f"{label} is invalid")
    host = hostname.rstrip(".").lower()
    if not host:
        raise BrokerError(f"{label} is invalid")
    scheme = parsed.scheme.lower()
    if scheme and scheme not in {"http", "https"}:
        raise BrokerError(f"{label} must use http or https")
    if port is not None and (port < 1 or port > 65535):
        raise BrokerError(f"{label} has an invalid port")
    if scheme:
        default_port = 443 if scheme == "https" else 80
        suffix = "" if port is None or port == default_port else f":{port}"
        return f"{scheme}://{host}{suffix}"
    suffix = "" if port is None else f":{port}"
    return f"{host}{suffix}"


def _scope_origins(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > _MAX_SCOPE_ITEMS:
        raise BrokerError(f"{label} must be a list of at most {_MAX_SCOPE_ITEMS} origins")
    for item in value:
        if isinstance(item, str) and item.strip() == "*":
            return []
    result = []
    for item in value:
        normalized = _normalize_origin(item, label)
        if normalized not in result:
            result.append(normalized)
    return result


def _scope_purposes(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > _MAX_SCOPE_ITEMS:
        raise BrokerError(
            f"{label} must be a list of at most {_MAX_SCOPE_ITEMS} purposes"
        )
    for item in value:
        if isinstance(item, str) and item.strip() == "*":
            return []
    result = []
    for item in value:
        purpose = _normal_text(item, label)
        if purpose not in result:
            result.append(purpose)
    return result


def _safe_provenance(value: Any, *, default_source: str) -> dict[str, Any]:
    """Keep provenance metadata while dropping nested secret-bearing keys."""

    if value is None:
        return {"source": default_source}
    if not isinstance(value, dict):
        raise BrokerError("provenance must be an object")

    def clean(item: Any, depth: int = 0) -> Any:
        if depth > 4:
            raise BrokerError("provenance is too deeply nested")
        if isinstance(item, dict):
            output = {}
            for key, nested in item.items():
                if not isinstance(key, str):
                    raise BrokerError("provenance keys must be strings")
                normalized_key = _normalize_snake_case(key)
                key_parts = set(normalized_key.split("_"))
                if (
                    _forbidden_transient_name(key)
                    or _forbidden_sensitive_name(key)
                    or normalized_key in _FORBIDDEN_PROVENANCE_PARTS
                    or key_parts & _FORBIDDEN_PROVENANCE_PARTS
                    or normalized_key in {"body", "code"}
                ):
                    continue
                output[key] = clean(nested, depth + 1)
            return output
        if isinstance(item, list):
            if len(item) > _MAX_SCOPE_ITEMS:
                raise BrokerError("provenance list is too long")
            return [clean(nested, depth + 1) for nested in item]
        if item is None or isinstance(item, (bool, int, float, str)):
            if isinstance(item, str) and len(item) > _MAX_TEXT_LENGTH:
                raise BrokerError("provenance text is too long")
            if isinstance(item, str) and _contains_raw_financial_number(item):
                raise BrokerError("provenance contains a raw financial number")
            return item
        raise BrokerError("provenance contains unsupported data")

    result = clean(value)
    if not isinstance(result, dict):
        raise BrokerError("provenance must be an object")
    result.setdefault("source", default_source)
    return result


def _profile_scalar(value: Any, label: str) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > _MAX_TEXT_LENGTH:
            raise BrokerError(f"{label} is too long")
        return value
    if isinstance(value, list):
        if len(value) > _MAX_SCOPE_ITEMS:
            raise BrokerError(f"{label} list is too long")
        result = []
        for item in value:
            if item is None or isinstance(item, (bool, int, float, str)):
                if isinstance(item, str) and len(item) > _MAX_TEXT_LENGTH:
                    raise BrokerError(f"{label} is too long")
                result.append(item)
            else:
                raise BrokerError(f"{label} must be a scalar or short list of scalars")
        return result
    raise BrokerError(f"{label} must be a scalar or short list of scalars")


def _record_type(value: Any) -> str:
    if not isinstance(value, str):
        raise BrokerError("record_type must be a string")
    result = value.strip().lower()
    if result not in _RECORD_TYPES:
        raise BrokerError("record_type must be profile, credential, or financial")
    return result


def _entry_record_id(entry: dict[str, Any], record_type: str) -> str:
    candidates = [entry.get("record_id")]
    if record_type == "profile":
        candidates.append(entry.get("field_id"))
    elif record_type == "credential":
        candidates.append(entry.get("credential_id"))
    provided = [candidate for candidate in candidates if candidate is not None]
    if len(provided) > 1:
        normalized = {_normal_identifier(candidate, "record_id") for candidate in provided}
        if len(normalized) != 1:
            raise BrokerError("record id aliases must match")
    for candidate in provided:
        record_id = _normal_identifier(candidate, "record_id")
        _validate_no_transient_name(record_id, "record_id")
        return record_id
    raise BrokerError("record_id is required")


def _record_scope_allowed(
    allowed_origins: list[str],
    allowed_purposes: list[str],
    target_origin: str,
    purpose: str,
    *,
    trusted_local: bool = False,
) -> None:
    if trusted_local:
        return
    if allowed_origins and not any(
        _origin_matches(allowed, target_origin) for allowed in allowed_origins
    ):
        raise AuthorizationError("target origin is not allowed for record")
    if allowed_purposes and purpose not in allowed_purposes:
        raise AuthorizationError("purpose is not allowed for record")


def _credential_scope_allowed(
    item: dict[str, Any],
    target_origin: str,
    purpose: str,
    *,
    trusted_local: bool = False,
) -> None:
    if trusted_local:
        return
    allowed_origins: list[str] = list(item.get("allowed_origins") or [])
    allowed_purposes: list[str] = list(item.get("allowed_purposes") or [])
    if allowed_purposes and purpose not in allowed_purposes:
        raise AuthorizationError("purpose is not allowed for record")
    if not allowed_origins:
        return
    if any(_origin_matches(allowed, target_origin) for allowed in allowed_origins):
        return
    raw_origin = item.get("origin")
    if isinstance(raw_origin, str) and raw_origin.strip():
        try:
            default_origin = _normalize_origin(raw_origin, "credential origin")
            cred_host = _origin_hostname(default_origin)
            target_host = _origin_hostname(target_origin)
        except BrokerError:
            default_origin = ""
            cred_host = ""
            target_host = ""
        if allowed_origins == [default_origin] and _registrable_domains_match(
            cred_host, target_host
        ):
            return
    raise AuthorizationError("target origin is not allowed for record")


def _bounded_limit(value) -> int:
    try:
        return max(1, min(int(value), 100))
    except (TypeError, ValueError):
        raise BrokerError("limit must be an integer")


def _origin_matches(left: str | None, right: str | None) -> bool:
    if left is None:
        return right is None
    if right is None:
        return False
    if left.strip() == "*":
        return bool(right.strip())

    def parts(value):
        parsed = urlparse(value if "://" in value else "//" + value)
        try:
            port = parsed.port
        except ValueError:
            port = -1
        host = (parsed.hostname or "").rstrip(".").lower()
        scheme = parsed.scheme.lower()
        if (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
            port = None
        return (scheme, host, port)

    a, b = parts(left), parts(right)
    return a == b or (not a[0] or not b[0]) and a[1:] == b[1:]


def _origin_hostname(value: str) -> str:
    """Return the hostname from a normalized URL or scheme-less origin."""

    normalized = _normalize_origin(value)
    parsed = urlparse(normalized if "://" in normalized else "//" + normalized)
    return (parsed.hostname or "").lower()


def _registrable_domain(hostname: str) -> str | None:
    """Return the eTLD+1 registrable domain (or tenant domain on shared hosting)."""

    host = hostname.strip().rstrip(".").lower()
    if not host or "." not in host:
        return None
    try:
        ipaddress.ip_address(host.strip("[]"))
        return None
    except ValueError:
        pass
    labels = [label for label in host.split(".") if label]
    if len(labels) < 2 or labels[-1] == "localhost":
        return None
    two_label_suffix = ".".join(labels[-2:])
    if two_label_suffix in _SHARED_HOSTING_SUFFIXES:
        if len(labels) < 3:
            return None
        return ".".join(labels[-3:])
    if two_label_suffix in _MULTI_LABEL_PUBLIC_SUFFIXES:
        if len(labels) < 3:
            return None
        return ".".join(labels[-3:])
    return two_label_suffix


def _registrable_domains_match(left_host: str, right_host: str) -> bool:
    left_reg = _registrable_domain(left_host)
    if left_reg is None:
        return False
    return left_reg == _registrable_domain(right_host)


def _sso_subdomain_rank(hostname: str, reg_domain: str) -> int:
    """Rank canonical SSO/auth hosts (0), apex/www (1), then other sibling subdomains (2)."""

    host = hostname.strip().rstrip(".").lower()
    if host == reg_domain:
        return 1
    prefix = host[: -(len(reg_domain) + 1)] if host.endswith("." + reg_domain) else ""
    sub_labels = [part for part in prefix.split(".") if part]
    if sub_labels and (
        sub_labels[0] in _SSO_SUBDOMAIN_PREFIXES
        or sub_labels[-1] in _SSO_SUBDOMAIN_PREFIXES
    ):
        return 0
    if sub_labels == ["www"]:
        return 1
    return 2


def _normalize_otp_origin_policy(value: Any) -> str:
    if value is None:
        return "target-origin"
    if not isinstance(value, str):
        raise BrokerError("origin_policy must be target-origin or mailbox-read")
    policy = value.strip().lower()
    if policy not in _OTP_ORIGIN_POLICIES:
        raise BrokerError("origin_policy must be target-origin or mailbox-read")
    return policy


def _normalize_messages_otp_origin_policy(value: Any) -> str:
    if value is None:
        return "target-origin"
    if not isinstance(value, str):
        raise BrokerError("origin_policy must be target-origin or messages-read")
    policy = value.strip().lower()
    if policy not in _MESSAGES_OTP_ORIGIN_POLICIES:
        raise BrokerError("origin_policy must be target-origin or messages-read")
    return policy


def _otp_sender_matches(sender: str, target_origin: str) -> bool:
    # Match the parsed address, never display names, body links, or suffix guesses.
    addresses = getaddresses([sender])
    if len(addresses) != 1:
        return False
    address = addresses[0][1]
    if address.count("@") != 1:
        return False
    domain = address.rsplit("@", 1)[1].lower()
    host = _origin_hostname(target_origin)
    return domain in ({host} | _OTP_SENDER_DOMAINS.get(host, set()))


def _extract_webotp_bindings(body: str) -> list[tuple[str, str]]:
    """Extract Apple Domain-Bound SMS / WebOTP (@domain #code) pairs."""

    return [
        (match.group(1).strip().rstrip(".").lower(), match.group(2))
        for match in _AUTH_CODE_WEBOTP.finditer(body or "")
    ]


def _messages_otp_matches_target(sender: str, body: str, target_origin: str) -> bool:
    """Match an incoming SMS/iMessage to target_origin using WebOTP, domain, or service name."""

    host = _origin_hostname(target_origin)
    reg_domain = _registrable_domain(host)
    allowed_domains = {host}
    if reg_domain:
        allowed_domains.add(reg_domain)
    allowed_domains |= _OTP_SENDER_DOMAINS.get(host, set())
    if reg_domain:
        allowed_domains |= _OTP_SENDER_DOMAINS.get(reg_domain, set())

    bindings = _extract_webotp_bindings(body)
    if bindings:
        for bound_domain, _ in bindings:
            if bound_domain in allowed_domains:
                return True
            if reg_domain and _registrable_domain(bound_domain) == reg_domain:
                return True
        return False

    combined = f"{sender or ''}\n{body or ''}"
    for domain in allowed_domains:
        if re.search(
            rf"(?<![A-Za-z0-9-]){re.escape(domain)}(?![A-Za-z0-9-])",
            combined,
            re.IGNORECASE,
        ):
            return True

    keywords: set[str] = set()
    keywords |= _SMS_OTP_BRAND_ALIASES.get(host, set())
    if reg_domain:
        keywords |= _SMS_OTP_BRAND_ALIASES.get(reg_domain, set())
        primary_label = reg_domain.split(".", 1)[0]
        if len(primary_label) >= 3 and primary_label not in _SMS_OTP_BRAND_STOPWORDS:
            keywords.add(primary_label)
    if reg_domain == "google.com" and _GOOGLE_SMS_PREFIX_PATTERN.search(body or ""):
        return True
    for keyword in keywords:
        if re.search(
            rf"(?<![A-Za-z0-9]){re.escape(keyword)}(?![A-Za-z0-9])",
            combined,
            re.IGNORECASE,
        ):
            return True
    return False


def _metadata_page(items, limit, offset):
    if type(offset) is not int or offset < 0:
        raise BrokerError("offset must be a nonnegative integer")
    return list(islice(items, offset, offset + _bounded_limit(limit)))


def _normalize_code_digits(raw: str) -> str:
    return re.sub(r"[-\s]+", "", raw)


def _extract_auth_codes(body: str) -> list[str]:
    """Prefer WebOTP or explicitly labeled authentication codes."""

    bindings = _extract_webotp_bindings(body)
    if bindings:
        return list(dict.fromkeys(code for _, code in bindings))
    labeled = [
        _normalize_code_digits(match.group(1))
        for pattern in (
            _AUTH_CODE_AFTER_LABEL,
            _AUTH_CODE_BEFORE_LABEL,
            _AUTH_CODE_BRANDED,
            _AUTH_CODE_ACTION,
            _AUTH_CODE_CJK,
        )
        for match in pattern.finditer(body)
    ]
    if labeled:
        return list(dict.fromkeys(labeled))
    scrubbed = _CALENDAR_DATE_PATTERN.sub(" ", body)
    return list(dict.fromkeys(re.findall(r"(?<!\d-)\b\d{4,8}\b(?!-\d)", scrubbed)))


def _message_received_at(value: Any) -> datetime | None:
    """Parse provider time without turning malformed input into a fresh time."""

    if not isinstance(value, str):
        return None
    try:
        received_at = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=UTC)
    return received_at


class Broker:
    def __init__(self, store: Store | None = None):
        self.store = store or Store()
        self.store.purge_legacy_otp_markers()
        self.email: dict[str, list[EmailMessage]] = {}
        self.texts: list[TextMessage] = []
        self.email_adapters: dict[str, object] = {}
        self.unavailable_email_sources: set[str] = set()
        self.removed_email_sources: set[str] = set()
        self.message_adapters: dict[str, object] = {}
        self.sources: dict[str, dict] = {}
        for row in self.store.db.execute("select key from kv where namespace='source'"):
            item = self.store.get("source", row["key"])
            if item:
                self.sources[row["key"]] = item

    def add_source(self, source_id: str, kind: str, label: str) -> None:
        self.removed_email_sources.discard(source_id)
        self.sources[source_id] = {"source_id": source_id, "kind": kind, "label": label}
        self.store.put("source", source_id, self.sources[source_id])

    def register_email_adapter(
        self, account_id: str, adapter: object, label: str | None = None
    ) -> None:
        self.email_adapters[account_id] = adapter
        self.unavailable_email_sources.discard(account_id)
        self.removed_email_sources.discard(account_id)
        self.add_source(account_id, "email", label or account_id)

    def mark_email_unavailable(self, account_id: str) -> None:
        """Keep a saved source visible while refusing unbacked reads."""

        self.unavailable_email_sources.add(account_id)

    def register_message_adapter(
        self, source_id: str, adapter: object, label: str | None = None
    ) -> None:
        self.message_adapters[source_id] = adapter
        self.add_source(source_id, "messages", label or source_id)

    def remove_source(self, source_id: str) -> None:
        grant_writes: list[tuple[str, str, dict[str, Any]]] = []
        for row in self.store.db.execute(
            "select key,value from kv where namespace='grant'"
        ).fetchall():
            grant = self.store._open(row["value"])
            if grant["resource_scope"] == source_id:
                grant["revoked"] = True
                grant_writes.append(("grant", row["key"], grant))
        self.store.apply_changes(
            grant_writes,
            [("source", source_id), ("connection", source_id)],
        )
        # Keep a tombstone even when the persisted source was unavailable and
        # no adapter was reconstructed. This prevents stale in-memory fallback
        # messages from becoming readable again after removal.
        self.removed_email_sources.add(source_id)
        self.sources.pop(source_id, None)
        self.email_adapters.pop(source_id, None)
        self.unavailable_email_sources.discard(source_id)
        self.message_adapters.pop(source_id, None)

    def put_profile(
        self,
        field_id: str,
        category: str,
        display_name: str,
        value,
        sensitivity: str = "normal",
        allowed_origins: list[str] | None = None,
        allowed_purposes: list[str] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> None:
        record = self._prepare_profile_record(
            {
                "record_type": "profile",
                "record_id": field_id,
                "category": category,
                "display_name": display_name,
                "value": value,
                "sensitivity": sensitivity,
                "allowed_origins": allowed_origins,
                "allowed_purposes": allowed_purposes,
                "provenance": provenance,
            }
        )
        self.store.put("profile", record["field_id"], record)

    def put_credential(
        self,
        credential_id: str,
        name: str,
        origin: str,
        username: str,
        password: str,
        note: str = "",
        allowed_origins: list[str] | None = None,
        allowed_purposes: list[str] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> None:
        record = self._prepare_credential_record(
            {
                "record_type": "credential",
                "record_id": credential_id,
                "name": name,
                "origin": origin,
                "username": username,
                "password": password,
                "note": note,
                "allowed_origins": allowed_origins,
                "allowed_purposes": allowed_purposes,
                "provenance": provenance,
            }
        )
        existing = self.store.get("credential", record["credential_id"])
        if existing:
            existing = self._credential_defaults(record["credential_id"], existing)
            record["created_at"] = existing.get("created_at", record["created_at"])
        self.store.put("credential", record["credential_id"], record)

    def list_credentials(
        self,
        *,
        query: str | None = None,
        target_origin: str | None = None,
        username_hint: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """List bounded credential metadata without passwords."""

        if target_origin is not None:
            return self.match_credentials(
                target_origin,
                username_hint=username_hint,
                limit=limit,
                offset=offset,
            )
        normalized_query = (
            _normal_text(query, "query").casefold() if query is not None else None
        )
        hint = (
            _normal_text(username_hint, "username_hint", required=False).casefold()
            if username_hint is not None
            else ""
        )
        result = []
        for row in self.store.db.execute(
            "select key from kv where namespace='credential' order by key"
        ):
            item = self.store.get("credential", row["key"])
            if item:
                item = self._credential_defaults(row["key"], item)
                metadata = self._credential_metadata(item)
                if normalized_query is not None:
                    searchable = " ".join(
                        [
                            *(
                                str(metadata.get(key, ""))
                                for key in ("credential_id", "name", "origin", "username")
                            ),
                            str((metadata.get("provenance") or {}).get("hint", "")),
                        ]
                    ).casefold()
                    if normalized_query not in searchable:
                        continue
                if hint and hint not in str(metadata.get("username") or "").casefold():
                    continue
                result.append(metadata)
        return _metadata_page(iter(result), limit, offset)

    def match_credentials(
        self,
        target_origin: str,
        username_hint: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        """Find credential metadata for one site without exposing secrets."""

        target = _normalize_origin(target_origin, "target_origin")
        target_host = _origin_hostname(target)
        target_reg_domain = _registrable_domain(target_host)
        hint = (
            _normal_text(username_hint, "username_hint", required=False).casefold()
            if username_hint is not None
            else ""
        )
        ranked: list[tuple[tuple[int, int, str, str], dict[str, Any]]] = []
        for row in self.store.db.execute(
            "select key from kv where namespace='credential'"
        ):
            item = self.store.get("credential", row["key"])
            if not item:
                continue
            item = self._credential_defaults(row["key"], item)
            try:
                normalized_origin = _normalize_origin(item["origin"], "credential origin")
                host = _origin_hostname(normalized_origin)
            except BrokerError:
                continue
            allowed_origins = item.get("allowed_origins") or []
            if host == target_host or any(
                _origin_matches(allowed, target) for allowed in allowed_origins
            ):
                match_kind = "exact-host" if host == target_host else "registrable-domain"
                tier = 0 if host == target_host else 1
                subtier = 0 if normalized_origin == target else 1
            elif (
                target_reg_domain is not None
                and _registrable_domain(host) == target_reg_domain
            ):
                if allowed_origins and not any(
                    _registrable_domain(_origin_hostname(allowed)) == target_reg_domain
                    for allowed in allowed_origins
                ):
                    continue
                match_kind = "registrable-domain"
                tier = 1
                subtier = _sso_subdomain_rank(host, target_reg_domain)
            else:
                continue
            username = item.get("username", "")
            if hint and hint not in username.casefold():
                continue
            metadata = self._credential_metadata(item)
            metadata["match"] = match_kind
            sort_key = (
                tier,
                subtier,
                username.casefold(),
                metadata.get("credential_id", ""),
            )
            ranked.append((sort_key, metadata))
        ranked.sort(key=lambda pair: pair[0])
        matches = [metadata for _, metadata in ranked]
        return _metadata_page(iter(matches), limit, offset)

    @staticmethod
    def _profile_defaults(key: str, item: dict[str, Any]) -> dict[str, Any]:
        result = dict(item)
        result["field_id"] = result.get("field_id", key)
        result.setdefault("category", "custom")
        result.setdefault("display_name", result["field_id"])
        result.setdefault("value", None)
        result.setdefault("sensitivity", "normal")
        result["allowed_origins"] = _scope_origins(
            result.get("allowed_origins"), "allowed_origins"
        )
        result["allowed_purposes"] = _scope_purposes(
            result.get("allowed_purposes"), "allowed_purposes"
        )
        result["provenance"] = _safe_provenance(
            result.get("provenance"), default_source="legacy-profile"
        )
        result.setdefault("updated_at", now_iso())
        return result

    def _prepare_profile_record(self, entry: dict[str, Any]) -> dict[str, Any]:
        field_id = _entry_record_id(entry, "profile")
        _validate_no_transient_name(field_id, "field_id")
        if _forbidden_sensitive_name(field_id):
            raise BrokerError(
                "profile field id cannot store credential or financial record data"
            )
        category = _normal_text(entry.get("category"), "category")
        normalized_category = re.sub(r"[^a-z0-9]+", "_", category.lower()).strip(
            "_"
        )
        if (
            _forbidden_transient_name(category)
            or any(
                part in normalized_category.split("_")
                for part in _FORBIDDEN_PROFILE_CATEGORIES
            )
        ):
            raise BrokerError("profile category cannot hold sensitive record data")
        display_name = _normal_text(entry.get("display_name"), "display_name")
        if _contains_raw_financial_number(display_name):
            raise BrokerError("display_name cannot contain a raw financial number")
        sensitivity = _normal_text(
            entry.get("sensitivity", "normal"), "sensitivity"
        )
        origins = _scope_origins(entry.get("allowed_origins"), "allowed_origins")
        purposes = _scope_purposes(entry.get("allowed_purposes"), "allowed_purposes")
        if "value" not in entry:
            raise BrokerError("profile value is required")
        return {
            "field_id": field_id,
            "category": category,
            "display_name": display_name,
            "value": _profile_scalar(entry.get("value"), "profile value"),
            "sensitivity": sensitivity,
            "allowed_origins": origins,
            "allowed_purposes": purposes,
            "provenance": _safe_provenance(
                entry.get("provenance"), default_source="local-profile"
            ),
            "updated_at": now_iso(),
        }

    @staticmethod
    def _credential_metadata(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "credential_id": item["credential_id"],
            "name": item["name"],
            "origin": item["origin"],
            "username": item["username"],
            "field_ids": list(_CREDENTIAL_FIELDS),
            "allowed_origins": list(item["allowed_origins"]),
            "allowed_purposes": list(item["allowed_purposes"]),
            "sensitivity": "credential",
            "provenance": _safe_provenance(
                item.get("provenance"), default_source="legacy-credential"
            ),
            "created_at": item["created_at"],
            "updated_at": item["updated_at"],
        }

    def _prepare_credential_record(self, entry: dict[str, Any]) -> dict[str, Any]:
        credential_id = _entry_record_id(entry, "credential")
        name = _normal_text(entry.get("name"), "credential name")
        if _contains_raw_financial_number(name):
            raise BrokerError("credential name cannot contain a raw financial number")
        stored_origin = _normal_text(entry.get("origin"), "credential origin")
        normalized_origin = _normalize_origin(stored_origin, "credential origin")
        username = _normal_text(
            entry.get("username", ""),
            "credential username",
            required=False,
            strip=False,
        )
        password = _normal_text(
            entry.get("password"),
            "credential password",
            strip=False,
        )
        note = _normal_text(
            entry.get("note", ""), "credential note", required=False, strip=False
        )
        sensitivity = _normal_text(
            entry.get("sensitivity", "credential"), "sensitivity"
        ).lower()
        if sensitivity != "credential":
            raise BrokerError("credential sensitivity must be credential")
        origins = (
            _scope_origins(entry.get("allowed_origins"), "allowed_origins")
            if "allowed_origins" in entry and entry["allowed_origins"] is not None
            else [normalized_origin]
        )
        purposes = _scope_purposes(entry.get("allowed_purposes"), "allowed_purposes")
        return {
            "credential_id": credential_id,
            "name": name,
            "origin": stored_origin,
            "username": username,
            "password": password,
            "note": note,
            "allowed_origins": origins,
            "allowed_purposes": purposes,
            "sensitivity": "credential",
            "provenance": _safe_provenance(
                entry.get("provenance"), default_source="local-credential-vault"
            ),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }

    def _credential_defaults(self, key: str, item: dict[str, Any]) -> dict[str, Any]:
        """Apply read-time defaults to credentials written by older versions."""

        result = dict(item)
        credential_id = result.get("credential_id", key)
        result["credential_id"] = credential_id
        result.setdefault("name", credential_id)
        result.setdefault("origin", "")
        result.setdefault("username", "")
        result.setdefault("password", "")
        result.setdefault("note", "")
        if "allowed_origins" not in result or result["allowed_origins"] is None:
            result["allowed_origins"] = (
                [_normalize_origin(result["origin"], "credential origin")]
                if result.get("origin")
                else []
            )
        else:
            result["allowed_origins"] = _scope_origins(
                result["allowed_origins"], "allowed_origins"
            )
        result.setdefault("allowed_purposes", [])
        result["allowed_purposes"] = _scope_purposes(
            result["allowed_purposes"], "allowed_purposes"
        )
        result.setdefault("sensitivity", "credential")
        result["provenance"] = _safe_provenance(
            result.get("provenance"), default_source="legacy-credential"
        )
        result.setdefault("created_at", result.get("updated_at", now_iso()))
        result.setdefault("updated_at", result["created_at"])
        return result

    @staticmethod
    def _requested_fields(
        field_ids: Any,
        label: str,
        allowed: tuple[str, ...] | None = None,
        *,
        identifiers: bool = False,
    ) -> list[str]:
        if not isinstance(field_ids, list) or not field_ids:
            raise BrokerError(f"{label} must contain 1 to 100 fields")
        if len(field_ids) > _MAX_SCOPE_ITEMS:
            raise BrokerError(f"{label} must contain 1 to 100 fields")
        result = []
        for raw in field_ids:
            normalizer = _normal_identifier if identifiers else _normal_field_id
            field_id = normalizer(raw, label.rstrip("s"))
            if field_id in result:
                raise BrokerError(f"{label} cannot contain duplicate fields")
            if allowed is not None and field_id not in allowed:
                raise BrokerError(f"unknown {label.rstrip('s')}: {field_id}")
            result.append(field_id)
        return result

    @staticmethod
    def _matching_hints(
        values: dict[str, str], supplied: Any
    ) -> dict[str, str]:
        if supplied is not None and not isinstance(supplied, dict):
            raise BrokerError("matching_hints must be an object")
        result: dict[str, str] = {}
        for raw_key, raw_value in (supplied or {}).items():
            key = _normal_field_id(raw_key, "matching hint")
            if key not in _SAFE_HINT_KEYS:
                raise BrokerError("matching_hints contains an unsupported key")
            if key in {"last4", "account_last4"}:
                if not isinstance(raw_value, str) or not re.fullmatch(
                    r"\d{4}", raw_value
                ):
                    raise BrokerError("matching hint last4 must contain four digits")
                result[key] = raw_value
            elif key == "expiration_month":
                if not isinstance(raw_value, str) or not re.fullmatch(
                    r"(?:0?[1-9]|1[0-2])", raw_value
                ):
                    raise BrokerError(
                        "matching hint expiration_month must be 1 to 12"
                    )
                result[key] = str(int(raw_value)).zfill(2)
            elif key == "expiration_year":
                if not isinstance(raw_value, str) or not re.fullmatch(
                    r"\d{4}", raw_value
                ):
                    raise BrokerError(
                        "matching hint expiration_year must contain four digits"
                    )
                result[key] = raw_value
            else:
                hint_value = _normal_text(
                    raw_value, "matching hint", required=False
                )
                if re.search(r"\d{8,}", hint_value):
                    raise BrokerError(
                        "matching hint cannot contain a raw financial number"
                    )
                result[key] = hint_value

        candidates = (
            ("last4", "card_number"),
            ("account_last4", "account_number"),
            ("account_last4", "bank_account_number"),
        )
        for hint_key, value_key in candidates:
            raw_value = values.get(value_key)
            if (
                hint_key not in result
                and isinstance(raw_value, str)
                and len(re.sub(r"\D", "", raw_value)) >= 4
            ):
                digits = re.sub(r"\D", "", raw_value)
                result[hint_key] = digits[-4:]
                break
        if "expiration_month" not in result:
            month = values.get("expiration_month")
            if isinstance(month, str) and re.fullmatch(r"(?:0?[1-9]|1[0-2])", month):
                result["expiration_month"] = str(int(month)).zfill(2)
        if "expiration_year" not in result:
            year = values.get("expiration_year")
            if isinstance(year, str) and re.fullmatch(r"\d{4}", year):
                result["expiration_year"] = year
        return result

    def _financial_defaults(
        self, key: str, item: dict[str, Any]
    ) -> dict[str, Any]:
        result = dict(item)
        result["record_id"] = _normal_identifier(
            result.get("record_id", key), "record_id"
        )
        _validate_no_transient_name(result["record_id"], "record_id")
        result["kind"] = _normal_text(
            result.get("kind", "custom_financial"), "financial kind"
        ).lower()
        if result["kind"] not in _FINANCIAL_KINDS:
            raise BrokerError("financial kind is unsupported")
        result.setdefault("display_name", result["record_id"])
        result.setdefault("values", {})
        if not isinstance(result["values"], dict):
            raise BrokerError("financial values must be an object")
        normalized_values: dict[str, str] = {}
        for raw_key, raw_value in result["values"].items():
            field_id = _normal_field_id(raw_key, "financial field id")
            _validate_no_transient_name(field_id, "financial field id")
            if field_id in _FINANCIAL_RESERVED_FIELDS:
                raise BrokerError("financial field id is reserved")
            if not isinstance(raw_value, str):
                raise BrokerError("financial values must contain strings")
            if len(raw_value) > _MAX_TEXT_LENGTH:
                raise BrokerError("financial value is too long")
            normalized_values[field_id] = raw_value
        result["values"] = normalized_values
        result["matching_hints"] = self._matching_hints(
            result["values"], result.get("matching_hints")
        )
        result["allowed_origins"] = _scope_origins(
            result.get("allowed_origins"), "allowed_origins"
        )
        result["allowed_purposes"] = _scope_purposes(
            result.get("allowed_purposes"), "allowed_purposes"
        )
        result["is_default"] = bool(result.get("is_default", False))
        result["sensitivity"] = "financial"
        result["provenance"] = _safe_provenance(
            result.get("provenance"), default_source="local-financial-vault"
        )
        result.setdefault("created_at", result.get("updated_at", now_iso()))
        result.setdefault("updated_at", result["created_at"])
        return result

    def _prepare_financial_record(self, entry: dict[str, Any]) -> dict[str, Any]:
        record_id = _entry_record_id(entry, "financial")
        kind = _normal_text(entry.get("kind"), "financial kind").lower()
        if kind not in _FINANCIAL_KINDS:
            raise BrokerError("financial kind is unsupported")
        display_name = _normal_text(entry.get("display_name"), "display_name")
        if _contains_raw_financial_number(display_name):
            raise BrokerError("display_name cannot contain a raw financial number")
        raw_values = entry.get("values")
        if not isinstance(raw_values, dict) or not raw_values:
            raise BrokerError("financial values must be a non-empty object")
        if len(raw_values) > _MAX_SCOPE_ITEMS:
            raise BrokerError("financial values contain too many fields")
        values: dict[str, str] = {}
        for raw_key, raw_value in raw_values.items():
            field_id = _normal_field_id(raw_key, "financial field id")
            if field_id in values:
                raise BrokerError("financial values contain duplicate fields")
            _validate_no_transient_name(field_id, "financial field id")
            if field_id in _FINANCIAL_RESERVED_FIELDS:
                raise BrokerError("financial field id is reserved")
            if not isinstance(raw_value, str):
                raise BrokerError("financial values must contain strings")
            if len(raw_value) > _MAX_TEXT_LENGTH:
                raise BrokerError("financial value is too long")
            values[field_id] = raw_value
        is_default = entry.get("is_default", False)
        if not isinstance(is_default, bool):
            raise BrokerError("is_default must be a boolean")
        sensitivity = _normal_text(
            entry.get("sensitivity", "financial"), "sensitivity"
        ).lower()
        if sensitivity != "financial":
            raise BrokerError("financial sensitivity must be financial")
        return {
            "record_id": record_id,
            "kind": kind,
            "display_name": display_name,
            "values": values,
            "matching_hints": self._matching_hints(
                values, entry.get("matching_hints")
            ),
            "is_default": is_default,
            "allowed_origins": _scope_origins(
                entry.get("allowed_origins"), "allowed_origins"
            ),
            "allowed_purposes": _scope_purposes(
                entry.get("allowed_purposes"), "allowed_purposes"
            ),
            "sensitivity": "financial",
            "provenance": _safe_provenance(
                entry.get("provenance"), default_source="local-financial-vault"
            ),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }

    def _clear_other_default_financial_writes(
        self,
        kind: str,
        active_record_id: str,
        *,
        skip_ids: set[str] | None = None,
    ) -> list[tuple[str, str, dict[str, Any]]]:
        writes: list[tuple[str, str, dict[str, Any]]] = []
        excluded = (skip_ids or set()) | {active_record_id}
        for row in self.store.db.execute(
            "select key from kv where namespace='financial' order by key"
        ):
            rid = row["key"]
            if rid in excluded:
                continue
            other = self.store.get("financial", rid)
            if not other:
                continue
            other = self._financial_defaults(rid, other)
            if other["kind"] == kind and other.get("is_default") is True:
                other["is_default"] = False
                other["updated_at"] = now_iso()
                writes.append(("financial", rid, other))
        return writes

    @staticmethod
    def _same_record(left: dict[str, Any], right: dict[str, Any]) -> bool:
        keys = set(right) - {"created_at", "updated_at"}
        return all(left.get(key) == right.get(key) for key in keys)

    def put_financial_record(
        self,
        record_id: str,
        kind: str,
        display_name: str,
        values: dict[str, str],
        matching_hints: dict[str, str] | None = None,
        allowed_origins: list[str] | None = None,
        allowed_purposes: list[str] | None = None,
        provenance: dict[str, Any] | None = None,
        *,
        is_default: bool = False,
    ) -> None:
        record = self._prepare_financial_record(
            {
                "record_type": "financial",
                "record_id": record_id,
                "kind": kind,
                "display_name": display_name,
                "values": values,
                "matching_hints": matching_hints,
                "is_default": is_default,
                "allowed_origins": allowed_origins,
                "allowed_purposes": allowed_purposes,
                "provenance": provenance,
            }
        )
        existing = self.store.get("financial", record["record_id"])
        if existing:
            existing = self._financial_defaults(record["record_id"], existing)
            record["created_at"] = existing.get("created_at", record["created_at"])
        writes: list[tuple[str, str, dict[str, Any]]] = [
            ("financial", record["record_id"], record)
        ]
        if record["is_default"]:
            writes.extend(
                self._clear_other_default_financial_writes(
                    record["kind"], record["record_id"]
                )
            )
        self.store.apply_changes(writes, [])

    def put_payment_card(
        self,
        record_id: str,
        display_name: str,
        card_number: str,
        expiration_month: str,
        expiration_year: str,
        security_code: str | None = None,
        additional_security_code: str | None = None,
        cardholder_name: str | None = None,
        billing_postal_code: str | None = None,
        network: str | None = None,
        allowed_origins: list[str] | None = None,
        allowed_purposes: list[str] | None = None,
        provenance: dict[str, Any] | None = None,
        *,
        hint: str | None = None,
        institution: str | None = None,
        is_default: bool = False,
    ) -> None:
        """Store a typed payment card in the encrypted financial namespace.

        This helper gives local administration a narrow card-shaped API while
        retaining the generic financial record format used by MCP callers.
        Sensitive values are accepted only in memory and are sealed by the
        store immediately; metadata listings expose only safe matching hints.
        """

        normalized_number = _normalize_payment_card_number(card_number)
        values: dict[str, str] = {
            "card_number": normalized_number,
            "expiration_month": _normalize_card_expiration_month(expiration_month),
            "expiration_year": _normalize_card_expiration_year(expiration_year),
        }
        if security_code is not None and security_code != "":
            values["security_code"] = _normalize_card_security_code(security_code)
        if additional_security_code is not None and additional_security_code != "":
            values["additional_security_code"] = _normalize_additional_security_code(
                additional_security_code
            )
        if cardholder_name is not None and cardholder_name != "":
            values["cardholder_name"] = _normal_text(
                cardholder_name, "cardholder name"
            )
        if billing_postal_code is not None and billing_postal_code != "":
            values["billing_postal_code"] = _normal_text(
                billing_postal_code, "billing postal code"
            )

        hints_dict: dict[str, str] = {}
        if network is not None and network != "":
            hints_dict["network"] = _normal_text(network, "card network")
        if hint is not None and hint != "":
            hints_dict["hint"] = _normal_text(hint, "card hint")
        if institution is not None and institution != "":
            hints_dict["institution"] = _normal_text(institution, "card institution")
        self.put_financial_record(
            record_id=record_id,
            kind="payment_card",
            display_name=display_name,
            values=values,
            matching_hints=hints_dict or None,
            allowed_origins=allowed_origins,
            allowed_purposes=allowed_purposes,
            provenance=provenance,
            is_default=is_default,
        )

    def list_financial_records(
        self,
        *,
        kind: str | None = None,
        query: str | None = None,
        is_default: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List bounded financial metadata without stored values."""

        return _metadata_page(
            self._iter_financial_records(
                kind=kind, query=query, is_default=is_default
            ),
            limit,
            offset,
        )

    def _iter_financial_records(
        self, *, kind=None, query=None, card_suffix=None, is_default=None
    ):
        if is_default is not None and not isinstance(is_default, bool):
            raise BrokerError("is_default must be a boolean")
        normalized_kind = (
            _normal_text(kind, "financial kind").casefold()
            if kind is not None
            else None
        )
        normalized_query = (
            _normal_text(query, "query").casefold() if query is not None else None
        )
        items_out: list[dict[str, Any]] = []
        for row in self.store.db.execute(
            "select key from kv where namespace='financial' order by key"
        ):
            item = self.store.get("financial", row["key"])
            if not item:
                continue
            item = self._financial_defaults(row["key"], item)
            if is_default is not None and bool(item.get("is_default", False)) is not is_default:
                continue
            if card_suffix is not None:
                number = re.sub(r"[ -]", "", item["values"].get("card_number", ""))
                if (item["kind"] != "payment_card"
                        or not re.fullmatch(r"[0-9]{12,19}", number)
                        or not number.endswith(card_suffix)):
                    continue
            metadata = {
                "record_id": item["record_id"],
                "kind": item["kind"],
                "display_name": item["display_name"],
                "field_ids": sorted(item["values"]),
                "matching_hints": dict(item["matching_hints"]),
                "is_default": bool(item.get("is_default", False)),
                "allowed_origins": list(item["allowed_origins"]),
                "allowed_purposes": list(item["allowed_purposes"]),
                "sensitivity": "financial",
                "provenance": _safe_provenance(
                    item.get("provenance"),
                    default_source="local-financial-vault",
                ),
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
            }
            if (
                normalized_kind is not None
                and metadata["kind"].casefold() != normalized_kind
            ):
                continue
            if normalized_query is not None:
                hints_map = metadata.get("matching_hints") or {}
                searchable = " ".join(
                    [
                        *(
                            str(metadata.get(key, ""))
                            for key in ("record_id", "kind", "display_name", "field_ids")
                        ),
                        *(str(val) for val in hints_map.values()),
                        str((metadata.get("provenance") or {}).get("hint", "")),
                    ]
                ).casefold()
                if normalized_query not in searchable:
                    continue
            items_out.append(metadata)
        items_out.sort(
            key=lambda entry: (
                0 if entry.get("is_default") else 1,
                entry["record_id"],
            )
        )
        yield from items_out

    def match_financial_records(
        self,
        *,
        kind: str | None = None,
        matching_hints: dict[str, str] | None = None,
        card_suffix: str | None = None,
        query: str | None = None,
        is_default: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Return metadata-only financial records matching safe card hints.

        This supports deterministic selection when a wallet contains duplicate
        cards or multiple expiration dates. An optional four- or five-digit
        suffix is compared inside the vault; stored values are never returned.
        Retrieving card fields still requires an authenticated, scoped read.
        """

        normalized_kind = None
        if kind is not None:
            normalized_kind = _normal_text(kind, "financial kind").lower()
            if normalized_kind not in _FINANCIAL_KINDS:
                raise BrokerError("financial kind is unsupported")
        if card_suffix is not None:
            if not isinstance(card_suffix, str) or not re.fullmatch(r"[0-9]{4,5}", card_suffix):
                raise BrokerError("card_suffix must contain four or five digits")
            if normalized_kind not in (None, "payment_card"):
                raise BrokerError("card_suffix is only supported for payment cards")
        requested = self._matching_hints(
            {}, matching_hints
        )
        records = self._iter_financial_records(
            kind=normalized_kind,
            query=query,
            card_suffix=card_suffix,
            is_default=is_default,
        )
        result = []
        for item in records:
            if normalized_kind and item["kind"] != normalized_kind:
                continue
            available = item.get("matching_hints", {})
            if all(
                str(available.get(key, "")).casefold() == str(value).casefold()
                for key, value in requested.items()
            ):
                result.append(item)
        return result

    def _prepare_scope_patch_entries(
        self,
        raw_entry: dict[str, Any],
    ) -> list[tuple[str, str, dict[str, Any], dict[str, Any]]]:
        """Validate one secret-free metadata/scope update and preserve stored values."""

        if not isinstance(raw_entry, dict):
            raise BrokerError("each entry must be an object")
        record_type = _record_type(raw_entry.get("record_type"))
        for key in raw_entry:
            if not isinstance(key, str):
                raise BrokerError("entry keys must be strings")
            normalized_key = _normalize_snake_case(key)
            if (
                normalized_key in _FORBIDDEN_SCOPE_PATCH_SECRET_KEYS
                or _forbidden_transient_name(key)
            ):
                raise BrokerError(
                    "scope/metadata updates cannot include raw secrets or transient fields"
                )
            if key not in _SCOPE_PATCH_KEYS[record_type]:
                raise BrokerError(f"unknown {record_type} scope update field: {key}")

        if record_type == "profile":
            group_id = raw_entry.get("group_id")
            has_record_id = (
                raw_entry.get("record_id") is not None
                or raw_entry.get("field_id") is not None
            )
            if group_id is not None and has_record_id:
                raise BrokerError("provide either record_id/field_id or group_id, not both")
            target_items: list[tuple[str, dict[str, Any]]] = []
            if group_id is not None:
                normalized_gid = _normal_identifier(group_id, "group_id")
                for row in self.store.db.execute(
                    "select key from kv where namespace='profile' order by key"
                ):
                    stored = self.store.get("profile", row["key"])
                    if not stored:
                        continue
                    defaults = self._profile_defaults(row["key"], stored)
                    if profile_group_id(defaults) == normalized_gid:
                        target_items.append((row["key"], defaults))
                if not target_items:
                    raise BrokerError(f"profile group not found: {normalized_gid}")
            else:
                fid = _entry_record_id(raw_entry, "profile")
                stored = self.store.get("profile", fid)
                if stored is None:
                    raise BrokerError(f"profile record not found: {fid}")
                target_items.append((fid, self._profile_defaults(fid, stored)))

            out: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
            for fid, current in target_items:
                updated = dict(current)
                if "category" in raw_entry and raw_entry["category"] is not None:
                    category = _normal_text(raw_entry["category"], "category")
                    if _normalize_snake_case(category) in _FORBIDDEN_PROFILE_CATEGORIES:
                        raise BrokerError("profile category is forbidden")
                    updated["category"] = category
                if "display_name" in raw_entry and raw_entry["display_name"] is not None:
                    if group_id is not None and len(target_items) > 1:
                        raise BrokerError(
                            "display_name cannot be patched across a multi-field group; use group_label"
                        )
                    display_name = _normal_text(raw_entry["display_name"], "display_name")
                    if _contains_raw_financial_number(display_name):
                        raise BrokerError("display_name cannot contain a raw financial number")
                    updated["display_name"] = display_name
                if "sensitivity" in raw_entry and raw_entry["sensitivity"] is not None:
                    sensitivity = _normal_text(raw_entry["sensitivity"], "sensitivity").lower()
                    if sensitivity not in {"normal", "sensitive"}:
                        raise BrokerError("profile sensitivity must be normal or sensitive")
                    updated["sensitivity"] = sensitivity
                if "allowed_origins" in raw_entry:
                    updated["allowed_origins"] = _scope_origins(
                        raw_entry["allowed_origins"], "allowed_origins"
                    )
                if "allowed_purposes" in raw_entry:
                    updated["allowed_purposes"] = _scope_purposes(
                        raw_entry["allowed_purposes"], "allowed_purposes"
                    )
                provenance = dict(updated.get("provenance") or {})
                if "provenance" in raw_entry and raw_entry["provenance"] is not None:
                    provenance.update(
                        _safe_provenance(
                            raw_entry["provenance"],
                            default_source=str(provenance.get("source", "local-profile")),
                        )
                    )
                if "group_label" in raw_entry:
                    raw_label = raw_entry["group_label"]
                    if raw_label is None or (isinstance(raw_label, str) and not raw_label.strip()):
                        provenance.pop("group_label", None)
                    else:
                        label_text = _normal_text(raw_label, "group_label")
                        if _contains_raw_financial_number(label_text):
                            raise BrokerError("group_label cannot contain a raw financial number")
                        provenance["group_label"] = label_text
                if "is_primary" in raw_entry and raw_entry["is_primary"] is not None:
                    if not isinstance(raw_entry["is_primary"], bool):
                        raise BrokerError("is_primary must be a boolean")
                    if raw_entry["is_primary"]:
                        provenance["is_primary"] = True
                    else:
                        provenance.pop("is_primary", None)
                updated["provenance"] = _safe_provenance(
                    provenance,
                    default_source=str(provenance.get("source", "local-profile")),
                )
                updated["updated_at"] = now_iso()
                out.append(("profile", fid, updated, current))
            return out

        if record_type == "credential":
            cid = _entry_record_id(raw_entry, "credential")
            stored = self.store.get("credential", cid)
            if stored is None:
                raise BrokerError(f"credential record not found: {cid}")
            current = self._credential_defaults(cid, stored)
            updated = dict(current)
            if "name" in raw_entry and raw_entry["name"] is not None:
                name = _normal_text(raw_entry["name"], "credential name")
                if _contains_raw_financial_number(name):
                    raise BrokerError("credential name cannot contain a raw financial number")
                updated["name"] = name
            if "origin" in raw_entry and raw_entry["origin"] is not None:
                stored_origin = _normal_text(raw_entry["origin"], "credential origin")
                _normalize_origin(stored_origin, "credential origin")
                updated["origin"] = stored_origin
            if "username" in raw_entry and raw_entry["username"] is not None:
                updated["username"] = _normal_text(
                    raw_entry["username"],
                    "credential username",
                    required=False,
                    strip=False,
                )
            if "note" in raw_entry and raw_entry["note"] is not None:
                updated["note"] = _normal_text(
                    raw_entry["note"], "credential note", required=False, strip=False
                )
            if "allowed_origins" in raw_entry:
                updated["allowed_origins"] = _scope_origins(
                    raw_entry["allowed_origins"], "allowed_origins"
                )
            if "allowed_purposes" in raw_entry:
                updated["allowed_purposes"] = _scope_purposes(
                    raw_entry["allowed_purposes"], "allowed_purposes"
                )
            if "provenance" in raw_entry and raw_entry["provenance"] is not None:
                merged_prov = dict(updated.get("provenance") or {})
                merged_prov.update(
                    _safe_provenance(
                        raw_entry["provenance"],
                        default_source=str(
                            merged_prov.get("source", "local-credential-vault")
                        ),
                    )
                )
                updated["provenance"] = _safe_provenance(
                    merged_prov,
                    default_source=str(
                        merged_prov.get("source", "local-credential-vault")
                    ),
                )
            updated["updated_at"] = now_iso()
            return [("credential", cid, updated, current)]

        rid = _entry_record_id(raw_entry, "financial")
        stored = self.store.get("financial", rid)
        if stored is None:
            raise BrokerError(f"financial record not found: {rid}")
        current = self._financial_defaults(rid, stored)
        updated = dict(current)
        if "display_name" in raw_entry and raw_entry["display_name"] is not None:
            display_name = _normal_text(raw_entry["display_name"], "display_name")
            if _contains_raw_financial_number(display_name):
                raise BrokerError("display_name cannot contain a raw financial number")
            updated["display_name"] = display_name
        if "matching_hints" in raw_entry and raw_entry["matching_hints"] is not None:
            if not isinstance(raw_entry["matching_hints"], dict):
                raise BrokerError("matching_hints must be an object")
            merged_hints = {
                k: v
                for k, v in dict(updated.get("matching_hints") or {}).items()
                if k
                not in {
                    "last4",
                    "account_last4",
                    "expiration_month",
                    "expiration_year",
                }
            }
            for hk, hv in raw_entry["matching_hints"].items():
                if hv is None or hv == "":
                    merged_hints.pop(hk, None)
                else:
                    merged_hints[hk] = hv
            for derived_key, val_keys in (
                ("last4", ("card_number",)),
                ("account_last4", ("account_number", "bank_account_number")),
                ("expiration_month", ("expiration_month",)),
                ("expiration_year", ("expiration_year",)),
            ):
                if derived_key in raw_entry["matching_hints"] and any(
                     vk in updated["values"] for vk in val_keys
                ):
                    derived_expected = self._matching_hints(updated["values"], {})
                    supplied_checked = self._matching_hints(
                        {}, {derived_key: raw_entry["matching_hints"][derived_key]}
                    )
                    if supplied_checked.get(derived_key) != derived_expected.get(
                        derived_key
                    ):
                        raise BrokerError(
                            f"matching hint {derived_key} conflicts with stored financial record"
                        )
                if any(vk in updated["values"] for vk in val_keys):
                    merged_hints.pop(derived_key, None)
            updated["matching_hints"] = self._matching_hints(
                updated["values"], merged_hints
            )
        if "is_default" in raw_entry and raw_entry["is_default"] is not None:
            if not isinstance(raw_entry["is_default"], bool):
                raise BrokerError("is_default must be a boolean")
            updated["is_default"] = raw_entry["is_default"]
        if "allowed_origins" in raw_entry:
            updated["allowed_origins"] = _scope_origins(
                raw_entry["allowed_origins"], "allowed_origins"
            )
        if "allowed_purposes" in raw_entry:
            updated["allowed_purposes"] = _scope_purposes(
                raw_entry["allowed_purposes"], "allowed_purposes"
            )
        if "provenance" in raw_entry and raw_entry["provenance"] is not None:
            merged_prov = dict(updated.get("provenance") or {})
            merged_prov.update(
                _safe_provenance(
                    raw_entry["provenance"],
                    default_source=str(
                        merged_prov.get("source", "local-financial-vault")
                    ),
                )
            )
            updated["provenance"] = _safe_provenance(
                merged_prov,
                default_source=str(merged_prov.get("source", "local-financial-vault")),
            )
        updated["updated_at"] = now_iso()
        return [("financial", rid, updated, current)]

    def _clear_other_primary_profile_writes(
        self,
        active_field_ids: set[str],
    ) -> list[tuple[str, str, dict[str, Any]]]:
        writes: list[tuple[str, str, dict[str, Any]]] = []
        for row in self.store.db.execute(
            "select key from kv where namespace='profile' order by key"
        ):
            fid = row["key"]
            if fid in active_field_ids:
                continue
            other = self.store.get("profile", fid)
            if not other:
                continue
            other = self._profile_defaults(fid, other)
            prov = dict(other.get("provenance") or {})
            if prov.get("is_primary") is True:
                prov.pop("is_primary", None)
                other["provenance"] = prov
                other["updated_at"] = now_iso()
                writes.append(("profile", fid, other))
        return writes

    def update_record_scope(
        self,
        agent_id: str,
        record_type: str | None = None,
        record_id: str | None = None,
        *,
        confirmed: Any = None,
        target_origin: str | None = None,
        purpose: str | None = None,
        entries: list[dict[str, Any]] | None = None,
        credential_id: str | None = None,
        field_id: str | None = None,
        group_id: str | None = None,
        allowed_origins: list[str] | None = None,
        allowed_purposes: list[str] | None = None,
        is_default: bool | None = None,
        matching_hints: dict[str, str] | None = None,
        display_name: str | None = None,
        name: str | None = None,
        origin: str | None = None,
        username: str | None = None,
        note: str | None = None,
        group_label: str | None = None,
        is_primary: bool | None = None,
        category: str | None = None,
        sensitivity: str | None = None,
        provenance: dict[str, Any] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Update record scopes and value-free metadata while preserving encrypted secrets."""

        for bad_key in extra:
            if bad_key in {
                "value",
                "values",
                "password",
                "card_number",
                "security_code",
                "cvv",
                "cvc",
            }:
                raise BrokerError(
                    "update_record_scope does not accept raw secret or record values"
                )
            raise BrokerError(f"unknown update_record_scope field: {bad_key}")
        if confirmed is not True:
            raise BrokerError("confirmed must be true")
        trusted_local = self._is_trusted_local(agent_id)
        if trusted_local:
            target = (
                _normalize_origin(target_origin, "target_origin")
                if target_origin is not None
                else "https://local.agent-autofill.invalid"
            )
            normalized_purpose = (
                _normal_text(purpose, "purpose")
                if purpose is not None
                else "local-scope-update"
            )
        else:
            target = _normalize_origin(target_origin, "target_origin")
            normalized_purpose = _normal_text(purpose, "purpose")

        if entries is not None:
            if record_type is not None or record_id is not None or group_id is not None:
                raise BrokerError("provide either entries or single-record fields, not both")
            raw_entries = entries
        else:
            single: dict[str, Any] = {"record_type": record_type}
            for key, val in (
                ("record_id", record_id),
                ("credential_id", credential_id),
                ("field_id", field_id),
                ("group_id", group_id),
                ("allowed_origins", allowed_origins),
                ("allowed_purposes", allowed_purposes),
                ("is_default", is_default),
                ("matching_hints", matching_hints),
                ("display_name", display_name),
                ("name", name),
                ("origin", origin),
                ("username", username),
                ("note", note),
                ("group_label", group_label),
                ("is_primary", is_primary),
                ("category", category),
                ("sensitivity", sensitivity),
                ("provenance", provenance),
            ):
                if val is not None:
                    single[key] = val
            raw_entries = [single]

        if not isinstance(raw_entries, list) or not raw_entries:
            raise BrokerError("entries must contain 1 to 100 records")
        if len(raw_entries) > _MAX_BATCH_RECORDS:
            raise BrokerError("entries must contain 1 to 100 records")

        prepared: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
        seen: set[tuple[str, str]] = set()
        for raw_entry in raw_entries:
            expanded = self._prepare_scope_patch_entries(raw_entry)
            for rtype, rid, normalized, current in expanded:
                pair = (rtype, rid)
                if pair in seen:
                    raise BrokerError("entries contain duplicate record ids")
                seen.add(pair)
                if not trusted_local:
                    self._authorized(
                        agent_id,
                        f"{rtype}.write",
                        rid,
                        target,
                        normalized_purpose,
                    )
                    _record_scope_allowed(
                        normalized["allowed_origins"],
                        normalized["allowed_purposes"],
                        target,
                        normalized_purpose,
                    )
                prepared.append((rtype, rid, normalized, current))

        results = []
        writes: list[tuple[str, str, dict[str, Any]]] = []
        default_financial_kinds: dict[str, str] = {}
        primary_profile_ids: set[str] = set()
        patched_financial_ids = {
            rid for rtype, rid, _, _ in prepared if rtype == "financial"
        }
        for rtype, rid, normalized, current in prepared:
            if self._same_record(current, normalized):
                status = "unchanged"
            else:
                status = "updated"
                writes.append((rtype, rid, normalized))
            if rtype == "financial" and normalized.get("is_default") is True:
                if normalized["kind"] in default_financial_kinds:
                    raise BrokerError(
                        "only one financial record per kind may set is_default=true"
                    )
                default_financial_kinds[normalized["kind"]] = rid
            if (
                rtype == "profile"
                and (normalized.get("provenance") or {}).get("is_primary") is True
            ):
                primary_profile_ids.add(rid)
            results.append(
                {
                    "record_type": rtype,
                    "record_id": rid,
                    "status": status,
                }
            )

        for kind, active_rid in default_financial_kinds.items():
            writes.extend(
                self._clear_other_default_financial_writes(
                    kind, active_rid, skip_ids=patched_financial_ids
                )
            )
        if primary_profile_ids:
            writes.extend(self._clear_other_primary_profile_writes(primary_profile_ids))

        self.store.apply_changes(
            writes,
            [],
            audits=[
                (
                    agent_id,
                    "records.scope_update",
                    "batch",
                    now_iso(),
                    {
                        "target_origin": target,
                        "purpose": normalized_purpose,
                        "records": results,
                    },
                )
            ],
        )
        updated_count = sum(1 for item in results if item["status"] == "updated")
        overall_status = "updated" if updated_count > 0 else "unchanged"
        response: dict[str, Any] = {
            "status": overall_status,
            "updated_count": updated_count,
            "records": results,
        }
        if len(prepared) == 1:
            single_type, single_id, single_norm, _ = prepared[0]
            response["record_type"] = single_type
            response["record_id"] = single_id
            response["allowed_origins"] = list(single_norm.get("allowed_origins", []))
            response["allowed_purposes"] = list(single_norm.get("allowed_purposes", []))
            if single_type == "financial":
                response["is_default"] = bool(single_norm.get("is_default", False))
        return response

    def write_records(
        self,
        agent_id: str,
        target_origin: Any = None,
        purpose: Any = None,
        confirmed: Any = None,
        entries: Any = None,
    ) -> dict[str, Any]:
        if confirmed is not True:
            raise BrokerError("confirmed must be true")
        trusted_local = self._is_trusted_local(agent_id)
        target = _normalize_origin(target_origin, "target_origin")
        purpose = _normal_text(purpose, "purpose")
        if not isinstance(entries, list) or not entries:
            raise BrokerError("entries must contain 1 to 100 records")
        if len(entries) > _MAX_BATCH_RECORDS:
            raise BrokerError("entries must contain 1 to 100 records")

        prepared: list[tuple[str, str, dict[str, Any], dict[str, Any] | None]] = []
        seen: set[tuple[str, str]] = set()
        for raw_entry in entries:
            if not isinstance(raw_entry, dict):
                raise BrokerError("each entry must be an object")
            if raw_entry.get("mode") in {"update_scope", "patch_metadata"}:
                for (
                    rtype,
                    rid,
                    patched_norm,
                    patched_curr,
                ) in self._prepare_scope_patch_entries(raw_entry):
                    pair = (rtype, rid)
                    if pair in seen:
                        raise BrokerError("entries contain duplicate record ids")
                    seen.add(pair)
                    if not trusted_local:
                        self._authorized(
                            agent_id,
                            f"{rtype}.write",
                            rid,
                            target,
                            purpose,
                        )
                        _record_scope_allowed(
                            patched_norm["allowed_origins"],
                            patched_norm["allowed_purposes"],
                            target,
                            purpose,
                        )
                    prepared.append((rtype, rid, patched_norm, patched_curr))
                continue
            record_type = _record_type(raw_entry.get("record_type"))
            record_id = _entry_record_id(raw_entry, record_type)
            pair = (record_type, record_id)
            if pair in seen:
                raise BrokerError("entries contain duplicate record ids")
            seen.add(pair)
            for key in raw_entry:
                if not isinstance(key, str):
                    raise BrokerError("entry keys must be strings")
                if key not in _RECORD_ENTRY_KEYS[record_type]:
                    if _forbidden_transient_name(key):
                        raise BrokerError(
                            "entries cannot store transient authentication material"
                        )
                    raise BrokerError(
                        f"unknown {record_type} record field: {key}"
                    )
            if record_type == "profile":
                normalized = self._prepare_profile_record(raw_entry)
                namespace = "profile"
                current = self.store.get(namespace, normalized["field_id"])
                if current:
                    current = self._profile_defaults(
                        normalized["field_id"], current
                    )
            elif record_type == "credential":
                normalized = self._prepare_credential_record(raw_entry)
                namespace = "credential"
                current = self.store.get(namespace, normalized["credential_id"])
                if current:
                    current = self._credential_defaults(
                        normalized["credential_id"], current
                    )
            else:
                normalized = self._prepare_financial_record(raw_entry)
                namespace = "financial"
                current = self.store.get(namespace, normalized["record_id"])
                if current:
                    current = self._financial_defaults(
                        normalized["record_id"], current
                    )
            if not trusted_local:
                self._authorized(
                    agent_id,
                    f"{record_type}.write",
                    record_id,
                    target,
                    purpose,
                )
                _record_scope_allowed(
                    normalized["allowed_origins"],
                    normalized["allowed_purposes"],
                    target,
                    purpose,
                )
            if current and "created_at" in current and "created_at" in normalized:
                normalized["created_at"] = current["created_at"]
            prepared.append((record_type, record_id, normalized, current))

        results = []
        writes: list[tuple[str, str, dict[str, Any]]] = []
        default_financial_kinds: dict[str, str] = {}
        written_financial_ids = {
            rid for rtype, rid, _, _ in prepared if rtype == "financial"
        }
        for record_type, record_id, normalized, current in prepared:
            if record_type == "financial" and normalized.get("is_default") is True:
                if normalized["kind"] in default_financial_kinds:
                    raise BrokerError(
                        "only one financial record per kind may set is_default=true"
                    )
                default_financial_kinds[normalized["kind"]] = record_id
            if current and self._same_record(current, normalized):
                status = "unchanged"
            else:
                status = "updated" if current else "created"
                namespace = record_type
                key = normalized.get(
                    "field_id", normalized.get("credential_id", record_id)
                )
                writes.append((namespace, key, normalized))
            results.append(
                {
                    "record_type": record_type,
                    "record_id": record_id,
                    "status": status,
                }
            )
        for kind, active_rid in default_financial_kinds.items():
            writes.extend(
                self._clear_other_default_financial_writes(
                    kind, active_rid, skip_ids=written_financial_ids
                )
            )
        self.store.apply_changes(
            writes,
            [],
            audits=[
                (
                    agent_id,
                    "records.write",
                    "batch",
                    now_iso(),
                    {
                        "target_origin": target,
                        "purpose": purpose,
                        "records": [
                            {
                                "record_type": record_type,
                                "record_id": record_id,
                                "status": next(
                                    item["status"]
                                    for item in results
                                    if item["record_type"] == record_type
                                    and item["record_id"] == record_id
                                ),
                            }
                            for record_type, record_id, _, _ in prepared
                        ],
                    },
                )
            ],
        )
        return {"records": results}

    @staticmethod
    def _read_provenance(
        item: dict[str, Any],
        default_source: str,
        record_id: str,
        target_origin: str,
        purpose: str,
        identifier_key: str = "record_id",
    ) -> dict[str, Any]:
        provenance = _safe_provenance(
            item.get("provenance"), default_source=default_source
        )
        provenance.setdefault("source", default_source)
        provenance[identifier_key] = record_id
        provenance["target_origin"] = target_origin
        provenance["purpose"] = purpose
        return provenance

    def request_credential(
        self,
        agent_id: str,
        delivery: str,
        credential_id: str,
        field_ids: list[str] | None = None,
        target_origin: str | None = None,
        purpose: str | None = None,
        expires_in: int = 60,
    ) -> dict[str, Any]:
        """Read a credential through one explicitly selected delivery mode.

        Direct delivery is the field-selective read path. Extension delivery
        issues the existing one-time origin-bound handle. Keeping the mode
        switch here prevents callers from silently falling back to the other
        exposure boundary when one path is refused.
        """

        if not isinstance(delivery, str) or delivery not in {"direct", "extension"}:
            raise BrokerError("delivery must be exactly direct or extension")
        normalized_id = _normal_identifier(credential_id, "credential_id")
        if isinstance(expires_in, bool) or not isinstance(expires_in, int):
            raise BrokerError("expires_in must be an integer")
        if expires_in < 1 or expires_in > 300:
            raise BrokerError(
                "credential handle lifetime must be between 1 and 300 seconds"
            )

        if delivery == "direct":
            if not isinstance(field_ids, list) or not field_ids:
                raise BrokerError("field_ids must contain 1 to 5 fields")
            if len(field_ids) > 5:
                raise BrokerError("field_ids must contain 1 to 5 fields")
            requested = self._requested_fields(
                field_ids, "field_ids", tuple(_CREDENTIAL_FIELDS)
            )
            normalized_target = (
                _normalize_origin(target_origin, "target_origin")
                if target_origin is not None
                else None
            )
            normalized_purpose = (
                _normal_text(purpose, "purpose") if purpose is not None else None
            )
            return self.request_credential_fields(
                agent_id,
                normalized_id,
                requested,
                normalized_target,
                normalized_purpose,
            )

        if field_ids is not None:
            raise BrokerError("field_ids are not accepted for extension delivery")
        if target_origin is None or purpose is None:
            raise BrokerError(
                "target_origin and purpose are required for extension delivery"
            )
        normalized_target = _normalize_origin(target_origin, "target_origin")
        normalized_purpose = _normal_text(purpose, "purpose")
        return self.request_credential_use(
            agent_id,
            normalized_id,
            normalized_target,
            normalized_purpose,
            expires_in,
        )

    def request_credential_fields(
        self,
        agent_id: str,
        credential_id: str,
        field_ids: list[str],
        target_origin: str | None = None,
        purpose: str | None = None,
    ) -> dict[str, Any]:
        record_id = _normal_identifier(credential_id, "credential_id")
        target, purpose = self._read_context(agent_id, target_origin, purpose)
        requested = self._requested_fields(
            field_ids, "field_ids", tuple(_CREDENTIAL_FIELDS)
        )
        self._authorized(
            agent_id, "credential.read", record_id, target, purpose
        )
        item = self.store.get("credential", record_id)
        if item is None:
            raise CredentialUseError("credential not found")
        item = self._credential_defaults(record_id, item)
        _credential_scope_allowed(
            item,
            target,
            purpose,
            trusted_local=self._is_trusted_local(agent_id),
        )
        result = {field_id: item[field_id] for field_id in requested}
        result["provenance"] = self._read_provenance(
            item,
            "local-credential-vault",
            record_id,
            target,
            purpose,
            "credential_id",
        )
        self.store.audit(
            agent_id,
            "credential.read",
            record_id,
            now_iso(),
            {
                "field_ids": requested,
                "target_origin": target,
                "purpose": purpose,
            },
        )
        return result

    def request_financial_fields(
        self,
        agent_id: str,
        record_id: str,
        field_ids: list[str],
        target_origin: str | None = None,
        purpose: str | None = None,
    ) -> dict[str, Any]:
        record_id = _normal_identifier(record_id, "record_id")
        target, purpose = self._read_context(agent_id, target_origin, purpose)
        self._authorized(agent_id, "financial.read", record_id, target, purpose)
        item = self.store.get("financial", record_id)
        if item is None:
            raise BrokerError("financial record not found")
        item = self._financial_defaults(record_id, item)
        requested = self._requested_fields(field_ids, "field_ids")
        for field_id in requested:
            if field_id not in item["values"]:
                raise BrokerError(f"unknown financial field: {field_id}")
        _record_scope_allowed(
            item["allowed_origins"],
            item["allowed_purposes"],
            target,
            purpose,
            trusted_local=self._is_trusted_local(agent_id),
        )
        result = {field_id: item["values"][field_id] for field_id in requested}
        result["provenance"] = self._read_provenance(
            item,
            "local-financial-vault",
            record_id,
            target,
            purpose,
        )
        self.store.audit(
            agent_id,
            "financial.read",
            record_id,
            now_iso(),
            {
                "field_ids": requested,
                "target_origin": target,
                "purpose": purpose,
            },
        )
        return result

    def delete_record(self, record_type: str, record_id: str) -> None:
        """Delete one local record and revoke grants that name that record."""

        record_type = _record_type(record_type)
        record_id = _normal_identifier(record_id, "record_id")
        _validate_no_transient_name(record_id, "record_id")
        namespace = record_type
        existing = self.store.get(namespace, record_id)
        if existing is None:
            raise BrokerError(f"{record_type} record not found")

        deletes: list[tuple[str, str]] = [(namespace, record_id)]
        if record_type == "credential":
            for row in self.store.db.execute(
                "select key,value from kv where namespace='credential-handle'"
            ).fetchall():
                handle = self.store._open(row["value"])
                if handle.get("credential_id") == record_id:
                    deletes.append(("credential-handle", row["key"]))

        grant_writes: list[tuple[str, str, dict[str, Any]]] = []
        revoked_operations = {
            f"{record_type}.read",
            f"{record_type}.write",
        }
        if record_type == "credential":
            revoked_operations.add("credential.use")
        for row in self.store.db.execute(
            "select key,value from kv where namespace='grant'"
        ).fetchall():
            grant = self.store._open(row["value"])
            grant_scope = grant.get("resource_scope")
            if (
                isinstance(grant_scope, str)
                and grant_scope.lower() == record_id
                and grant.get("operation") in revoked_operations
                and not grant.get("revoked", False)
            ):
                grant["revoked"] = True
                grant_writes.append(("grant", row["key"], grant))

        self.store.apply_changes(
            grant_writes,
            deletes,
            cascade_field_references=True,
            audits=[
                (
                    "local-admin",
                    f"{record_type}.delete",
                    record_id,
                    now_iso(),
                    {"record_type": record_type, "record_id": record_id},
                )
            ],
        )

    def request_credential_use(
        self,
        agent_id: str,
        credential_id: str,
        target_origin: str,
        purpose: str,
        expires_in: int = 60,
    ) -> dict:
        if not target_origin or not purpose:
            raise CredentialUseError("target_origin and purpose are required")
        if expires_in < 1 or expires_in > 300:
            raise CredentialUseError(
                "credential handle lifetime must be between 1 and 300 seconds"
            )
        credential_id = _normal_identifier(credential_id, "credential_id")
        target = _normalize_origin(target_origin, "target_origin")
        purpose = _normal_text(purpose, "purpose")
        self._authorized(agent_id, "credential.use", credential_id, target, purpose)
        item = self.store.get("credential", credential_id)
        if item is None:
            raise CredentialUseError("credential not found")
        item = self._credential_defaults(credential_id, item)
        try:
            _credential_scope_allowed(
                item,
                target,
                purpose,
                trusted_local=self._is_trusted_local(agent_id),
            )
        except AuthorizationError as exc:
            raise CredentialUseError(str(exc)) from exc
        try:
            credential_host = _origin_hostname(item["origin"])
        except BrokerError as exc:
            raise CredentialUseError("credential origin is invalid") from exc
        target_host = _origin_hostname(target)
        allowed_origins = item.get("allowed_origins") or []
        if (
            target_host != credential_host
            and not any(_origin_matches(allowed, target) for allowed in allowed_origins)
            and not _registrable_domains_match(credential_host, target_host)
        ):
            raise CredentialUseError("target origin does not match credential origin")
        handle = secrets.token_urlsafe(32)
        self.store.put(
            "credential-handle",
            hashlib.sha256(handle.encode()).hexdigest(),
            {
                "agent_id": agent_id,
                "credential_id": credential_id,
                "purpose": purpose,
                "target_origin": target,
                "expires_at": (
                    datetime.now(UTC) + timedelta(seconds=expires_in)
                ).isoformat(),
                "consumed": False,
                "credential_handle": handle,
            },
        )
        self.store.audit(
            agent_id,
            "credential.use",
            credential_id,
            now_iso(),
            {"target_origin": target, "purpose": purpose},
        )
        return {
            "handle": handle,
            "username": item["username"],
            "origin": item["origin"],
            "expires_in": expires_in,
            "provenance": {
                "source": "local-credential-vault",
                "credential_id": credential_id,
            },
        }

    def consume_credential_handle(
        self,
        handle: str,
        agent_id: str,
        target_origin: str | None = None,
    ) -> dict:
        key = hashlib.sha256(handle.encode()).hexdigest()
        item = self.store.get("credential-handle", key)
        expires_at = _message_received_at(item.get("expires_at")) if item else None
        if (
            not item
            or item["agent_id"] != agent_id
            or item["consumed"]
            or expires_at is None
            or expires_at <= datetime.now(UTC)
        ):
            raise CredentialUseError(
                "credential handle is invalid, expired, or already consumed"
            )
        if target_origin is not None:
            try:
                target = _normalize_origin(target_origin, "target_origin")
            except BrokerError as exc:
                raise CredentialUseError("target origin is invalid") from exc
            issued_origin = item.get("target_origin")
            if not isinstance(issued_origin, str) or not (
                _origin_matches(issued_origin, target)
                or (
                    issued_origin.startswith("https://")
                    and target.startswith("https://")
                    and _registrable_domains_match(
                        _origin_hostname(issued_origin),
                        _origin_hostname(target),
                    )
                )
            ):
                raise CredentialUseError(
                    "target origin does not match credential handle"
                )
        credential = self.store.get("credential", item["credential_id"])
        if credential is None:
            raise CredentialUseError("credential not found")
        credential = self._credential_defaults(item["credential_id"], credential)
        self._authorized(
            agent_id, "credential.use", item["credential_id"],
            item["target_origin"], item["purpose"],
        )
        _credential_scope_allowed(
            credential,
            item["target_origin"],
            item["purpose"],
            trusted_local=self._is_trusted_local(agent_id),
        )

        def claimable(current: dict[str, Any]) -> bool:
            current_expires_at = _message_received_at(current.get("expires_at"))
            return bool(
                current.get("agent_id") == agent_id
                and not current.get("consumed", False)
                and current_expires_at is not None
                and current_expires_at > datetime.now(UTC)
            )

        claimed = self.store.update_if(
            "credential-handle",
            key,
            claimable,
            lambda current: {**current, "consumed": True, "credential_handle": None},
        )
        if not claimed:
            raise CredentialUseError(
                "credential handle is invalid, expired, or already consumed"
            )
        return {
            "username": credential["username"],
            "password": credential["password"],
            "origin": credential["origin"],
        }

    def grant(
        self,
        agent_id: str,
        operation: str,
        resource_scope: str,
        target_origin: str | None,
        purpose: str | None,
        expires_in: int = 600,
    ) -> Grant:
        if expires_in < 1 or expires_in > 86400:
            raise BrokerError("expires_in must be between 1 and 86400 seconds")
        issued = datetime.now(UTC)
        g = Grant(
            agent_id,
            operation,
            resource_scope,
            target_origin,
            purpose,
            issued.isoformat(),
            (issued + timedelta(seconds=expires_in)).isoformat(),
        )
        self.store.put("grant", secrets.token_hex(12), g.__dict__)
        return g

    def _grant_email_scope(
        self,
        agent_id: str,
        operation: str,
        resource_scope: str,
        target_origin: str | None,
        purpose: str | None,
        expires_in: int = 600,
    ) -> Grant:
        if operation not in _EMAIL_GRANT_OPERATIONS:
            raise BrokerError("email grant operation is unsupported")
        return self.grant(
            agent_id,
            operation,
            resource_scope,
            target_origin,
            purpose,
            expires_in,
        )

    def grant_email_recipient(
        self,
        agent_id: str,
        operation: str,
        recipient: str,
        target_origin: str | None,
        purpose: str | None,
        expires_in: int = 600,
    ) -> Grant:
        """Create an email grant from an address without exposing source IDs."""

        return self._grant_email_scope(
            agent_id,
            operation,
            self.resolve_email_account(recipient),
            target_origin,
            purpose,
            expires_in,
        )

    def grant_email_all(
        self,
        agent_id: str,
        operation: str,
        target_origin: str | None,
        purpose: str | None,
        expires_in: int = 600,
    ) -> Grant:
        """Create an explicit wildcard email grant for a service.

        This is intentionally an owner-facing convenience. Runtime callers
        still identify one recipient, and the broker resolves that recipient
        to one configured mailbox before reading anything.
        """

        return self._grant_email_scope(
            agent_id,
            operation,
            "*",
            target_origin,
            purpose,
            expires_in,
        )

    def create_agent(self, agent_id: str) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        self.store.put(
            "agent",
            agent_id,
            {
                "agent_id": agent_id,
                "token_hash": token_hash,
                "created_at": now_iso(),
                "revoked": False,
            },
        )
        return token

    def enable_trusted_local(self, agent_id: str) -> None:
        """Mark a Keychain-paired local service as trusted for local reads and confirmed writes.

        Local services authenticate with a token recovered from the operating
        system credential store.  That pairing is the owner's installation
        decision, so requiring a second, expiring per-record grant for every
        read or user-confirmed write makes the installed skill unusable.
        Keep explicit ``confirmed=True`` checks, validation, audit logging,
        agent revocation, and non-trusted/remote grant enforcement intact.
        """

        item = self.store.get("agent", agent_id)
        if item is None:
            raise AuthorizationError("unknown agent identity")
        if item.get("revoked", False):
            raise AuthorizationError("agent identity is revoked")
        if not item.get("trusted_local", False):
            item["trusted_local"] = True
            self.store.put("agent", agent_id, item)

    def _is_trusted_local(self, agent_id: str) -> bool:
        item = self.store.get("agent", agent_id)
        return bool(
            item
            and item.get("trusted_local", False)
            and not item.get("revoked", False)
            and str(agent_id).startswith("service:")
        )

    def _read_context(
        self,
        agent_id: str,
        target_origin: str | None,
        purpose: str | None,
    ) -> tuple[str, str]:
        """Normalize optional context for trusted local reads.

        Local services already authenticated through Keychain do not need to
        manufacture a target-form scope just to read a saved value. Keep the
        context in provenance, while preserving the old required arguments for
        grant-based and remote agents.
        """

        if not target_origin or not purpose:
            if not self._is_trusted_local(agent_id):
                raise BrokerError("target_origin and purpose are required")
            return "local://agent-autofill", "local-read"
        return _normalize_origin(target_origin, "target_origin"), _normal_text(
            purpose, "purpose"
        )

    def authenticate_agent(self, agent_id: str, token: str | None) -> bool:
        if not token:
            return False
        item = self.store.get("agent", agent_id)
        return bool(
            item
            and not item.get("revoked", False)
            and hmac.compare_digest(
                item["token_hash"], hashlib.sha256(token.encode()).hexdigest()
            )
        )

    def list_agents(self) -> list[dict]:
        result = []
        for row in self.store.db.execute("select key from kv where namespace='agent'"):
            item = self.store.get("agent", row["key"])
            if item:
                result.append(
                    {
                        "agent_id": item["agent_id"],
                        "created_at": item["created_at"],
                        "revoked": item.get("revoked", False),
                        "access_mode": (
                            "trusted-local-read"
                            if item.get("trusted_local", False)
                            else "grant-required"
                        ),
                    }
                )
        return result

    def revoke_agent(self, agent_id: str) -> None:
        grant_writes: list[tuple[str, str, dict[str, Any]]] = []
        for row in self.store.db.execute(
            "select key,value from kv where namespace='grant'"
        ).fetchall():
            d = self.store._open(row["value"])
            if d["agent_id"] == agent_id:
                d["revoked"] = True
                grant_writes.append(("grant", row["key"], d))
        item = self.store.get("agent", agent_id)
        if item:
            item["revoked"] = True
            grant_writes.append(("agent", agent_id, item))
        self.store.apply_changes(grant_writes, [])

    def list_grants(self) -> list[dict]:
        result = []
        for row in self.store.db.execute(
            "select key,value from kv where namespace='grant'"
        ):
            grant = self.store._open(row["value"])
            result.append(
                {"grant_id": row["key"], **grant, "active": Grant(**grant).active()}
            )
        return result

    def _authorized(
        self, agent_id, operation, resource, origin=None, purpose=None
    ) -> Grant:
        if (
            operation in _TRUSTED_LOCAL_READ_OPERATIONS
            and self._is_trusted_local(agent_id)
        ):
            issued = datetime.now(UTC)
            return Grant(
                agent_id,
                operation,
                "*",
                None,
                None,
                issued.isoformat(),
                "9999-12-31T23:59:59+00:00",
            )
        rows = self.store.db.execute(
            "select value from kv where namespace='grant'"
        ).fetchall()
        for row in rows:
            d = self.store._open(row["value"])
            if (
                d["agent_id"] == agent_id
                and d["operation"] == operation
                and d["resource_scope"] in ("*", resource)
                and (
                    d["target_origin"] is None
                    or _origin_matches(d["target_origin"], origin)
                )
                and d["purpose"] in (None, purpose)
                and Grant(**d).active()
            ):
                return Grant(**d)
        raise AuthorizationError(f"no active grant for {operation}:{resource}")

    def list_sources(
        self,
        *,
        kind: str | None = None,
        query: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List bounded source metadata, optionally narrowed by kind or text."""

        normalized_kind = (
            _normal_text(kind, "kind").casefold() if kind is not None else None
        )
        normalized_query = (
            _normal_text(query, "query").casefold() if query is not None else None
        )
        rows = []
        for item in self.sources.values():
            item_kind = str(item.get("kind", "")).casefold()
            searchable = " ".join(
                str(item.get(key, "")) for key in ("source_id", "kind", "label")
            ).casefold()
            if normalized_kind is not None and item_kind != normalized_kind:
                continue
            if normalized_query is not None and normalized_query not in searchable:
                continue
            rows.append(dict(item))
        return rows[: _bounded_limit(limit)]

    def _ensure_email_source(self, account_id: str) -> object | None:
        if account_id in self.removed_email_sources:
            raise BrokerError("email account is not configured")
        if account_id in self.unavailable_email_sources:
            raise BrokerError("email source is unavailable; run email-test")
        if account_id in self.email_adapters:
            return self.email_adapters[account_id]
        if account_id in self.email:
            if self.sources.get(account_id, {}).get("kind") not in (None, "email"):
                raise BrokerError("email account is not configured")
            return None
        if self.sources.get(account_id, {}).get("kind") == "email":
            raise BrokerError("email source is unavailable; run email-test")
        raise BrokerError("email account is not configured")

    def list_profile_fields(
        self,
        *,
        category: str | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List bounded profile metadata without returning stored values."""

        return _metadata_page(self._iter_profile_fields(category=category, query=query), limit, offset)

    def _iter_profile_fields(self, *, category=None, query=None):
        normalized_category = (
            _normal_text(category, "category").casefold()
            if category is not None
            else None
        )
        normalized_query = (
            _normal_text(query, "query").casefold() if query is not None else None
        )
        for row in self.store.db.execute(
            "select key from kv where namespace='profile' order by key"
        ):
            item = self.store.get("profile", row["key"])
            if not item:
                continue
            item = self._profile_defaults(row["key"], item)
            metadata = {
                key: item[key]
                for key in _PROFILE_METADATA_FIELDS
                if key in item
            }
            if (
                normalized_category is not None
                and str(metadata.get("category", "")).casefold()
                != normalized_category
            ):
                continue
            if normalized_query is not None:
                prov = metadata.get("provenance") or {}
                searchable = " ".join(
                    [
                        *(
                            str(metadata.get(key, ""))
                            for key in ("field_id", "display_name", "category")
                        ),
                        str(prov.get("hint", "")),
                        str(prov.get("group_label", "")),
                    ]
                ).casefold()
                if normalized_query not in searchable:
                    continue
            yield metadata

    def list_profile_groups(self) -> list[dict[str, Any]]:
        """List metadata-only groups with bounded disambiguation summaries."""

        raw_items_by_id: dict[str, dict[str, Any]] = {}
        for row in self.store.db.execute(
            "select key from kv where namespace='profile' order by key"
        ):
            item = self.store.get("profile", row["key"])
            if item:
                raw_items_by_id[row["key"]] = self._profile_defaults(row["key"], item)

        confirmed_bundles_by_fid: dict[str, dict[str, Any]] = {}
        self_emails: set[str] = set()
        self_name_pairs: set[tuple[str, str]] = set()
        self_full_names: set[str] = set()

        for ctx_row in self.store.db.execute(
            "select key from kv where namespace='context' and key like 'bundle:%' order by key"
        ):
            bundle = self.store.get("context", ctx_row["key"])
            if (
                not isinstance(bundle, dict)
                or bundle.get("authority") != "confirmed"
                or not isinstance(bundle.get("field_versions"), dict)
            ):
                continue
            field_versions = bundle["field_versions"]
            if not field_versions or not all(
                raw_items_by_id.get(fid, {}).get("updated_at") == ver
                for fid, ver in field_versions.items()
            ):
                continue
            data = bundle.get("data", {})
            if not isinstance(data, dict):
                data = {}
            meaning = {
                "bundle_id": bundle.get("id", ctx_row["key"]),
                "subject": data.get("subject", "unknown"),
                "role": data.get("role", "unknown"),
                "currentness": data.get("currentness", "unknown"),
                "title": data.get("title", ""),
            }
            for fid in field_versions:
                if fid not in confirmed_bundles_by_fid or str(ctx_row["key"]).startswith(
                    "bundle:review_"
                ):
                    confirmed_bundles_by_fid[fid] = meaning
            if data.get("subject") == "self":
                fields_map = data.get("fields")
                if isinstance(fields_map, dict):
                    fn_id = fields_map.get("first_name")
                    ln_id = fields_map.get("last_name")
                    nm_id = fields_map.get("name") or fields_map.get("full_name")
                    em_id = fields_map.get("email")
                    fn_item = raw_items_by_id.get(fn_id, {}) if isinstance(fn_id, str) else {}
                    ln_item = raw_items_by_id.get(ln_id, {}) if isinstance(ln_id, str) else {}
                    nm_item = raw_items_by_id.get(nm_id, {}) if isinstance(nm_id, str) else {}
                    em_item = raw_items_by_id.get(em_id, {}) if isinstance(em_id, str) else {}
                    fn_val = str(fn_item.get("value") or "").strip().casefold()
                    ln_val = str(ln_item.get("value") or "").strip().casefold()
                    nm_val = normalize_form_label(nm_item.get("value") or "")
                    em_val = str(em_item.get("value") or "").strip().casefold()
                    if fn_val and ln_val:
                        self_name_pairs.add((fn_val, ln_val))
                        self_full_names.add(normalize_form_label(f"{fn_val} {ln_val}"))
                    if nm_val:
                        self_full_names.add(nm_val)
                    if em_val and "@" in em_val:
                        self_emails.add(em_val)

        # Also extract self identity signals from standalone local-profile records.
        local_fn = ""
        local_ln = ""
        for item in raw_items_by_id.values():
            raw_prov = item.get("provenance")
            item_prov: dict[str, Any] = raw_prov if isinstance(raw_prov, dict) else {}
            if item_prov.get("source") == "chrome-structured-address":
                continue
            val = item.get("value")
            if not isinstance(val, str) or not val.strip():
                continue
            label_norm = normalize_form_label(item.get("display_name") or item.get("field_id") or "")
            fid_norm = normalize_form_label(item.get("field_id") or "")
            if label_norm in {"email", "email address"} or fid_norm in {"email", "email address"}:
                if "@" in val:
                    self_emails.add(val.strip().casefold())
            elif label_norm in {"first name", "given name"} or fid_norm in {"first name", "given name"}:
                local_fn = val.strip().casefold()
            elif label_norm in {"last name", "family name", "surname"} or fid_norm in {"last name", "family name", "surname"}:
                local_ln = val.strip().casefold()
            elif label_norm in {"full name", "name"} or fid_norm in {"full name", "name"}:
                self_full_names.add(normalize_form_label(val))
        if local_fn and local_ln:
            self_name_pairs.add((local_fn, local_ln))
            self_full_names.add(normalize_form_label(f"{local_fn} {local_ln}"))

        groups: dict[str, dict[str, Any]] = {}
        group_raw_items: dict[str, list[dict[str, Any]]] = {}
        for item in raw_items_by_id.values():
            provenance = item.get("provenance")
            if not isinstance(provenance, dict):
                provenance = {}
            profile_id = provenance.get("source_profile_id")
            source_key = provenance.get("source_guid") or provenance.get(
                "source_guid_hash"
            )
            if isinstance(profile_id, str) and isinstance(source_key, str):
                source = provenance.get("source", "chrome-structured-address")
            else:
                source = provenance.get("source", "local-profile")
            group_id = profile_group_id(item)
            group = groups.setdefault(
                group_id,
                {
                    "group_id": group_id,
                    "source": source,
                    "source_profile_id": profile_id,
                    "field_ids": [],
                    "field_types": [],
                    "fields_by_type": {},
                },
            )
            group["field_ids"].append(item["field_id"])
            field_type = provenance.get("field_type") or item.get("display_name")
            if isinstance(field_type, str) and field_type not in group["field_types"]:
                group["field_types"].append(field_type)
            if isinstance(field_type, str) and field_type and field_type not in group["fields_by_type"]:
                group["fields_by_type"][field_type] = item["field_id"]
            group_raw_items.setdefault(group_id, []).append(item)

        for group_id, group in groups.items():
            group["field_ids"].sort()
            group["field_types"].sort()
            group["fields_by_type"] = dict(sorted(group["fields_by_type"].items()))
            group["field_count"] = len(group["field_ids"])
            is_chrome_group = group["source"] == "chrome-structured-address"
            group["needs_selection"] = is_chrome_group and group["field_count"] > 1
            if group["needs_selection"]:
                group["selection_reason"] = (
                    "Choose this address group by meaning (for example home, work, "
                    "origin, or destination) before filling a form."
                )
            else:
                group["selection_reason"] = None

            review_bundle = self.store.get("context", f"bundle:review_{group_id}")
            confirmed_meaning: dict[str, Any] | None = None
            if (
                isinstance(review_bundle, dict)
                and review_bundle.get("authority") == "confirmed"
                and isinstance(review_bundle.get("field_versions"), dict)
                and all(
                    raw_items_by_id.get(fid, {}).get("updated_at") == ver
                    for fid, ver in review_bundle["field_versions"].items()
                )
            ):
                data = review_bundle.get("data", {})
                confirmed_meaning = {
                    "bundle_id": review_bundle.get("id"),
                    "subject": data.get("subject", "unknown"),
                    "role": data.get("role", "unknown"),
                    "currentness": data.get("currentness", "unknown"),
                    "title": data.get("title", ""),
                }
            else:
                for fid in group["field_ids"]:
                    if fid in confirmed_bundles_by_fid:
                        confirmed_meaning = dict(confirmed_bundles_by_fid[fid])
                        break
            if confirmed_meaning is not None:
                group["confirmed_meaning"] = confirmed_meaning

            group_items = group_raw_items.get(group_id, [])
            explicit_label: str | None = None
            explicit_primary = False
            first_val: Any = None
            last_val: Any = None
            full_val: Any = None
            email_val: str = ""
            state_raw: Any = None
            country_raw: Any = None

            for g_item in group_items:
                g_raw_prov = g_item.get("provenance")
                prov: dict[str, Any] = (
                    g_raw_prov if isinstance(g_raw_prov, dict) else {}
                )
                if explicit_label is None:
                    cand_label = (
                        prov.get("group_label")
                        or prov.get("label")
                        or prov.get("hint")
                    )
                    if isinstance(cand_label, str) and cand_label.strip():
                        explicit_label = cand_label.strip()
                if prov.get("is_primary") is True:
                    explicit_primary = True
                ftype = str(prov.get("field_type") or "").strip()
                dname = normalize_form_label(g_item.get("display_name") or "")
                val = g_item.get("value")
                if ftype in {"first_name", "givenName"} or dname in {"first name", "chrome first name"}:
                    first_val = val
                elif ftype in {"last_name", "familyName"} or dname in {"last name", "chrome last name"}:
                    last_val = val
                elif ftype == "name" or dname in {"full name", "name", "chrome name"}:
                    full_val = val
                elif ftype == "email" or dname in {"email", "email address", "chrome email"}:
                    if isinstance(val, str):
                        email_val = val.strip().casefold()
                elif ftype in {"state", "addressLevel1"} or dname in {"state", "province", "chrome state"}:
                    state_raw = val
                elif ftype in {"country", "countryCode"} or dname in {"country", "chrome country"}:
                    country_raw = val

            confirmed_self_current = bool(
                confirmed_meaning
                and confirmed_meaning.get("subject") == "self"
                and confirmed_meaning.get("currentness") == "current"
            )
            is_primary = explicit_primary or confirmed_self_current
            if explicit_label is None and confirmed_meaning:
                title = str(confirmed_meaning.get("title") or "").strip()
                if title:
                    explicit_label = title

            initials: str | None = None
            state_code: str | None = None
            country_code: str | None = None
            matches_self = False
            if is_chrome_group or group["field_count"] > 1:
                initials = masked_recipient_initials(
                    first_name=first_val,
                    last_name=last_val,
                    full_name=full_val,
                )
                state_code = safe_state_code(state_raw)
                country_code = safe_country_code(country_raw)
                matches_self = bool(
                    (confirmed_meaning and confirmed_meaning.get("subject") == "self")
                    or (email_val and email_val in self_emails)
                    or (
                        isinstance(first_val, str)
                        and isinstance(last_val, str)
                        and (
                            (first_val.strip().casefold(), last_val.strip().casefold())
                            in self_name_pairs
                            or normalize_form_label(f"{first_val} {last_val}")
                            in self_full_names
                        )
                    )
                    or (
                        isinstance(full_val, str)
                        and (
                            normalize_form_label(full_val) in self_full_names
                            or (
                                len(full_val.split()) == 2
                                and (
                                    full_val.split()[0].casefold(),
                                    full_val.split()[1].casefold(),
                                )
                                in self_name_pairs
                            )
                        )
                    )
                )

            summary = build_group_summary(
                group_label=explicit_label,
                is_primary=is_primary,
                matches_self_profile=matches_self,
                confirmed_meaning=confirmed_meaning,
                recipient_initials=initials,
                state=state_code,
                country=country_code,
            )
            group["group_label"] = explicit_label
            group["is_primary"] = is_primary
            group["matches_self_profile"] = matches_self
            group["recipient_initials"] = initials
            group["state"] = state_code
            group["country"] = country_code
            group["summary"] = summary
            disambiguation: dict[str, Any] = {"is_primary": is_primary}
            if explicit_label:
                disambiguation["group_label"] = explicit_label
            if matches_self:
                disambiguation["matches_self_profile"] = True
            if initials:
                disambiguation["recipient_initials"] = initials
            if state_code:
                disambiguation["state"] = state_code
            if country_code:
                disambiguation["country"] = country_code
            group["disambiguation"] = disambiguation

        return sorted(
            groups.values(),
            key=lambda item: (
                0 if item.get("is_primary") else 1,
                0 if item.get("matches_self_profile") else 1,
                item["group_id"],
            ),
        )

    def match_form_fields(
        self,
        fields: list[dict[str, Any]],
        limit: int = 100,
        target_origin: str | None = None,
        purpose: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return value-free mapping and missing-field guidance."""

        if not isinstance(fields, list) or not fields:
            raise BrokerError("fields must contain 1 to 100 items")
        if len(fields) > _MAX_SCOPE_ITEMS:
            raise BrokerError("fields must contain 1 to 100 items")
        if (target_origin is None) != (purpose is None):
            raise BrokerError("target_origin and purpose must be provided together")
        bounded = _bounded_limit(limit)
        preferences: dict[str, dict[str, Any]] = {}
        target: str | None = None
        normalized_purpose: str | None = None
        if target_origin is not None and purpose is not None:
            target = _normalize_origin(target_origin, "target_origin")
            normalized_purpose = _normal_text(purpose, "purpose")
            for raw in fields[:bounded]:
                label = raw.get("label", raw.get("display_name", raw.get("field_id", "")))
                normalized_label = normalize_form_label(label)
                if not normalized_label:
                    continue
                preference = self.store.get(
                    _FORM_MAPPING_NAMESPACE,
                    self._form_mapping_key(target, normalized_purpose, normalized_label),
                )
                if isinstance(preference, dict):
                    preferences[normalized_label] = preference
        group_metadata = {
            group["group_id"]: group for group in self.list_profile_groups()
        }
        try:
            return _match_form_fields(
                fields[:bounded],
                list(self._iter_profile_fields()),
                preferences,
                target_origin=target,
                purpose=normalized_purpose,
                group_metadata=group_metadata,
            )
        except (TypeError, ValueError) as exc:
            raise BrokerError(str(exc)) from exc

    @staticmethod
    def _form_mapping_key(target_origin: str, purpose: str, label: str) -> str:
        digest = hashlib.sha256(
            f"{target_origin}\0{purpose}\0{label}".encode()
        ).hexdigest()[:24]
        return "mapping_" + digest

    def record_form_mapping_feedback(
        self,
        agent_id: str,
        target_origin: Any = None,
        purpose: Any = None,
        field_label: Any = None,
        selected_field_ids: Any = None,
        group_id: Any = None,
        outcome: Any = "accepted",
        confirmed: Any = None,
    ) -> dict[str, Any]:
        """Remember a value-free mapping choice after the user decision point."""

        if confirmed is not True:
            raise BrokerError("confirmed must be true")
        target = _normalize_origin(target_origin, "target_origin")
        normalized_purpose = _normal_text(purpose, "purpose")
        label = normalize_form_label(field_label)
        if not label:
            raise BrokerError("field_label is required")
        _validate_no_transient_name(label, "field_label")
        if any(
            term in label
            for term in (
                "card number",
                "account number",
                "routing number",
                "security code",
                "credit card",
                "payment",
                "secret",
            )
        ):
            raise BrokerError("field_label cannot identify a secret or financial field")
        if not isinstance(selected_field_ids, list) or not selected_field_ids:
            raise BrokerError("selected_field_ids must contain 1 to 100 items")
        if len(selected_field_ids) > _MAX_SCOPE_ITEMS:
            raise BrokerError("selected_field_ids must contain 1 to 100 items")
        field_ids = [_normal_field_id(item, "selected field id") for item in selected_field_ids]
        if len(set(field_ids)) != len(field_ids):
            raise BrokerError("selected_field_ids contain duplicates")
        if outcome not in _FORM_MAPPING_OUTCOMES:
            raise BrokerError("outcome must be accepted or corrected")
        if group_id is not None:
            group_id = _normal_text(group_id, "group_id")
            group_fields = self._profile_group_field_ids(group_id)
            if not set(field_ids).issubset(group_fields):
                raise BrokerError("selected fields are outside profile group")
        known_fields = {
            item["field_id"] for item in self._iter_profile_fields()
        }
        if not set(field_ids).issubset(known_fields):
            raise BrokerError("selected field is not in profile memory")
        record = {
            "target_origin": target,
            "purpose": normalized_purpose,
            "label": label,
            "field_ids": field_ids,
            "group_id": group_id,
            "outcome": outcome,
            "updated_at": now_iso(),
        }
        key = self._form_mapping_key(target, normalized_purpose, label)
        self.store.apply_changes(
            [(_FORM_MAPPING_NAMESPACE, key, record)],
            [],
            audits=[
                (
                    agent_id,
                    "form-mapping.write",
                    key,
                    record["updated_at"],
                    {
                        "target_origin": target,
                        "purpose": normalized_purpose,
                        "label": label,
                        "field_ids": field_ids,
                        "group_id": group_id,
                        "outcome": outcome,
                    },
                )
            ],
        )
        return {
            "status": "stored",
            "target_origin": target,
            "purpose": normalized_purpose,
            "label": label,
            "field_ids": field_ids,
            "group_id": group_id,
            "outcome": outcome,
        }

    def _profile_group(self, group_id: str) -> dict[str, Any]:
        if not isinstance(group_id, str) or not group_id.strip():
            raise BrokerError("group_id must be a string")
        for group in self.list_profile_groups():
            if group["group_id"] == group_id:
                return group
        raise BrokerError("unknown profile group")

    def _profile_group_field_ids(self, group_id: str) -> set[str]:
        return set(self._profile_group(group_id)["field_ids"])

    def _resolve_profile_group_fields(
        self, group_id: str, requested: list[str]
    ) -> list[str]:
        group = self._profile_group(group_id)
        group_fields = set(group["field_ids"])
        fields_by_type = group.get("fields_by_type", {})
        by_type_ci = {
            ftype.strip().lower(): fid
            for ftype, fid in fields_by_type.items()
        }
        semantics_ci = {k.lower(): v for k, v in _CHROME_FIELD_SEMANTICS.items()}
        by_semantic: dict[str, list[str]] = {}
        for ftype, fid in fields_by_type.items():
            semantic = semantics_ci.get(ftype.strip().lower())
            if semantic is not None:
                by_semantic.setdefault(semantic, []).append(fid)

        resolved: list[str] = []
        for raw_fid in requested:
            if raw_fid in group_fields:
                target_fid = raw_fid
            elif raw_fid in by_type_ci:
                target_fid = by_type_ci[raw_fid]
            else:
                semantic = semantics_ci.get(raw_fid)
                candidates = by_semantic.get(semantic, []) if semantic else []
                if len(candidates) == 1:
                    target_fid = candidates[0]
                elif semantic and semantic in by_type_ci:
                    target_fid = by_type_ci[semantic]
                else:
                    raise BrokerError("requested profile fields are outside profile group")
            if target_fid in resolved:
                raise BrokerError("field_ids cannot contain duplicate fields")
            resolved.append(target_fid)
        return resolved

    def request_profile_fields(
        self,
        agent_id,
        field_ids,
        target_origin: str | None = None,
        purpose: str | None = None,
        group_id: str | None = None,
        bundle_id: str | None = None,
    ):
        target, purpose = self._read_context(agent_id, target_origin, purpose)
        requested = self._requested_fields(
            field_ids, "field_ids", identifiers=True
        )
        if group_id is not None:
            requested = self._resolve_profile_group_fields(group_id, requested)
        bundle = None
        if bundle_id is not None:
            from .context_memory import ContextMemory, scope_matches

            memory = ContextMemory(self)
            bundle = self.store.get("context", f"bundle:{_normal_identifier(bundle_id, 'bundle_id')}")
            if not bundle or not scope_matches(bundle, target, purpose):
                raise BrokerError("bundle unavailable in this context")
            memory._authorized_fields(agent_id, bundle["field_ids"], target, purpose)
            if not memory._fresh(bundle) or not set(requested).issubset(bundle["field_ids"]):
                raise BrokerError("bundle evidence changed or selected fields are outside bundle")
        out = {}
        for fid in requested:
            self._authorized(agent_id, "profile.read", fid, target, purpose)
            item = self.store.get("profile", fid)
            if item is None:
                raise BrokerError(f"unknown profile field: {fid}")
            if bundle is not None and item.get("updated_at") != bundle["field_versions"].get(fid):
                raise BrokerError("bundle evidence changed during read; review the bundle")
            item = self._profile_defaults(fid, item)
            _record_scope_allowed(
                item["allowed_origins"],
                item["allowed_purposes"],
                target,
                purpose,
                trusted_local=self._is_trusted_local(agent_id),
            )
            out[fid] = {
                "value": item["value"],
                "provenance": self._read_provenance(
                    item, "local-profile", fid, target, purpose, "field_id"
                ),
            }
        if bundle is not None and not memory._fresh(bundle):
            raise BrokerError("bundle evidence changed during read; review the bundle")
        self.store.audit(
            agent_id,
            "profile.read",
            ",".join(requested),
            now_iso(),
            {"target_origin": target, "purpose": purpose, "field_ids": requested},
        )
        return out

    def search_email(self, agent_id, account_id, query, limit=20):
        limit = _bounded_limit(limit)
        self._authorized(agent_id, "email.search", account_id)
        adapter = self._ensure_email_source(account_id)
        q = query.lower()
        rows = [
            m
            for m in self.email.get(account_id, [])
            if q in (m.subject + " " + m.sender + " " + m.body).lower()
        ][:limit]
        if adapter is not None:
            rows = adapter.search(query, limit)
        self.store.audit(
            agent_id,
            "email.search",
            account_id,
            now_iso(),
            {
                "query_hash": hashlib.sha256(query.encode()).hexdigest(),
                "count": len(rows),
            },
        )
        return [
            m.__dict__
            | {"provenance": {"account_id": account_id, "message_id": m.message_id}}
            for m in rows
        ]

    def resolve_email_account(self, recipient: str) -> str:
        """Resolve one exact configured mailbox address to its opaque source.

        Clients identify a mailbox by the address they already know.  The
        broker keeps source IDs private and refuses both fuzzy matches and
        ambiguous duplicate addresses.
        """

        matches = self.match_email_sources(recipient)
        if not matches:
            raise BrokerError("email recipient is not configured")
        if len(matches) != 1:
            raise BrokerError("email recipient is ambiguous")
        return matches[0]["source_id"]

    def match_email_sources(self, recipient: str) -> list[dict[str, str]]:
        """Return metadata for exact configured mailbox recipient matches.

        The returned source id and provider label are routing metadata only;
        connector usernames, tokens, and mailbox contents are never returned.
        An empty list means the recipient is not configured.  Multiple rows
        are returned so callers can explain an ambiguous configuration before
        attempting a scoped read.
        """

        if not isinstance(recipient, str):
            raise BrokerError("recipient must be a string")
        address = recipient.strip().casefold()
        if (
            not address
            or len(address) > 320
            or address.count("@") != 1
            or address.startswith("@")
            or address.endswith("@")
        ):
            raise BrokerError("recipient must be an email address")

        configs: dict[str, dict[str, Any]] = {}
        for row in self.store.db.execute(
            "select key from kv where namespace='connection'"
        ):
            config = self.store.get("connection", row["key"])
            if not isinstance(config, dict) or config.get("kind") not in {"imap", "gmail"}:
                continue
            source_id = config.get("source_id", row["key"])
            if isinstance(source_id, str):
                configs[source_id] = config

        matches: dict[str, dict[str, str]] = {}
        for source_id, source in self.sources.items():
            if source.get("kind") != "email":
                continue
            config = configs.get(source_id, {})
            username = config.get("username")
            label = source.get("label")
            if not (
                isinstance(username, str)
                and username.strip().casefold() == address
            ):
                continue
            provider = (
                label.strip()
                if isinstance(label, str) and label.strip()
                else str(config.get("provider") or config.get("host") or "email")
            )
            matches[source_id] = {
                "source_id": source_id,
                "provider": provider,
                "label": provider,
                "kind": "email",
            }

        # A persisted connection can exist before its source metadata is
        # reconstructed.  Include it only when its kind and exact username
        # prove the recipient match, while keeping the result value-free.
        for source_id, config in configs.items():
            username = config.get("username")
            if not (
                isinstance(username, str)
                and username.strip().casefold() == address
            ):
                continue
            if source_id in matches:
                continue
            provider = str(config.get("provider") or config.get("host") or "email")
            matches[source_id] = {
                "source_id": source_id,
                "provider": provider,
                "label": provider,
                "kind": "email",
            }
        return [matches[key] for key in sorted(matches)]

    def get_email_message(self, agent_id, account_id, message_id):
        self._authorized(agent_id, "email.read", account_id)
        adapter = self._ensure_email_source(account_id)
        if adapter is not None:
            m = adapter.get(message_id)
            self.store.audit(
                agent_id,
                "email.read",
                account_id,
                now_iso(),
                {"message_id": message_id},
            )
            return m.__dict__ | {
                "provenance": {"account_id": account_id, "message_id": message_id}
            }
        for m in self.email.get(account_id, []):
            if m.message_id == message_id:
                self.store.audit(
                    agent_id,
                    "email.read",
                    account_id,
                    now_iso(),
                    {"message_id": message_id},
                )
                return m.__dict__ | {
                    "provenance": {"account_id": account_id, "message_id": message_id}
                }
        raise BrokerError("message not found")

    def _resolve_messages_source(
        self, source_id: str | None
    ) -> tuple[str, object | None]:
        if source_id is not None:
            sid = _normal_text(source_id, "source_id")
            if sid in self.removed_email_sources:
                raise BrokerError("messages source is not configured")
            if sid in self.message_adapters:
                return sid, self.message_adapters[sid]
            if self.sources.get(sid, {}).get("kind") == "messages":
                if self.texts:
                    return sid, None
                raise BrokerError("messages source is unavailable; run messages-test")
            if sid == "messages" and self.texts and not self.message_adapters:
                return sid, None
            raise BrokerError("messages source is not configured")
        configured = sorted(
            {
                *(
                    sid
                    for sid, src in self.sources.items()
                    if src.get("kind") == "messages"
                ),
                *self.message_adapters.keys(),
            }
        )
        if len(configured) == 1:
            return self._resolve_messages_source(configured[0])
        if len(configured) > 1:
            raise BrokerError(
                "multiple messages sources configured; specify source_id"
            )
        if self.texts:
            return "messages", None
        raise BrokerError("no messages source is configured; run messages-add")

    def request_auth_code(
        self,
        agent_id,
        account_id,
        target_origin=None,
        purpose=None,
        max_age_seconds=300,
        origin_policy="target-origin",
    ):
        if (
            isinstance(account_id, str)
            and (
                self.sources.get(account_id, {}).get("kind") == "messages"
                or account_id in self.message_adapters
            )
        ):
            mapped_policy = (
                "messages-read"
                if isinstance(origin_policy, str)
                and origin_policy.strip().lower() == "mailbox-read"
                else origin_policy
            )
            return self.request_messages_auth_code(
                agent_id,
                source_id=account_id,
                target_origin=target_origin,
                purpose=purpose,
                max_age_seconds=max_age_seconds,
                origin_policy=mapped_policy,
            )
        origin_policy = _normalize_otp_origin_policy(origin_policy)
        try:
            max_age_seconds = int(max_age_seconds)
        except (TypeError, ValueError):
            raise BrokerError("max_age_seconds must be an integer")
        if max_age_seconds < 1 or max_age_seconds > 3600:
            raise BrokerError("max_age_seconds must be between 1 and 3600 seconds")
        normalized_target = (
            _normalize_origin(target_origin, "target_origin")
            if target_origin
            else None
        )
        normalized_purpose = _normal_text(purpose, "purpose") if purpose else None
        if origin_policy == "target-origin" and not normalized_target:
            raise BrokerError("target-origin OTP selection requires target_origin")
        self._authorized(
            agent_id,
            "email.otp",
            account_id,
            normalized_target,
            normalized_purpose,
        )
        self.store.purge_legacy_otp_markers()
        adapter = self._ensure_email_source(account_id)
        now = datetime.now(UTC)
        candidates = []
        stale = False
        unmatched = False
        messages = (
            adapter.recent(min(3600, max(900, max_age_seconds * 2)))
            if adapter is not None and hasattr(adapter, "recent")
            else self.email.get(account_id, [])
        )
        for m in messages:
            received_at = _message_received_at(m.received_at)
            if received_at is None:
                continue
            age = (now - received_at).total_seconds()
            if age < 0 or age > 3600:
                continue
            codes = _extract_auth_codes(m.body)
            if not codes:
                continue
            if origin_policy == "target-origin" and not _otp_sender_matches(m.sender, normalized_target):
                unmatched = unmatched or age <= max_age_seconds
                continue
            if age > max_age_seconds:
                stale = True
                continue
            candidates.extend((m, c, received_at) for c in codes)
        if not candidates:
            if stale:
                raise AuthCodeError("otp_stale", "Only expired authentication codes were found",
                                    "Request a new code on the website, then retry")
            if unmatched:
                raise AuthCodeError("otp_service_mismatch", "No recent code matched the target service",
                                    "Verify the service's sender domain; complete sign-in manually if it is unsupported")
            raise AuthCodeError("otp_not_found", "No authentication code was found in the checked messages",
                                "Wait for delivery and retry; if none arrives, request a new code on the website")
        # Use the newest message only; never fall back to an older code.
        newest_received_at = max(item[2] for item in candidates)
        newest = [
            item
            for item in candidates
            if item[2] == newest_received_at
        ]
        if len(newest) != 1:
            raise AuthCodeError("otp_ambiguous", "No unambiguous recent authentication code",
                                "Complete verification manually or request one new code; do not guess")
        m, code, _ = newest[0]
        used_key = hashlib.sha256(
            f"{account_id}\0{m.message_id}".encode()
        ).hexdigest()
        if not self.store.put_if_absent(
            "otp-consumed", used_key, {"used_at": now_iso()}
        ):
            raise AuthCodeError("otp_consumed", "Authentication code already consumed",
                                "Check whether sign-in succeeded; otherwise request a new code on the website")
        self.store.audit(
            agent_id,
            "email.otp",
            account_id,
            now_iso(),
            {
                "message_id": m.message_id,
                "target_origin": normalized_target,
                "purpose": normalized_purpose,
                "origin_policy": origin_policy,
            },
        )
        return {
            "code": code,
            "provenance": {
                "source": "email",
                "account_id": account_id,
                "message_id": m.message_id,
                "received_at": m.received_at,
            },
        }

    def request_auth_code_for_recipient(
        self,
        agent_id,
        recipient,
        target_origin=None,
        purpose=None,
        max_age_seconds=300,
        origin_policy="target-origin",
    ):
        """Return a code for an exact recipient without exposing source IDs."""

        account_id = self.resolve_email_account(recipient)
        return self.request_auth_code(
            agent_id,
            account_id,
            target_origin,
            purpose,
            max_age_seconds,
            origin_policy,
        )

    def request_verification_link_for_recipient(
        self,
        agent_id: str,
        recipient: str,
        target_origin: str,
        purpose: str,
        max_age_seconds: int = 300,
    ) -> dict[str, Any]:
        """Return one recent, target-origin-bound verification link.

        This is intentionally narrower than email search or message reads:
        the caller receives only the selected URL and safe provenance.  The
        message body, sender, subject, connector identity, and link are never
        sent to an audit or error surface.  A hash-only marker makes the
        selected link one-time for this mailbox, even if another authorized
        agent later presents the same message.
        """

        target = _normalize_origin(target_origin, "target_origin")
        purpose = _normal_text(purpose, "purpose")
        try:
            max_age_seconds = int(max_age_seconds)
        except (TypeError, ValueError) as exc:
            raise BrokerError("max_age_seconds must be an integer") from exc
        if max_age_seconds < 1 or max_age_seconds > 3600:
            raise BrokerError("max_age_seconds must be between 1 and 3600 seconds")

        account_id = self.resolve_email_account(recipient)
        self._authorized(
            agent_id,
            "email.verification_link",
            account_id,
            target,
            purpose,
        )
        self.store.purge_legacy_otp_markers()
        adapter = self._ensure_email_source(account_id)
        now = datetime.now(UTC)
        messages = (
            adapter.recent(max_age_seconds)
            if adapter is not None and hasattr(adapter, "recent")
            else self.email.get(account_id, [])
        )

        candidates: list[tuple[EmailMessage, str]] = []
        for message in messages:
            received_at = _message_received_at(message.received_at)
            if received_at is None:
                continue
            age = (now - received_at).total_seconds()
            if age < 0 or age > max_age_seconds:
                continue
            text = f"{message.sender} {message.subject} {message.body}"
            for raw_link in _VERIFICATION_LINK_PATTERN.findall(text):
                link = raw_link.rstrip(_VERIFICATION_LINK_TRAILING)
                try:
                    link_origin = _normalize_origin(link, "verification link")
                except BrokerError:
                    continue
                if _origin_matches(link_origin, target):
                    candidates.append((message, link))

        if len(candidates) != 1:
            raise BrokerError(
                "no unambiguous recent verification link associated with target origin"
            )
        message, link = candidates[0]
        link_digest = hashlib.sha256(link.encode()).hexdigest()
        consumed_key = hashlib.sha256(
            f"{account_id}\0{message.message_id}\0{link_digest}".encode()
        ).hexdigest()
        if not self.store.put_if_absent(
            "verification-link-consumed", consumed_key, {"used_at": now_iso()}
        ):
            raise BrokerError("verification link already consumed")
        self.store.audit(
            agent_id,
            "email.verification_link",
            account_id,
            now_iso(),
            {
                "message_id": message.message_id,
                "target_origin": target,
                "purpose": purpose,
            },
        )
        return {
            "url": link,
            "provenance": {
                "source": "email",
                "account_id": account_id,
                "message_id": message.message_id,
                "received_at": message.received_at,
            },
        }

    def request_messages_auth_code(
        self,
        agent_id: str,
        source_id: str | None = None,
        target_origin: str | None = None,
        purpose: str | None = None,
        max_age_seconds: int = 300,
        origin_policy: str = "target-origin",
        conversation_scope: str = "*",
        sender: str | None = None,
    ) -> dict[str, Any]:
        """Return one recent 2FA code from local macOS Messages without exposing text bodies."""

        policy = _normalize_messages_otp_origin_policy(origin_policy)
        try:
            max_age_seconds = int(max_age_seconds)
        except (TypeError, ValueError):
            raise BrokerError("max_age_seconds must be an integer")
        if max_age_seconds < 1 or max_age_seconds > 3600:
            raise BrokerError("max_age_seconds must be between 1 and 3600 seconds")
        normalized_target = (
            _normalize_origin(target_origin, "target_origin")
            if target_origin
            else None
        )
        normalized_purpose = _normal_text(purpose, "purpose") if purpose else None
        normalized_scope = (
            "*"
            if conversation_scope is None
            else _normal_text(conversation_scope, "conversation_scope")
        )
        normalized_sender = (
            _normal_text(sender, "sender")
            if sender is not None and str(sender).strip()
            else None
        )
        if policy == "target-origin" and not normalized_target:
            raise BrokerError("target-origin OTP selection requires target_origin")

        resolved_source_id, adapter = self._resolve_messages_source(source_id)
        try:
            self._authorized(
                agent_id,
                "messages.otp",
                resolved_source_id,
                normalized_target,
                normalized_purpose,
            )
        except AuthorizationError:
            if normalized_scope != "*":
                self._authorized(
                    agent_id,
                    "messages.otp",
                    normalized_scope,
                    normalized_target,
                    normalized_purpose,
                )
            else:
                raise

        self.store.purge_legacy_otp_markers()
        now = datetime.now(UTC)
        lookback = min(3600, max(900, max_age_seconds * 2))
        if adapter is not None and hasattr(adapter, "recent"):
            messages = adapter.recent(
                lookback,
                conversation_scope=normalized_scope,
                sender=normalized_sender,
            )
        elif adapter is not None and hasattr(adapter, "search"):
            messages = adapter.search("", normalized_scope, 100)
        else:
            messages = [
                m
                for m in self.texts
                if (normalized_scope == "*" or m.conversation_id == normalized_scope)
                and m.sender != "me"
            ]

        candidates = []
        stale = False
        unmatched = False
        for m in messages:
            if normalized_scope != "*" and m.conversation_id != normalized_scope:
                continue
            if m.sender == "me":
                continue
            if normalized_sender and not _sender_matches_filter(
                m.sender, normalized_sender
            ):
                continue
            received_at = _message_received_at(m.sent_at)
            if received_at is None:
                continue
            age = (now - received_at).total_seconds()
            if age < 0 or age > 3600:
                continue
            codes = _extract_auth_codes(m.body)
            if not codes:
                continue
            if policy == "target-origin" and not _messages_otp_matches_target(
                m.sender, m.body, normalized_target or ""
            ):
                unmatched = unmatched or age <= max_age_seconds
                continue
            if age > max_age_seconds:
                stale = True
                continue
            candidates.extend((m, c, received_at) for c in codes)

        if not candidates:
            if stale:
                raise AuthCodeError(
                    "otp_stale",
                    "Only expired authentication codes were found in Messages",
                    "Request a new SMS code on the website, then retry",
                )
            if unmatched:
                raise AuthCodeError(
                    "otp_service_mismatch",
                    "No recent Messages code matched the target service",
                    "Verify the SMS sender or retry with explicit origin_policy='messages-read' or a sender filter if the service sends generic texts",
                )
            raise AuthCodeError(
                "otp_not_found",
                "No authentication code was found in recent Messages",
                "Wait for SMS delivery and retry; ensure iPhone Text Message Forwarding to this Mac is enabled",
            )

        newest_received_at = max(item[2] for item in candidates)
        newest = [item for item in candidates if item[2] == newest_received_at]
        if len(newest) != 1:
            raise AuthCodeError(
                "otp_ambiguous",
                "No unambiguous recent authentication code in Messages",
                "Complete verification manually, narrow with sender/conversation_scope, or request one new code",
            )
        m, code, _ = newest[0]
        used_key = hashlib.sha256(
            f"messages\0{resolved_source_id}\0{m.message_id}".encode()
        ).hexdigest()
        if not self.store.put_if_absent(
            "otp-consumed", used_key, {"used_at": now_iso()}
        ):
            raise AuthCodeError(
                "otp_consumed",
                "Authentication code already consumed",
                "Check whether sign-in succeeded; otherwise request a new SMS code on the website",
            )
        self.store.audit(
            agent_id,
            "messages.otp",
            resolved_source_id,
            now_iso(),
            {
                "conversation_id": m.conversation_id,
                "message_id": m.message_id,
                "target_origin": normalized_target,
                "purpose": normalized_purpose,
                "origin_policy": policy,
            },
        )
        return {
            "code": code,
            "provenance": {
                "source": "messages",
                "source_id": resolved_source_id,
                "conversation_id": m.conversation_id,
                "message_id": m.message_id,
                "received_at": m.sent_at,
            },
        }

    def search_messages(self, agent_id, conversation_scope, query, limit=20):
        limit = _bounded_limit(limit)
        self._authorized(agent_id, "messages.search", conversation_scope)
        q = query.lower()
        rows = []
        for adapter in self.message_adapters.values():
            if hasattr(adapter, "search"):
                rows.extend(adapter.search(query, conversation_scope, limit))
        rows.extend(
            [
                m
                for m in self.texts
                if (
                    conversation_scope == "*" or m.conversation_id == conversation_scope
                )
                and q in (m.body + m.sender).lower()
            ][:limit]
        )
        rows = rows[:limit]
        self.store.audit(
            agent_id,
            "messages.search",
            conversation_scope,
            now_iso(),
            {
                "query_hash": hashlib.sha256(query.encode()).hexdigest(),
                "count": len(rows),
            },
        )
        return [
            m.__dict__
            | {
                "provenance": {
                    "source": "messages",
                    "conversation_id": m.conversation_id,
                    "message_id": m.message_id,
                }
            }
            for m in rows
        ]

    def list_conversations(self, agent_id, source_id="*"):
        self._authorized(agent_id, "messages.list", source_id)
        result = []
        for adapter in self.message_adapters.values():
            if hasattr(adapter, "list_conversations"):
                result.extend(adapter.list_conversations())
        result.extend(
            {"conversation_id": cid, "source": "memory"}
            for cid in sorted({m.conversation_id for m in self.texts})
            if source_id in ("*", cid)
        )
        self.store.audit(
            agent_id, "messages.list", source_id, now_iso(), {"count": len(result)}
        )
        return result

    def get_message(self, agent_id, message_id):
        for adapter in self.message_adapters.values():
            try:
                m = adapter.get(message_id)
                self._authorized(agent_id, "messages.read", m.conversation_id)
                self.store.audit(
                    agent_id,
                    "messages.read",
                    m.conversation_id,
                    now_iso(),
                    {"message_id": message_id},
                )
                return m.__dict__ | {
                    "provenance": {
                        "source": "messages",
                        "conversation_id": m.conversation_id,
                        "message_id": message_id,
                    }
                }
            except KeyError:
                continue
        for m in self.texts:
            if m.message_id == message_id:
                self._authorized(agent_id, "messages.read", m.conversation_id)
                self.store.audit(
                    agent_id,
                    "messages.read",
                    m.conversation_id,
                    now_iso(),
                    {"message_id": message_id},
                )
                return m.__dict__ | {
                    "provenance": {
                        "source": "messages",
                        "conversation_id": m.conversation_id,
                        "message_id": message_id,
                    }
                }
        raise BrokerError("message not found")
