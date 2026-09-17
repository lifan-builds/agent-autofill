from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets
from typing import Any
from urllib.parse import urlparse
from .models import Grant, EmailMessage, TextMessage, now_iso
from .storage import Store


class BrokerError(Exception):
    pass


class AuthorizationError(BrokerError):
    pass


class CredentialUseError(BrokerError):
    pass


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
_SAFE_HINT_KEYS = {
    "account_last4",
    "country",
    "currency",
    "institution",
    "last4",
    "network",
}
_FINANCIAL_RESERVED_FIELDS = {
    "allowed_origins",
    "allowed_purposes",
    "created_at",
    "display_name",
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
        "sensitivity",
        "allowed_origins",
        "allowed_purposes",
        "provenance",
    },
}


def _contains_raw_financial_number(value: str) -> bool:
    """Detect common full-number forms without treating safe last-four hints as raw."""

    return bool(
        re.search(r"\d{8,}", value)
        or re.search(
            r"(?<![A-Za-z0-9])(?:\d[\s-]*){12,}(?![A-Za-z0-9])", value
        )
    )


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


def _forbidden_transient_name(value: str) -> bool:
    normalized = re.sub(
        r"[^A-Za-z0-9]+",
        "_",
        re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value),
    ).lower().strip("_")
    if not normalized:
        return False
    parts = normalized.split("_")
    for forbidden in _FORBIDDEN_TRANSIENT_PARTS:
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


def _forbidden_sensitive_name(value: str) -> bool:
    normalized = re.sub(
        r"[^A-Za-z0-9]+",
        "_",
        re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value),
    ).lower().strip("_")
    if not normalized:
        return False
    parts = normalized.split("_")
    for forbidden in _FORBIDDEN_SENSITIVE_PARTS:
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
                normalized_key = re.sub(
                    r"[^a-z0-9]+",
                    "_",
                    re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key).lower(),
                ).strip("_")
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
    allowed_origins: list[str], allowed_purposes: list[str], target_origin: str, purpose: str
) -> None:
    if allowed_origins and not any(
        _origin_matches(allowed, target_origin) for allowed in allowed_origins
    ):
        raise AuthorizationError("target origin is not allowed for record")
    if allowed_purposes and purpose not in allowed_purposes:
        raise AuthorizationError("purpose is not allowed for record")


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

    def parts(value):
        parsed = urlparse(value if "://" in value else "//" + value)
        try:
            port = parsed.port
        except ValueError:
            port = -1
        return (parsed.scheme.lower(), (parsed.hostname or "").lower(), port)

    a, b = parts(left), parts(right)
    return a == b or (not a[0] or not b[0]) and a[1:] == b[1:]


def _origin_hostname(value: str) -> str:
    """Return the hostname from a normalized URL or scheme-less origin."""

    normalized = _normalize_origin(value)
    parsed = urlparse(normalized if "://" in normalized else "//" + normalized)
    return (parsed.hostname or "").lower()


def _hostname_token_matches(text: str, hostname: str) -> bool:
    """Match a hostname as a complete token in provider text.

    URL and email delimiters such as ``/``, ``?``, ``@``, ``<`` and ``>`` are
    valid boundaries. Hostname characters are not, so ``notexample.com`` and
    ``example.com.evil`` cannot accidentally bind an OTP to ``example.com``.
    """

    if not hostname:
        return False
    boundary = r"A-Za-z0-9_.-"
    pattern = rf"(?<![{boundary}]){re.escape(hostname)}(?![{boundary}])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


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
        # Keep a tombstone even when the persisted source was unavailable and
        # no adapter was reconstructed. This prevents stale in-memory fallback
        # messages from becoming readable again after removal.
        self.removed_email_sources.add(source_id)
        self.sources.pop(source_id, None)
        self.email_adapters.pop(source_id, None)
        self.unavailable_email_sources.discard(source_id)
        self.message_adapters.pop(source_id, None)
        self.store.db.execute(
            "delete from kv where namespace in ('source','connection') and key=?",
            (source_id,),
        )
        self.store.db.commit()
        for row in self.store.db.execute(
            "select key,value from kv where namespace='grant'"
        ).fetchall():
            grant = self.store._open(row["value"])
            if grant["resource_scope"] == source_id:
                grant["revoked"] = True
                self.store.put("grant", row["key"], grant)

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

    def list_credentials(self) -> list[dict]:
        result = []
        for row in self.store.db.execute(
            "select key from kv where namespace='credential'"
        ):
            item = self.store.get("credential", row["key"])
            if item:
                item = self._credential_defaults(row["key"], item)
                result.append(self._credential_metadata(item))
        return result

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
        kind: str, values: dict[str, str], supplied: Any
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
            result["kind"], result["values"], result.get("matching_hints")
        )
        result["allowed_origins"] = _scope_origins(
            result.get("allowed_origins"), "allowed_origins"
        )
        result["allowed_purposes"] = _scope_purposes(
            result.get("allowed_purposes"), "allowed_purposes"
        )
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
                kind, values, entry.get("matching_hints")
            ),
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
    ) -> None:
        record = self._prepare_financial_record(
            {
                "record_type": "financial",
                "record_id": record_id,
                "kind": kind,
                "display_name": display_name,
                "values": values,
                "matching_hints": matching_hints,
                "allowed_origins": allowed_origins,
                "allowed_purposes": allowed_purposes,
                "provenance": provenance,
            }
        )
        existing = self.store.get("financial", record["record_id"])
        if existing:
            existing = self._financial_defaults(record["record_id"], existing)
            record["created_at"] = existing.get("created_at", record["created_at"])
        self.store.put("financial", record["record_id"], record)

    # Keep a concise name for internal callers while exposing the descriptive
    # name in the public broker API and MCP catalog.
    put_financial = put_financial_record

    def list_financial_records(self) -> list[dict[str, Any]]:
        result = []
        for row in self.store.db.execute(
            "select key from kv where namespace='financial'"
        ):
            item = self.store.get("financial", row["key"])
            if not item:
                continue
            item = self._financial_defaults(row["key"], item)
            result.append(
                {
                    "record_id": item["record_id"],
                    "kind": item["kind"],
                    "display_name": item["display_name"],
                    "field_ids": sorted(item["values"]),
                    "matching_hints": dict(item["matching_hints"]),
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
            )
        return result

    list_financial = list_financial_records

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
            if current:
                if "created_at" in current and "created_at" in normalized:
                    normalized["created_at"] = current["created_at"]
            prepared.append((record_type, record_id, normalized, current))

        results = []
        writes: list[tuple[str, str, dict[str, Any]]] = []
        for record_type, record_id, normalized, current in prepared:
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

    def request_credential_fields(
        self,
        agent_id: str,
        credential_id: str,
        field_ids: list[str],
        target_origin: str,
        purpose: str,
    ) -> dict[str, Any]:
        if not target_origin or not purpose:
            raise BrokerError("target_origin and purpose are required")
        record_id = _normal_identifier(credential_id, "credential_id")
        target = _normalize_origin(target_origin, "target_origin")
        purpose = _normal_text(purpose, "purpose")
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
        _record_scope_allowed(
            item["allowed_origins"], item["allowed_purposes"], target, purpose
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
        target_origin: str,
        purpose: str,
    ) -> dict[str, Any]:
        if not target_origin or not purpose:
            raise BrokerError("target_origin and purpose are required")
        record_id = _normal_identifier(record_id, "record_id")
        target = _normalize_origin(target_origin, "target_origin")
        purpose = _normal_text(purpose, "purpose")
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
            item["allowed_origins"], item["allowed_purposes"], target, purpose
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
            _record_scope_allowed(
                item["allowed_origins"], item["allowed_purposes"], target, purpose
            )
        except AuthorizationError as exc:
            raise CredentialUseError(str(exc)) from exc
        try:
            credential_host = _origin_hostname(item["origin"])
        except BrokerError as exc:
            raise CredentialUseError("credential origin is invalid") from exc
        target_host = _origin_hostname(target)
        if target_host != credential_host:
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
                    datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                ).isoformat(),
                "consumed": False,
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

    def consume_credential_handle(self, handle: str, agent_id: str) -> dict:
        key = hashlib.sha256(handle.encode()).hexdigest()
        item = self.store.get("credential-handle", key)
        if (
            not item
            or item["agent_id"] != agent_id
            or item["consumed"]
            or datetime.fromisoformat(item["expires_at"]) <= datetime.now(timezone.utc)
        ):
            raise CredentialUseError(
                "credential handle is invalid, expired, or already consumed"
            )
        credential = self.store.get("credential", item["credential_id"])
        if credential is None:
            raise CredentialUseError("credential not found")
        credential = self._credential_defaults(item["credential_id"], credential)
        item["consumed"] = True
        self.store.put("credential-handle", key, item)
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
        issued = datetime.now(timezone.utc)
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
                    }
                )
        return result

    def revoke_agent(self, agent_id: str) -> None:
        for row in self.store.db.execute(
            "select key,value from kv where namespace='grant'"
        ).fetchall():
            d = self.store._open(row["value"])
            if d["agent_id"] == agent_id:
                d["revoked"] = True
                self.store.put("grant", row["key"], d)
        item = self.store.get("agent", agent_id)
        if item:
            item["revoked"] = True
            self.store.put("agent", agent_id, item)

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

    def list_sources(self):
        return list(self.sources.values())

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

    def list_profile_fields(self):
        result = []
        for row in self.store.db.execute(
            "select key from kv where namespace='profile'"
        ):
            item = self.store.get("profile", row["key"])
            if not item:
                continue
            item = self._profile_defaults(row["key"], item)
            result.append(
                {
                    key: item[key]
                    for key in _PROFILE_METADATA_FIELDS
                    if key in item
                }
            )
        return result

    def request_profile_fields(self, agent_id, field_ids, target_origin, purpose):
        if not target_origin or not purpose:
            raise BrokerError("target_origin and purpose are required")
        target = _normalize_origin(target_origin, "target_origin")
        purpose = _normal_text(purpose, "purpose")
        requested = self._requested_fields(
            field_ids, "field_ids", identifiers=True
        )
        out = {}
        for fid in requested:
            self._authorized(agent_id, "profile.read", fid, target, purpose)
            item = self.store.get("profile", fid)
            if item is None:
                raise BrokerError(f"unknown profile field: {fid}")
            item = self._profile_defaults(fid, item)
            _record_scope_allowed(
                item["allowed_origins"], item["allowed_purposes"], target, purpose
            )
            out[fid] = {
                "value": item["value"],
                "provenance": self._read_provenance(
                    item, "local-profile", fid, target, purpose, "field_id"
                ),
            }
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

    def request_auth_code(
        self, agent_id, account_id, target_origin, purpose, max_age_seconds=300
    ):
        if not target_origin or not purpose:
            raise BrokerError("target_origin and purpose are required")
        try:
            max_age_seconds = int(max_age_seconds)
        except (TypeError, ValueError):
            raise BrokerError("max_age_seconds must be an integer")
        if max_age_seconds < 1 or max_age_seconds > 3600:
            raise BrokerError("max_age_seconds must be between 1 and 3600 seconds")
        self._authorized(agent_id, "email.otp", account_id, target_origin, purpose)
        self.store.purge_legacy_otp_markers()
        adapter = self._ensure_email_source(account_id)
        now = datetime.now(timezone.utc)
        candidates = []
        messages = (
            adapter.recent(max_age_seconds)
            if adapter is not None and hasattr(adapter, "recent")
            else self.email.get(account_id, [])
        )
        for m in messages:
            received_at = datetime.fromisoformat(m.received_at)
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=timezone.utc)
            age = (now - received_at).total_seconds()
            if 0 <= age <= max_age_seconds:
                codes = re.findall(r"\b\d{4,8}\b", m.body)
                candidates.extend((m, c) for c in codes)
        parsed_origin = urlparse(
            target_origin if "://" in target_origin else "//" + target_origin
        )
        domain = (parsed_origin.hostname or "").lower().rstrip(".")
        if len(candidates) != 1:
            raise BrokerError(
                "no unambiguous recent authentication code associated with target origin"
            )
        bound = [
            item
            for item in candidates
            if _hostname_token_matches(
                item[0].sender + " " + item[0].subject + " " + item[0].body,
                domain,
            )
        ]
        if len(bound) != 1:
            raise BrokerError(
                "recent authentication code is not bound to target origin"
            )
        candidates = bound
        m, code = candidates[0]
        used_key = hashlib.sha256(
            f"{agent_id}\0{account_id}\0{m.message_id}".encode()
        ).hexdigest()
        if not self.store.put_if_absent(
            "otp-consumed", used_key, {"used_at": now_iso()}
        ):
            raise BrokerError("authentication code already consumed")
        self.store.audit(
            agent_id,
            "email.otp",
            account_id,
            now_iso(),
            {
                "message_id": m.message_id,
                "target_origin": target_origin,
                "purpose": purpose,
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

    def search_messages(self, agent_id, conversation_scope, query, limit=20):
        limit = _bounded_limit(limit)
        self._authorized(agent_id, "messages.search", conversation_scope)
        q = query.lower()
        rows = []
        for source_id, adapter in self.message_adapters.items():
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
