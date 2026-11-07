"""Regression checks for the delivery-form example in the product tour."""

import pytest

from agent_autofill.service import Broker
from agent_autofill.storage import Store


@pytest.mark.parametrize("label", ["Delivery address", "Shipping address", "Billing address"])
def test_address_prompt_reuses_candidates_without_choosing_a_destination(label):
    broker = Broker(Store(":memory:", key=b"v" * 32))
    assert broker.match_form_fields([{"label": label}])[0]["status"] == "missing"
    for group in ("home", "work"):
        broker.put_profile(
            f"{group}_street", "address", f"{group.title()} street",
            f"Synthetic {group} street",
            provenance={
                "source": "chrome-structured-address",
                "source_profile_id": "synthetic-profile",
                "source_guid_hash": group,
                "field_type": "street",
            },
        )
    result = broker.match_form_fields([{"label": label}])[0]
    assert result["status"] == "ambiguous"
    assert result["llm_guidance"]["action"] == "choose_best_candidate_or_ask"
    assert {item["field_id"] for item in result["candidates"]} == {
        "home_street", "work_street",
    }
    assert len({item["group_id"] for item in result["candidates"]}) == 2
    assert "Synthetic home street" not in str(result)
    assert "Synthetic work street" not in str(result)
    assert broker.match_form_fields([
        {"label": label, "task_specific": True},
    ])[0]["status"] == "task_specific"


@pytest.mark.parametrize("label", ["Delivery address", "Shipping address", "Billing address"])
@pytest.mark.parametrize("components", [
    ("country",), ("postcode",), ("street",), ("street", "city", "postcode"),
])
def test_whole_address_requires_completeness_judgment_even_with_one_group(label, components):
    broker = Broker(Store(":memory:", key=b"w" * 32))
    for component in components:
        broker.put_profile(
            f"home_{component}", "address", f"Home {component}",
            f"Synthetic {component}",
            provenance={
                "source": "chrome-structured-address",
                "source_profile_id": "synthetic-profile",
                "source_guid_hash": "home",
                "field_type": component,
            },
        )
    result = broker.match_form_fields([{"label": label}])[0]
    assert result["decision"] == "agent_judgment"
    assert result["llm_guidance"]["action"] == "choose_best_candidate_or_ask"
    assert result["confidence"] == "low"
    assert "complete address" in result["reason"]
    assert {item["field_id"] for item in result["candidates"]} == {
        f"home_{component}" for component in components
    }
    assert len({item["group_id"] for item in result["candidates"]}) == 1
    assert "Synthetic" not in str(result)
