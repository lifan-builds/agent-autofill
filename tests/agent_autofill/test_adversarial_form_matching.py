# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Agent Autofill; Section 7 attribution terms in ATTRIBUTION.md.
"""Deterministic adversarial regression suite for HTML form DOM field matching."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from agent_autofill.service import Broker
from agent_autofill.storage import Store

_DOM_SPEC = importlib.util.spec_from_file_location(
    "dom_extractor", Path(__file__).parent / "dom_extractor.py"
)
assert _DOM_SPEC is not None and _DOM_SPEC.loader is not None
_DOM_MOD = importlib.util.module_from_spec(_DOM_SPEC)
_DOM_SPEC.loader.exec_module(_DOM_MOD)
extract_form_fields_from_html = _DOM_MOD.extract_form_fields_from_html

_CORPUS_PATH = Path(__file__).parent / "fixtures" / "adversarial_form_dom_corpus.json"
_CORPUS = json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))

_GROUP_FIXTURES = {
    "home": {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "full_name": "Ada Lovelace",
        "email": "ada.home@example.test",
        "phone": "415-555-0199",
        "company": "Analytical Engines Ltd",
        "street": "100 Synthetic Maple Way",
        "city": "Mountain View",
        "state": "CA",
        "postcode": "94041",
        "country": "US",
    },
    "work": {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "full_name": "Ada Lovelace",
        "email": "ada.work@example.test",
        "phone": "650-555-0144",
        "company": "Babbage Labs",
        "street": "500 Synthetic Commerce Blvd",
        "city": "San Francisco",
        "state": "CA",
        "postcode": "94107",
        "country": "US",
    },
}

_FIELD_DISPLAY = {
    "first_name": ("identity", "First name"),
    "last_name": ("identity", "Last name"),
    "full_name": ("identity", "Full name"),
    "email": ("contact", "Email address"),
    "phone": ("contact", "Phone number"),
    "company": ("identity", "Company"),
    "street": ("address", "Street address"),
    "city": ("address", "City"),
    "state": ("address", "State"),
    "postcode": ("address", "Postal code"),
    "country": ("address", "Country"),
}


def _populate_group(broker: Broker, group_key: str, *, is_primary: bool = False) -> str:
    values = _GROUP_FIXTURES[group_key]
    for ftype, value in values.items():
        category, display = _FIELD_DISPLAY[ftype]
        prov: dict[str, object] = {
            "source": "chrome-structured-address",
            "source_profile_id": "synthetic-chrome",
            "source_guid_hash": group_key,
            "field_type": ftype,
            "group_label": f"{group_key.title()} address",
        }
        if is_primary:
            prov["is_primary"] = True
        broker.put_profile(
            f"{group_key}_{ftype}",
            category,
            display,
            value,
            provenance=prov,
        )
    groups = broker.list_profile_groups()
    for group in groups:
        if f"{group_key}_street" in group["field_ids"]:
            return group["group_id"]
    raise AssertionError(f"group not found for {group_key}")


def _setup_broker(profile_setup: str) -> tuple[Broker, list[str]]:
    broker = Broker(Store(":memory:", key=b"f" * 32))
    secrets_to_check: list[str] = []
    if profile_setup == "empty":
        return broker, secrets_to_check
    if profile_setup == "single_home":
        _populate_group(broker, "home")
        secrets_to_check.extend(_GROUP_FIXTURES["home"].values())
    elif profile_setup == "two_addresses_home_and_work":
        _populate_group(broker, "home")
        _populate_group(broker, "work")
        secrets_to_check.extend(_GROUP_FIXTURES["home"].values())
        secrets_to_check.extend(_GROUP_FIXTURES["work"].values())
    elif profile_setup == "two_addresses_with_primary_home":
        _populate_group(broker, "home", is_primary=True)
        _populate_group(broker, "work", is_primary=False)
        secrets_to_check.extend(_GROUP_FIXTURES["home"].values())
        secrets_to_check.extend(_GROUP_FIXTURES["work"].values())
    else:
        raise ValueError(f"unknown profile_setup: {profile_setup}")
    return broker, secrets_to_check


@pytest.mark.parametrize(
    "scenario",
    _CORPUS["scenarios"],
    ids=[item["id"] for item in _CORPUS["scenarios"]],
)
def test_adversarial_form_dom_matching_corpus(scenario: dict):
    broker, secret_values = _setup_broker(scenario["profile_setup"])
    extracted = extract_form_fields_from_html(scenario["html"])
    expected = scenario["expected_fields"]
    assert [f["field_id"] for f in extracted] == [e["field_id"] for e in expected], (
        f"{scenario['id']}: extracted HTML controls did not match fixture expectation"
    )

    matched = broker.match_form_fields(
        extracted,
        target_origin=scenario["target_origin"],
        purpose=scenario["purpose"],
    )
    serialized = json.dumps(matched)
    for secret in secret_values:
        if len(secret) > 2:
            assert secret not in serialized, f"leaked profile value in {scenario['id']}"

    for row, exp in zip(matched, expected, strict=True):
        fid = exp["field_id"]
        assert row["field_id"] == fid
        assert row["status"] == exp["expected_status"], (
            f"{scenario['id']}.{fid} ({row['label']!r}): "
            f"expected status={exp['expected_status']!r}, got {row['status']!r} "
            f"(reason={row.get('reason')!r})"
        )
        if exp["expected_semantic"] is not None:
            assert row.get("semantic") == exp["expected_semantic"], (
                f"{scenario['id']}.{fid}: expected semantic={exp['expected_semantic']!r}, "
                f"got {row.get('semantic')!r}"
            )
        if exp["expected_section"] is not None:
            assert row.get("section") == exp["expected_section"], (
                f"{scenario['id']}.{fid}: expected section={exp['expected_section']!r}, "
                f"got {row.get('section')!r}"
            )
        if exp["expected_status"] == "financial":
            assert row["candidates"] == []
            assert row["llm_guidance"]["action"] == "route_to_dedicated_workflow"


def test_shipping_and_billing_learned_preferences_stay_isolated():
    broker = Broker(Store(":memory:", key=b"b" * 32))
    broker.create_agent("service:local")
    broker.enable_trusted_local("service:local")
    home_gid = _populate_group(broker, "home")
    work_gid = _populate_group(broker, "work")

    # Learn a shipping preference for home and a billing preference for work.
    broker.record_form_mapping_feedback(
        "service:local",
        target_origin="https://store.example.test",
        purpose="checkout",
        field_label="Shipping Street Address",
        selected_field_ids=["home_street"],
        group_id=home_gid,
        confirmed=True,
    )
    broker.record_form_mapping_feedback(
        "service:local",
        target_origin="https://store.example.test",
        purpose="checkout",
        field_label="Billing Street Address",
        selected_field_ids=["work_street"],
        group_id=work_gid,
        confirmed=True,
    )

    fields = extract_form_fields_from_html(
        "<form>"
        "<fieldset><legend>Shipping Address</legend>"
        "<label for='s1'>Shipping Street Address</label><input id='s1'>"
        "<label for='s2'>Shipping City</label><input id='s2'>"
        "<label for='s3'>Shipping ZIP</label><input id='s3'>"
        "</fieldset>"
        "<fieldset><legend>Billing Address</legend>"
        "<label for='b1'>Billing Street Address</label><input id='b1'>"
        "<label for='b2'>Billing City</label><input id='b2'>"
        "<label for='b3'>Billing ZIP</label><input id='b3'>"
        "</fieldset>"
        "</form>"
    )
    rows = broker.match_form_fields(
        fields,
        target_origin="https://store.example.test",
        purpose="checkout",
    )
    by_id = {row["field_id"]: row for row in rows}
    for sid in ("s1", "s2", "s3"):
        assert by_id[sid]["status"] == "matched"
        assert by_id[sid]["section"] == "shipping"
        assert by_id[sid]["group_id"] == home_gid
    for bid in ("b1", "b2", "b3"):
        assert by_id[bid]["status"] == "matched"
        assert by_id[bid]["section"] == "billing"
        assert by_id[bid]["group_id"] == work_gid
