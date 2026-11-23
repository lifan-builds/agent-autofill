# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Agent Autofill; Section 7 attribution terms in ATTRIBUTION.md.
"""Metadata-only form matching helpers for acting agents.

The browser remains outside Agent Autofill.  This module only turns visible
form metadata into safe matching guidance; it never reads or returns values.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any
from urllib.parse import urlparse

_REQUIRED_MARKERS = re.compile(r"(?:\s*[✱*†‡]+)+\s*$")
_SPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^a-z0-9]+")
_MAX_CANDIDATE_ROWS = 25

_ALIASES: dict[str, tuple[str, ...]] = {
    "email": (
        "email",
        "email address",
        "work email",
        "business email",
        "company email",
    ),
    "first_name": ("first name", "given name", "forename"),
    "last_name": ("last name", "family name", "surname"),
    "full_name": ("full name", "name", "your name"),
    "phone": ("phone", "phone number", "mobile", "mobile number", "telephone"),
    "company": ("company", "company name", "organization", "organisation"),
    "street": (
        "street",
        "street address",
        "address line 1",
        "address 1",
        "street address line 1",
        "address",
    ),
    "city": ("city", "town", "locality"),
    "state": (
        "state",
        "province",
        "region",
        "state province",
        "state or province",
        "state province region",
    ),
    "postcode": (
        "postal code",
        "zip",
        "zip code",
        "postcode",
        "zip postal code",
        "zip or postal code",
    ),
    "country": (
        "country",
        "country or region",
        "country territory",
        "country region",
    ),
}

_CHROME_FIELD_SEMANTICS = {
    "name": "full_name",
    "full_name": "full_name",
    "first_name": "first_name",
    "givenName": "first_name",
    "last_name": "last_name",
    "familyName": "last_name",
    "email": "email",
    "phone": "phone",
    "street": "street",
    "street_line1": "street",
    "street_address": "street",
    "streetAddress": "street",
    "city": "city",
    "addressLevel2": "city",
    "state": "state",
    "addressLevel1": "state",
    "postcode": "postcode",
    "postal_code": "postcode",
    "postalCode": "postcode",
    "country": "country",
    "countryCode": "country",
}
_ADDRESS_COMPONENT_SEMANTICS = {"street", "city", "state", "postcode", "country"}
_ADDRESS_GROUP_LABELS = {"delivery address", "shipping address", "billing address"}

_COUNTRY_NAME_TO_ISO2 = {
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "u s": "US",
    "u s a": "US",
    "canada": "CA",
    "can": "CA",
    "united kingdom": "GB",
    "great britain": "GB",
    "england": "GB",
    "uk": "GB",
    "gbr": "GB",
    "australia": "AU",
    "aus": "AU",
    "china": "CN",
    "chn": "CN",
    "japan": "JP",
    "jpn": "JP",
    "germany": "DE",
    "deu": "DE",
    "france": "FR",
    "fra": "FR",
    "mexico": "MX",
    "mex": "MX",
    "india": "IN",
    "ind": "IN",
    "singapore": "SG",
    "sgp": "SG",
    "south korea": "KR",
    "korea": "KR",
    "kor": "KR",
    "taiwan": "TW",
    "twn": "TW",
    "hong kong": "HK",
    "hkg": "HK",
    "italy": "IT",
    "ita": "IT",
    "spain": "ES",
    "esp": "ES",
    "netherlands": "NL",
    "nld": "NL",
    "switzerland": "CH",
    "che": "CH",
    "brazil": "BR",
    "bra": "BR",
    "new zealand": "NZ",
    "nzl": "NZ",
    "ireland": "IE",
    "irl": "IE",
}

_STATE_NAME_TO_CODE = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "puerto rico": "PR",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
    "alberta": "AB",
    "british columbia": "BC",
    "manitoba": "MB",
    "new brunswick": "NB",
    "newfoundland and labrador": "NL",
    "nova scotia": "NS",
    "ontario": "ON",
    "prince edward island": "PE",
    "quebec": "QC",
    "saskatchewan": "SK",
    "new south wales": "NSW",
    "victoria": "VIC",
    "queensland": "QLD",
}
_THREE_LETTER_STATE_CODES = {"NSW", "VIC", "QLD", "ACT", "TAS"}
_COMPOSITE_SEMANTICS = {
    "phone number or email": {"phone", "email"},
    "email or phone": {"phone", "email"},
    "legal name": {"full_name", "first_name", "last_name"},
    "current location": {"street", "city", "state", "postcode", "country"},
    "delivery address": {"street", "city", "state", "postcode", "country"},
    "shipping address": {"street", "city", "state", "postcode", "country"},
    "billing address": {"street", "city", "state", "postcode", "country"},
}

_TRANSIENT_TERMS = {
    "captcha",
    "challenge",
    "one time password",
    "one time code",
    "otp",
    "security code",
    "verification code",
    "confirmation code",
}
_CREDENTIAL_TERMS = {
    "password",
    "passphrase",
    "username",
    "user name",
    "login",
}
_TASK_SPECIFIC_TERMS = {
    "account number",
    "declared value",
    "destination",
    "shipment",
    "ship date",
    "package",
    "quantity",
    "comments",
    "message",
    "job description",
    "cover letter",
    "account",
    "origin",
    "usage",
    "job level",
    "job function",
    "mailing from",
    "mailing to",
    "mailing date",
    "mailing time",
    "date you plan to mail",
    "time you plan to mail",
    "shipping date",
    "shipping time",
    "mailpiece",
    "ground transportation",
    "what are you sending",
    "what zip code are you mailing",
    "package type",
}
_CONSENT_TERMS = {
    "consent",
    "marketing",
    "opt in",
    "terms of service",
    "agree and continue",
    "privacy policy",
}


def normalize_form_label(label: Any) -> str:
    """Normalize visible labels, including required-marker glyphs."""

    if not isinstance(label, str):
        return ""
    text = unicodedata.normalize("NFKC", label).casefold().strip()
    text = _REQUIRED_MARKERS.sub("", text)
    text = text.replace("/", " ").replace("&", " and ")
    text = _PUNCTUATION.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


def _semantic_label(label: str) -> str | None:
    normalized = normalize_form_label(label)
    for semantic, aliases in _ALIASES.items():
        if normalized in {normalize_form_label(alias) for alias in aliases}:
            return semantic
    return None


def _field_semantics(field: dict[str, Any]) -> set[str]:
    """Return semantic types from profile metadata, including Chrome provenance."""

    semantics: set[str] = set()
    provenance = field.get("provenance")
    if isinstance(provenance, dict):
        field_type = provenance.get("field_type")
        if isinstance(field_type, str):
            semantic = _CHROME_FIELD_SEMANTICS.get(field_type)
            if semantic:
                semantics.add(semantic)
    for key in ("field_id", "display_name"):
        semantic = _semantic_label(field.get(key, ""))
        if semantic:
            semantics.add(semantic)
    return semantics


def profile_group_id(field: dict[str, Any]) -> str:
    """Return the stable metadata-only group id used by the broker."""

    provenance = field.get("provenance")
    if not isinstance(provenance, dict):
        provenance = {}
    profile_id = provenance.get("source_profile_id")
    source_key = provenance.get("source_guid") or provenance.get("source_guid_hash")
    if isinstance(profile_id, str) and isinstance(source_key, str):
        grouping_key = f"{profile_id}\0{source_key}"
    else:
        grouping_key = f"local\0{field.get('field_id', '')}"
    digest = hashlib.sha256(grouping_key.encode("utf-8")).hexdigest()[:24]
    return "profile_group_" + "_".join(
        digest[index : index + 4] for index in range(0, 24, 4)
    )


def _contains_term(label: str, terms: set[str]) -> bool:
    normalized = normalize_form_label(label)
    return any(term in normalized for term in terms)


def _locator_hints(field: dict[str, Any]) -> dict[str, str]:
    """Keep only stable, value-free attributes useful for browser targeting."""

    hints: dict[str, str] = {}
    for key in ("name", "autocomplete", "type", "role"):
        value = field.get(key)
        if isinstance(value, str) and value.strip() and len(value) <= 256:
            hints[key] = value.strip()
    return hints


def safe_country_code(raw: Any) -> str | None:
    """Return a bounded ISO-3166 alpha-2 country code without echoing arbitrary strings."""

    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    if len(cleaned) == 2 and cleaned.isascii() and cleaned.isalpha():
        return cleaned.upper()
    normalized = normalize_form_label(cleaned)
    return _COUNTRY_NAME_TO_ISO2.get(normalized)


def safe_state_code(raw: Any) -> str | None:
    """Return a bounded 2-3 letter state/province code without echoing arbitrary text."""

    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    if len(cleaned) == 2 and cleaned.isascii() and cleaned.isalpha():
        return cleaned.upper()
    if len(cleaned) == 3 and cleaned.isascii() and cleaned.isalpha():
        upper = cleaned.upper()
        if upper in _THREE_LETTER_STATE_CODES:
            return upper
    normalized = normalize_form_label(cleaned)
    return _STATE_NAME_TO_CODE.get(normalized)


def masked_recipient_initials(
    *,
    first_name: Any = None,
    last_name: Any = None,
    full_name: Any = None,
) -> str | None:
    """Return masked single-letter initials (e.g. 'A. L.') without exposing raw names."""

    def _clean_tokens(raw: Any) -> list[str]:
        if not isinstance(raw, str) or not raw.strip():
            return []
        tokens = [
            token
            for token in re.split(r"[\s,._-]+", raw.strip())
            if token and any(ch.isalpha() for ch in token)
        ]
        if any(token.casefold() == "synthetic" for token in tokens):
            return []
        return tokens

    first_tokens = _clean_tokens(first_name)
    last_tokens = _clean_tokens(last_name)
    if not first_tokens and not last_tokens:
        full_tokens = _clean_tokens(full_name)
        if len(full_tokens) >= 2:
            first_tokens = [full_tokens[0]]
            last_tokens = [full_tokens[-1]]
        elif len(full_tokens) == 1 and len(full_tokens[0]) >= 2:
            first_tokens = [full_tokens[0]]

    def _initial(token: str) -> str | None:
        for ch in token:
            if ch.isascii() and ch.isalpha():
                return f"{ch.upper()}."
        return None

    parts: list[str] = []
    if first_tokens and (len(first_tokens[0]) >= 2 or last_tokens):
        init = _initial(first_tokens[0])
        if init:
            parts.append(init)
    if last_tokens and (len(last_tokens[-1]) >= 2 or parts):
        init = _initial(last_tokens[-1])
        if init:
            parts.append(init)
    return " ".join(parts) if parts else None


def build_group_summary(
    *,
    group_label: str | None = None,
    is_primary: bool = False,
    matches_self_profile: bool = False,
    confirmed_meaning: dict[str, Any] | None = None,
    recipient_initials: str | None = None,
    state: str | None = None,
    country: str | None = None,
) -> str | None:
    """Build a bounded value-free disambiguation string for multi-group selection."""

    parts: list[str] = []
    if is_primary:
        parts.append("Primary")
    elif matches_self_profile:
        parts.append("Self match")
    label = (group_label or "").strip()
    if not label and isinstance(confirmed_meaning, dict):
        title = str(confirmed_meaning.get("title") or "").strip()
        role = str(confirmed_meaning.get("role") or "").strip()
        if title:
            label = title
        elif role and role != "unknown":
            label = role.title()
    if label:
        parts.append(label)
    if recipient_initials:
        parts.append(recipient_initials)
    region = ", ".join(item for item in (state, country) if item)
    if region:
        parts.append(region)
    return " · ".join(parts) if parts else None


def _origin_tuple(raw: str) -> tuple[str, str, int | None] | None:
    text = raw.strip()
    if not text:
        return None
    parsed = urlparse(text if "://" in text else "//" + text)
    try:
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        return None
    scheme = parsed.scheme.lower()
    if (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
        port = None
    return (scheme, host, port)


def _origins_match_for_form(allowed: str, target_origin: str) -> bool:
    if allowed.strip() == "*":
        return True
    left = _origin_tuple(allowed)
    right = _origin_tuple(target_origin)
    if left is None or right is None:
        return False
    return left == right or ((not left[0] or not right[0]) and left[1:] == right[1:])


def _scope_eligibility(
    field: dict[str, Any],
    target_origin: str | None,
    purpose: str | None,
) -> tuple[bool, int, bool]:
    """Return (eligible, specificity_bonus, scope_unverified) for one profile field."""

    raw_origins = field.get("allowed_origins") or []
    allowed_origins = [
        item for item in raw_origins if isinstance(item, str) and item.strip() and item.strip() != "*"
    ]
    raw_purposes = field.get("allowed_purposes") or []
    allowed_purposes = [
        item.strip()
        for item in raw_purposes
        if isinstance(item, str) and item.strip() and item.strip() != "*"
    ]

    bonus = 0
    scope_unverified = False
    if allowed_origins:
        if target_origin is None:
            scope_unverified = True
            bonus -= 30
        elif any(_origins_match_for_form(item, target_origin) for item in allowed_origins):
            bonus += 30
        else:
            return (False, 0, False)

    if allowed_purposes:
        if purpose is None:
            scope_unverified = True
            bonus -= 15
        elif purpose.strip().casefold() in {item.casefold() for item in allowed_purposes}:
            bonus += 15
        else:
            return (False, 0, False)

    return (True, bonus, scope_unverified)


def _is_chrome_structured_field(field: dict[str, Any]) -> bool:
    provenance = field.get("provenance")
    return (
        isinstance(provenance, dict)
        and provenance.get("source") == "chrome-structured-address"
    )


def _candidate_score(field: dict[str, Any], normalized: str, semantic: str | None) -> int:
    field_id = normalize_form_label(field.get("field_id", ""))
    display = normalize_form_label(field.get("display_name", ""))
    category = normalize_form_label(field.get("category", ""))
    field_semantics = _field_semantics(field)
    score = 0
    if semantic and semantic in field_semantics:
        score += 100
    if normalized and normalized == display:
        score += 80
    if normalized and normalized == field_id:
        score += 70
    if semantic and semantic in {field_id, display, category}:
        score += 40
    if normalized and (normalized in display or display in normalized):
        score += 20
    return score


def _decision_fields(
    status: str,
    label: str,
    *,
    score: int | None = None,
    learned: bool = False,
    candidate_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the model-facing decision shape without exposing values."""

    if status == "ambiguous":
        confidence = "low"
        action = "choose_best_candidate_or_ask"
        question = f'Which saved record should I use for "{label}"?'
        learning = "record_after_confirmed_choice_or_correction"
    elif status == "missing":
        confidence = "none"
        action = "ask_for_missing_value"
        question = f'What should I use for "{label}"? You can save it for next time.'
        learning = "offer_save_after_user_answer"
    elif status == "matched":
        confidence = "high" if (score or 0) >= 70 else "medium" if (score or 0) >= 20 else "low"
        action = "use_top_candidate"
        question = None
        learning = (
            "already_learned" if learned else "record_after_confirmed_choice_or_correction"
        )
    else:
        confidence = "none"
        action = "route_to_dedicated_workflow"
        question = None
        learning = "not_applicable"
    guidance = {
        "strategy": "ranked_record_table",
        "action": action,
        "confidence": confidence,
        "question": question,
        "learning": learning,
        "memory_choices": ["use_once", "remember_for_site_and_purpose", "replace_saved_value"]
        if status in {"matched", "ambiguous", "missing"} else [],
        "reuse_notice": "Using your remembered choice for this site and purpose; you can change it."
        if learned else None,
    }
    out: dict[str, Any] = {
        "candidate_rows": candidate_rows or [],
        "confidence": confidence,
        "llm_guidance": guidance,
        "question": guidance["question"],
    }
    if status == "matched" and candidate_rows:
        out["group_id"] = candidate_rows[0]["group_id"]
        out["matched_field_ids"] = [candidate_rows[0]["field_id"]]
    return out


def _enrich_candidate_row(
    candidate: dict[str, Any],
    *,
    rank: int,
    score: int,
    group_metadata: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    gid = profile_group_id(candidate)
    row: dict[str, Any] = {
        key: candidate[key]
        for key in (
            "field_id",
            "category",
            "display_name",
            "sensitivity",
            "allowed_origins",
            "allowed_purposes",
        )
        if key in candidate
    }
    row["group_id"] = gid
    info = (group_metadata or {}).get(gid)
    if isinstance(info, dict):
        if info.get("group_label"):
            row["group_label"] = info["group_label"]
        if "is_primary" in info:
            row["is_primary"] = bool(info["is_primary"])
        if info.get("matches_self_profile") is True:
            row["matches_self_profile"] = True
        if info.get("recipient_initials"):
            row["recipient_initials"] = info["recipient_initials"]
        if info.get("state"):
            row["state"] = info["state"]
        if info.get("country"):
            row["country"] = info["country"]
        if info.get("summary"):
            row["group_summary"] = info["summary"]
        if isinstance(info.get("confirmed_meaning"), dict):
            row["confirmed_meaning"] = dict(info["confirmed_meaning"])
    else:
        provenance = candidate.get("provenance")
        if isinstance(provenance, dict):
            group_label = provenance.get("group_label") or provenance.get("label") or provenance.get("hint")
            if isinstance(group_label, str) and group_label.strip():
                row["group_label"] = group_label.strip()
            if provenance.get("is_primary") is True:
                row["is_primary"] = True
    raw_origins = candidate.get("allowed_origins") or []
    if any(isinstance(o, str) and o.strip() and o.strip() != "*" for o in raw_origins):
        row["origin_restricted"] = True
    row["rank"] = rank
    row["score"] = score
    return row


def match_form_fields(
    fields: list[dict[str, Any]],
    profile_fields: list[dict[str, Any]],
    preferences: dict[str, dict[str, Any]] | None = None,
    *,
    target_origin: str | None = None,
    purpose: str | None = None,
    group_metadata: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return value-free mapping guidance for visible form fields."""

    eligible_fields: list[tuple[dict[str, Any], int, bool]] = []
    excluded_fields: list[dict[str, Any]] = []
    for field in profile_fields:
        ok, bonus, unverified = _scope_eligibility(field, target_origin, purpose)
        if ok:
            eligible_fields.append((field, bonus, unverified))
        else:
            excluded_fields.append(field)

    form_address_semantics: set[str] = set()
    shared_preferred_address_group: str | None = None
    for raw in fields:
        label = raw.get("label", raw.get("display_name", raw.get("field_id", "")))
        if not isinstance(label, str) or not label.strip():
            continue
        if (
            raw.get("obscured")
            or raw.get("blocked")
            or raw.get("validation_error")
            or raw.get("task_specific") is True
            or raw.get("consent") is True
            or _contains_term(label, _CREDENTIAL_TERMS)
            or _contains_term(label, _CONSENT_TERMS)
            or _contains_term(label, _TRANSIENT_TERMS)
            or _contains_term(label, _TASK_SPECIFIC_TERMS)
        ):
            continue
        sem = _semantic_label(label)
        norm = normalize_form_label(label)
        if sem in _ADDRESS_COMPONENT_SEMANTICS:
            form_address_semantics.add(sem)
        if sem in _ADDRESS_COMPONENT_SEMANTICS or norm in _ADDRESS_GROUP_LABELS:
            pref = (preferences or {}).get(norm, {})
            pref_gid = pref.get("group_id")
            if isinstance(pref_gid, str) and pref_gid:
                shared_preferred_address_group = pref_gid

    multi_component_address_form = len(form_address_semantics) >= 2
    group_semantics: dict[str, set[str]] = {}
    group_is_structured: dict[str, bool] = {}
    for field, _, _ in eligible_fields:
        gid = profile_group_id(field)
        group_semantics.setdefault(gid, set()).update(_field_semantics(field))
        if _is_chrome_structured_field(field):
            group_is_structured[gid] = True

    group_address_coverage: dict[str, int] = {
        gid: len(sems & form_address_semantics)
        for gid, sems in group_semantics.items()
    }
    max_address_coverage = max(group_address_coverage.values(), default=0)

    def _group_is_primary(gid: str, field: dict[str, Any]) -> bool:
        info = (group_metadata or {}).get(gid)
        if isinstance(info, dict) and info.get("is_primary") is True:
            return True
        provenance = field.get("provenance")
        return isinstance(provenance, dict) and provenance.get("is_primary") is True

    def _group_matches_self(gid: str) -> bool:
        info = (group_metadata or {}).get(gid)
        return isinstance(info, dict) and info.get("matches_self_profile") is True

    result: list[dict[str, Any]] = []
    address_row_indices: list[tuple[int, str, bool]] = []

    for raw in fields:
        label = raw.get("label", raw.get("display_name", raw.get("field_id", "")))
        if not isinstance(label, str) or not label.strip():
            raise ValueError("each form field needs a label")
        normalized = normalize_form_label(label)
        semantic = _semantic_label(label)
        base = {
            "field_id": raw.get("field_id"),
            "label": label,
            "normalized_label": normalized,
            "required": bool(raw.get("required", False)),
        }
        locator_hints = _locator_hints(raw)
        if locator_hints:
            base["locator_hints"] = locator_hints
        if raw.get("page_default") is True or raw.get("value_state") == "page_default":
            base["value_state"] = "page_default"
        if raw.get("obscured") or raw.get("blocked"):
            result.append(
                base
                | {
                    "status": "obstructed",
                    "candidates": [],
                    "reason": "page UI is obscured or blocked; do not dismiss or consent automatically",
                }
                | _decision_fields("obstructed", label)
            )
            continue
        if raw.get("validation_error"):
            result.append(
                base
                | {
                    "status": "validation_error",
                    "candidates": [],
                    "reason": "the site rejected the current value; ask the user before transforming or replacing it",
                }
                | _decision_fields("validation_error", label)
            )
            continue
        if _contains_term(label, _CREDENTIAL_TERMS):
            result.append(
                base
                | {
                    "status": "credential",
                    "candidates": [],
                    "reason": "credential fields use the credential workflow and are not profile memory",
                }
                | _decision_fields("credential", label)
            )
            continue
        if _contains_term(label, _CONSENT_TERMS):
            result.append(
                base
                | {
                    "status": "consent",
                    "candidates": [],
                    "reason": "consent and legal controls are decisions, not profile memory",
                }
                | _decision_fields("consent", label)
            )
            continue
        if _contains_term(label, _TRANSIENT_TERMS):
            result.append(
                base
                | {
                    "status": "transient",
                    "candidates": [],
                    "reason": "one-time or challenge values are never saved as profile memory",
                }
                | _decision_fields("transient", label)
            )
            continue
        if raw.get("task_specific") is True:
            result.append(
                base
                | {
                    "status": "task_specific",
                    "candidates": [],
                    "reason": "value belongs to the current task and needs acting-agent input",
                    "classification_basis": "explicit_hint",
                }
                | _decision_fields("task_specific", label)
            )
            continue
        if raw.get("consent") is True:
            result.append(
                base
                | {
                    "status": "consent",
                    "candidates": [],
                    "reason": "consent and legal controls are decisions, not profile memory",
                    "classification_basis": "explicit_hint",
                }
                | _decision_fields("consent", label)
            )
            continue
        if _contains_term(label, _TASK_SPECIFIC_TERMS):
            result.append(
                base
                | {
                    "status": "task_specific",
                    "candidates": [],
                    "reason": "value belongs to the current task and needs acting-agent input",
                }
                | _decision_fields("task_specific", label)
            )
            continue

        composite_semantics = _COMPOSITE_SEMANTICS.get(normalized)
        preference = (preferences or {}).get(normalized, {})
        preferred_field_ids = set(preference.get("field_ids", []))
        preferred_group_id = preference.get("group_id") or (
            shared_preferred_address_group
            if semantic in _ADDRESS_COMPONENT_SEMANTICS
            else None
        )
        has_learned_preference = bool(preference) or bool(
            preferred_group_id and semantic in _ADDRESS_COMPONENT_SEMANTICS
        )

        def score_field(
            field: dict[str, Any],
            scope_bonus: int,
            normalized=normalized,
            semantic=semantic,
            composite_semantics=composite_semantics,
            preferred_field_ids=preferred_field_ids,
            preferred_group_id=preferred_group_id,
        ) -> int:
            gid = profile_group_id(field)
            if (
                multi_component_address_form
                and max_address_coverage >= 2
                and semantic in _ADDRESS_COMPONENT_SEMANTICS
                and not composite_semantics
            ):
                if semantic in _field_semantics(field):
                    coverage = group_address_coverage.get(gid, 1)
                    score = 120 + coverage * 60
                else:
                    score = _candidate_score(field, normalized, semantic)
                    if score <= 0:
                        return 0
            else:
                score = _candidate_score(field, normalized, semantic)
                if composite_semantics:
                    if not (_field_semantics(field) & composite_semantics):
                        return 0
                    score = max(score, 100)
                if score <= 0:
                    return 0

            score = max(1, score + scope_bonus)
            if _group_is_primary(gid, field):
                score += 50
            if field.get("field_id") in preferred_field_ids or (
                isinstance(preferred_group_id, str) and gid == preferred_group_id
            ):
                score += 1000
            return score

        scored_entries: list[tuple[int, dict[str, Any], bool]] = []
        for field, scope_bonus, scope_unverified in eligible_fields:
            computed = score_field(field, scope_bonus)
            if computed > 0:
                scored_entries.append((computed, field, scope_unverified))

        scored_entries.sort(
            key=lambda item: (
                -item[0],
                0 if _group_is_primary(profile_group_id(item[1]), item[1]) else 1,
                0 if _group_matches_self(profile_group_id(item[1])) else 1,
                profile_group_id(item[1]),
                str(item[1].get("field_id", "")),
            )
        )

        if not scored_entries:
            excluded_matches = any(
                _candidate_score(field, normalized, semantic) > 0
                or bool(
                    composite_semantics
                    and (_field_semantics(field) & composite_semantics)
                )
                for field in excluded_fields
            )
            status = "missing"
            reason = (
                "saved profile fields for this label are restricted to other origins or purposes"
                if excluded_matches
                else "no compatible saved profile field"
            )
        elif normalized in _ADDRESS_GROUP_LABELS:
            status = "ambiguous"
            reason = (
                "whole-address fields need agent judgment: choose a complete saved "
                "address group or ask for missing components; one matching component "
                "does not establish a complete address"
            )
        elif scored_entries[0][2]:
            status = "ambiguous"
            reason = (
                "saved profile field is restricted to specific origins or purposes; "
                "provide matching target_origin and purpose before reading values"
            )
        elif len(scored_entries) > 1 and scored_entries[0][0] == scored_entries[1][0]:
            status = "ambiguous"
            chrome_groups = {
                (
                    field["provenance"].get("source_profile_id"),
                    field["provenance"].get("source_guid")
                    or field["provenance"].get("source_guid_hash"),
                )
                for _, field, _ in scored_entries
                if isinstance(field.get("provenance"), dict)
                and field["provenance"].get("source") == "chrome-structured-address"
            }
            if len(chrome_groups) > 1:
                reason = "multiple Chrome profile groups match; acting agent chooses one by meaning before reading values"
            else:
                reason = "multiple saved fields match with the same confidence"
        else:
            status = "matched"
            reason = None

        candidate_rows = [
            _enrich_candidate_row(
                candidate,
                rank=rank,
                score=score,
                group_metadata=group_metadata,
            )
            for rank, (score, candidate, _) in enumerate(
                scored_entries[:_MAX_CANDIDATE_ROWS], start=1
            )
        ]
        row_index = len(result)
        if semantic in _ADDRESS_COMPONENT_SEMANTICS and candidate_rows:
            address_row_indices.append((row_index, label, has_learned_preference))
        result.append(
            base
            | {
                "status": status,
                "semantic": semantic,
                "candidates": candidate_rows,
                **_decision_fields(
                    status,
                    label,
                    score=scored_entries[0][0] if scored_entries else None,
                    learned=bool(has_learned_preference and status == "matched"),
                    candidate_rows=candidate_rows,
                ),
                **({"reason": reason} if reason else {}),
                **(
                    {
                        "decision": "agent_judgment",
                        "submission_confirmation": "required_before_submit",
                        "learning": "record_mapping_feedback_after_user_correction_or_confirmed_submit",
                    }
                    if status == "ambiguous"
                    else {}
                ),
                **(
                    {"selection_basis": "learned_mapping"}
                    if has_learned_preference and status == "matched"
                    else {}
                ),
            }
        )

    if multi_component_address_form and len(address_row_indices) >= 2:
        _enforce_address_group_coherence(
            result,
            address_row_indices=address_row_indices,
            group_address_coverage=group_address_coverage,
            group_is_structured=group_is_structured,
            shared_preferred_group_id=shared_preferred_address_group,
            group_metadata=group_metadata,
        )
    return result


def _enforce_address_group_coherence(
    result: list[dict[str, Any]],
    *,
    address_row_indices: list[tuple[int, str, bool]],
    group_address_coverage: dict[str, int],
    group_is_structured: dict[str, bool],
    shared_preferred_group_id: str | None,
    group_metadata: dict[str, dict[str, Any]] | None,
) -> None:
    """Prevent address components on a multi-field form from splitting across groups."""

    candidate_gids: set[str] = set()
    for idx, _, _ in address_row_indices:
        for cand in result[idx].get("candidates", []):
            gid = cand.get("group_id")
            if isinstance(gid, str):
                candidate_gids.add(gid)

    multi_groups = {
        gid
        for gid in candidate_gids
        if group_address_coverage.get(gid, 0) >= 2 or group_is_structured.get(gid, False)
    }
    primary_groups = {
        gid
        for gid in multi_groups
        if (
            isinstance((group_metadata or {}).get(gid), dict)
            and (group_metadata or {})[gid].get("is_primary") is True
        )
        or any(
            cand.get("group_id") == gid and cand.get("is_primary") is True
            for idx, _, _ in address_row_indices
            for cand in result[idx].get("candidates", [])
        )
    }

    chosen_group_id: str | None = None
    if shared_preferred_group_id and shared_preferred_group_id in candidate_gids:
        chosen_group_id = shared_preferred_group_id
    elif len(primary_groups) == 1:
        chosen_group_id = next(iter(primary_groups))
    elif len(multi_groups) == 1:
        chosen_group_id = next(iter(multi_groups))

    def _demote_to_ambiguous(row: dict[str, Any], label: str, reason: str) -> None:
        cands = row.get("candidates", [])
        row["status"] = "ambiguous"
        row["reason"] = reason
        row.pop("selection_basis", None)
        row.pop("group_id", None)
        row.pop("matched_field_ids", None)
        row.update(
            _decision_fields(
                "ambiguous",
                label,
                score=cands[0]["score"] if cands else None,
                learned=False,
                candidate_rows=cands,
            )
        )
        row["decision"] = "agent_judgment"
        row["submission_confirmation"] = "required_before_submit"
        row["learning"] = (
            "record_mapping_feedback_after_user_correction_or_confirmed_submit"
        )

    if chosen_group_id is not None:
        for idx, label, _ in address_row_indices:
            row = result[idx]
            if row.get("status") != "matched":
                continue
            top_gid = (row.get("candidates") or [{}])[0].get("group_id")
            if top_gid != chosen_group_id:
                _demote_to_ambiguous(
                    row,
                    label,
                    "selected address group does not include this component; avoid mixing address groups",
                )
        return

    if len(multi_groups) >= 2:
        for idx, label, has_pref in address_row_indices:
            row = result[idx]
            if row.get("status") == "matched" and not has_pref:
                _demote_to_ambiguous(
                    row,
                    label,
                    "multiple Chrome profile groups match; acting agent chooses one by meaning before reading values",
                )
