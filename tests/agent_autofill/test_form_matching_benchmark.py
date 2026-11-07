import json
from pathlib import Path

from agent_autofill.form_memory import match_form_fields


def test_form_matching_benchmark_keeps_classification_and_top1_metrics_stable():
    path = Path(__file__).parents[2] / "evals" / "form_filling" / "matching-benchmark.json"
    cases = json.loads(path.read_text())
    results = []
    for case in cases:
        result = match_form_fields(
            [{"field_id": case["case_id"], "label": case["label"]}],
            case["profile_fields"],
        )[0]
        assert result["status"] == case["expected_status"], case["case_id"]
        if "expected_top_field_id" in case:
            assert result["candidates"][0]["field_id"] == case["expected_top_field_id"]
        assert "value" not in result
        results.append(result)

    classification_accuracy = sum(
        result["status"] == case["expected_status"]
        for result, case in zip(results, cases)
    ) / len(cases)
    top1_cases = [case for case in cases if "expected_top_field_id" in case]
    top1_accuracy = sum(
        result["candidates"][0]["field_id"] == case["expected_top_field_id"]
        for result, case in zip(results, cases)
        if "expected_top_field_id" in case
    ) / len(top1_cases)
    assert classification_accuracy == 1.0
    assert top1_accuracy == 1.0
