"""Metadata-only form matching helpers for acting agents.

The browser remains outside Agent Autofill.  This module only turns visible
form metadata into safe matching guidance; it never reads or returns values.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

_REQUIRED_MARKERS = re.compile(r"(?:\s*[✱*†‡]+)+\s*$")
_SPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^a-z0-9]+")

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
    "street": ("street", "street address", "address line 1", "address"),
    "city": ("city", "town", "locality"),
    "state": ("state", "province", "region"),
    "postcode": ("postal code", "zip", "zip code", "postcode"),
    "country": (
        "country",
        "country or region",
        "country territory",
        "country region",
    ),
}

_CHROME_FIELD_SEMANTICS = {
    "name": "full_name",
    "first_name": "first_name",
    "last_name": "last_name",
    "email": "email",
    "phone": "phone",
    "street": "street",
    "street_line1": "street",
    "city": "city",
    "state": "state",
    "postcode": "postcode",
    "country": "country",
}
_ADDRESS_GROUP_LABELS = {"delivery address", "shipping address", "billing address"}
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
        normalized = normalize_form_label(field.get(key, ""))
        for semantic, aliases in _ALIASES.items():
            if normalized in {normalize_form_label(alias) for alias in aliases}:
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
    return any(normalized == term or term in normalized for term in terms)


def _locator_hints(field: dict[str, Any]) -> dict[str, str]:
    """Keep only stable, value-free attributes useful for browser targeting."""

    hints: dict[str, str] = {}
    for key in ("name", "autocomplete", "type", "role"):
        value = field.get(key)
        if isinstance(value, str) and value.strip() and len(value) <= 256:
            hints[key] = value.strip()
    return hints


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
    return {
        "candidate_rows": candidate_rows or [],
        "confidence": confidence,
        "llm_guidance": guidance,
        "question": guidance["question"],
    }


def match_form_fields(
    fields: list[dict[str, Any]],
    profile_fields: list[dict[str, Any]],
    preferences: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return value-free mapping guidance for visible form fields."""

    result: list[dict[str, Any]] = []
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
        preferred_group_id = preference.get("group_id")
        def score_field(
            field: dict[str, Any],
            normalized=normalized,
            semantic=semantic,
            composite_semantics=composite_semantics,
            preferred_field_ids=preferred_field_ids,
            preferred_group_id=preferred_group_id,
        ) -> int:
            score = _candidate_score(field, normalized, semantic)
            if composite_semantics:
                if not (_field_semantics(field) & composite_semantics):
                    return 0
                score = max(score, 100)
            if score <= 0:
                return 0
            if field.get("field_id") in preferred_field_ids or (
                isinstance(preferred_group_id, str)
                and profile_group_id(field) == preferred_group_id
            ):
                score += 1000
            return score

        scored = sorted(
            ((score_field(field), field) for field in profile_fields),
            key=lambda item: item[0],
            reverse=True,
        )
        scored = [item for item in scored if item[0] > 0]
        if not scored:
            status = "missing"
            reason = "no compatible saved profile field"
        elif normalized in _ADDRESS_GROUP_LABELS:
            status = "ambiguous"
            reason = (
                "whole-address fields need agent judgment: choose a complete saved "
                "address group or ask for missing components; one matching component "
                "does not establish a complete address"
            )
        elif len(scored) > 1 and scored[0][0] == scored[1][0]:
            status = "ambiguous"
            chrome_groups = {
                (
                    field["provenance"].get("source_profile_id"),
                    field["provenance"].get("source_guid")
                    or field["provenance"].get("source_guid_hash"),
                )
                for _, field in scored
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
        candidate_rows = []
        for rank, (score, candidate) in enumerate(scored[:5], start=1):
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
            row.update(
                {
                    "group_id": profile_group_id(candidate),
                    "rank": rank,
                    "score": score,
                }
            )
            candidate_rows.append(row)
        result.append(
            base
            | {
                "status": status,
                "semantic": semantic,
                "candidates": candidate_rows,
                **_decision_fields(
                    status,
                    label,
                    score=scored[0][0] if scored else None,
                    learned=bool(preference and status == "matched"),
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
                    if preference and status == "matched"
                    else {}
                ),
            }
        )
    return result
