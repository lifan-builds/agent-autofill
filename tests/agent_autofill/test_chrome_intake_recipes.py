import hashlib
import re
import sqlite3
from pathlib import Path

import pytest

from agent_autofill.service import Broker
from agent_autofill.storage import Store


_REFERENCE = (
    Path(__file__).parents[2]
    / ".agents"
    / "skills"
    / "agent-autofill-chrome-intake"
    / "references"
    / "local-intake.md"
)


def _recipe_namespace():
    text = _REFERENCE.read_text(encoding="utf-8")
    blocks = re.findall(r"```python\n(.*?)```", text, flags=re.DOTALL)
    assert len(blocks) == 1
    namespace = {}
    exec(compile(blocks[0], str(_REFERENCE), "exec"), namespace)
    return namespace


def _web_data(
    tmp_path: Path,
    rows: list[tuple[str, object, object]],
    *,
    token_type: str = "integer",
    include_tables: bool = True,
) -> Path:
    profile = tmp_path / "Default"
    profile.mkdir(parents=True)
    path = profile / "Web Data"
    db = sqlite3.connect(path)
    if include_tables:
        db.execute("create table addresses (guid text primary key)")
        db.execute(
            f"create table address_type_tokens (guid text, type {token_type}, value text)"
        )
        for guid, field_type, value in rows:
            db.execute("insert or ignore into addresses values (?)", (guid,))
            db.execute(
                "insert into address_type_tokens values (?, ?, ?)",
                (guid, field_type, value),
            )
    db.commit()
    db.close()
    return profile


def _proposal(candidate, *, action="add", **overrides):
    proposal = {
        "candidate_id": candidate["candidate_id"],
        "field_id": candidate["candidate_id"],
        "action": action,
        "category": "chrome-address",
        "display_name": candidate["field_type"],
        "value": candidate["value"],
        "sensitivity": "normal",
        "allowed_origins": ["example.invalid"],
        "allowed_purposes": ["intake"],
        "source_profile_id": candidate["source_profile_id"],
        "source_guid": candidate["source_guid"],
        "field_type": candidate["field_type"],
        "provenance": {
            "source": "chrome-structured-address",
            "source_profile_id": candidate["source_profile_id"],
            "source_guid": candidate["source_guid"],
            "field_type": candidate["field_type"],
        },
    }
    proposal.update(overrides)
    return proposal


def test_recipe_reads_only_allowlisted_fields_and_preserves_groups(tmp_path):
    ns = _recipe_namespace()
    rows = [
        ("guid-home", 7, "SYNTHETIC HOME"),
        ("guid-home", 9, "home@example.invalid"),
        ("guid-home", 77, "ONE TEST STREET"),
        ("guid-home", 33, "TESTVILLE"),
        ("guid-home", 51, "4111111111111111"),
        ("guid-work", 7, "SYNTHETIC WORK"),
        ("guid-work", 14, "+1-555-0100"),
        ("guid-work", 36, "US"),
        ("guid-work", 199, "ignore this history"),
    ]
    profile = _web_data(tmp_path, rows)
    db_path = profile / "Web Data"
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    result = ns["read_structured_addresses"](profile)
    again = ns["read_structured_addresses"](profile)

    assert result == again
    assert {item["source_guid"] for item in result} == {"guid-home", "guid-work"}
    assert {item["field_type"] for item in result} == {
        "name",
        "email",
        "street",
        "city",
        "phone",
        "country",
    }
    assert not any("credit" in str(item).lower() for item in result)
    assert not any("history" in str(item).lower() for item in result)
    assert all(re.fullmatch(r"chrome_addr_[a-z0-9_]+", item["candidate_id"]) for item in result)
    assert all(not re.search(r"\d{8,}", item["candidate_id"]) for item in result)
    assert before == hashlib.sha256(db_path.read_bytes()).hexdigest()
    assert sorted(path.name for path in profile.iterdir()) == ["Web Data"]


def test_recipe_maps_documented_integer_field_types_without_merging_street_fields(
    tmp_path,
):
    ns = _recipe_namespace()
    profile = _web_data(
        tmp_path,
        [("guid", 7, "SYNTHETIC NAME"), ("guid", 30, "LINE ONE"), ("guid", 77, "FULL STREET")],
        token_type="integer",
    )
    result = ns["read_structured_addresses"](profile)
    assert {item["field_type"] for item in result} == {"name", "street_line1", "street"}
    assert len({item["candidate_id"] for item in result}) == 3


def test_recipe_rejects_unsupported_schema_and_malformed_source(tmp_path):
    ns = _recipe_namespace()
    profile = _web_data(tmp_path, [], token_type="text", include_tables=True)
    with pytest.raises(ns["ChromeIntakeError"], match="unsupported"):
        ns["read_structured_addresses"](profile)

    malformed = _web_data(tmp_path / "malformed", [("guid", 7, "ok")])
    db_path = malformed / "Web Data"
    db = sqlite3.connect(db_path)
    db.execute(
        "update address_type_tokens set value=? where guid=? and type=?",
        (b"not-text", "guid", 7),
    )
    db.commit()
    db.close()
    with pytest.raises(ns["ChromeIntakeError"], match="malformed"):
        ns["read_structured_addresses"](malformed)

    conflict = _web_data(tmp_path / "conflict", [("guid", 7, "one")])
    db = sqlite3.connect(conflict / "Web Data")
    db.execute(
        "insert into address_type_tokens values (?, ?, ?)",
        ("guid", 7, "two"),
    )
    db.commit()
    db.close()
    with pytest.raises(ns["ChromeIntakeError"], match="conflicting"):
        ns["read_structured_addresses"](conflict)


def test_recipe_reports_locked_source_without_path_or_source_value(tmp_path):
    ns = _recipe_namespace()
    profile = _web_data(tmp_path, [("guid", 77, "SYNTHETIC_LOCKED_VALUE")])
    db_path = profile / "Web Data"
    writer = sqlite3.connect(db_path)
    writer.execute("begin exclusive")
    try:
        with pytest.raises(ns["ChromeIntakeError"]) as raised:
            ns["read_structured_addresses"](profile)
    finally:
        writer.rollback()
        writer.close()
    message = str(raised.value).lower()
    assert "lock" in message or "busy" in message
    assert str(db_path) not in str(raised.value)
    assert "synthetic_locked_value" not in message


def test_recipe_reads_a_committed_uncheckpointed_wal_row(tmp_path):
    ns = _recipe_namespace()
    profile = _web_data(tmp_path, [("guid", 7, "INITIAL")])
    db_path = profile / "Web Data"
    writer = sqlite3.connect(db_path)
    writer.execute("pragma journal_mode=wal")
    writer.execute("pragma wal_autocheckpoint=0")
    writer.execute("insert into addresses values (?)", ("wal-guid",))
    writer.execute(
        "insert into address_type_tokens values (?, ?, ?)",
        ("wal-guid", 7, "COMMITTED_WAL_VALUE"),
    )
    writer.commit()
    try:
        result = ns["read_structured_addresses"](profile)
        assert any(item["value"] == "COMMITTED_WAL_VALUE" for item in result)
    finally:
        writer.close()


def test_save_recipe_requires_approval_and_writes_approved_subset_with_metadata(
    tmp_path,
):
    ns = _recipe_namespace()
    profile = _web_data(
        tmp_path,
        [("guid", 77, "SYNTHETIC STREET"), ("guid", 33, "TESTVILLE")],
    )
    candidates = ns["read_structured_addresses"](profile)
    by_type = {item["field_type"]: item for item in candidates}
    broker = Broker(Store(":memory:", key=b"r" * 32))
    proposals = [
        _proposal(by_type["street"]),
        _proposal(by_type["city"]),
        {
            **_proposal(by_type["street"], action="no-op"),
            "candidate_id": "chrome_addr_noop_aaaa_bbbb_cccc_dddd_eeee_ffff",
            "field_id": "chrome_addr_noop_aaaa_bbbb_cccc_dddd_eeee_ffff",
        },
        {
            **_proposal(by_type["street"], action="held"),
            "candidate_id": "chrome_addr_held_aaaa_bbbb_cccc_dddd_eeee_ffff",
            "field_id": "chrome_addr_held_aaaa_bbbb_cccc_dddd_eeee_ffff",
        },
    ]
    no_approval = ns["save_approved_profiles"](broker, proposals)
    assert no_approval["status"] == "cancelled"
    assert no_approval["saved_ids"] == []
    assert broker.list_profile_fields() == []

    no_rows = ns["save_approved_profiles"](broker, proposals, [])
    assert no_rows["saved_ids"] == []
    assert broker.list_profile_fields() == []

    street = proposals[0]
    saved = ns["save_approved_profiles"](
        broker,
        proposals,
        [street["candidate_id"], proposals[1]["candidate_id"]],
    )
    assert saved["status"] == "complete"
    assert saved["saved_ids"] == [street["field_id"], proposals[1]["field_id"]]
    assert saved["skipped_ids"] == [proposals[2]["candidate_id"], proposals[3]["candidate_id"]]
    assert saved["pending_ids"] == []
    stored = broker.store.get("profile", street["field_id"])
    assert stored["value"] == "SYNTHETIC STREET"
    assert stored["allowed_origins"] == ["example.invalid"]
    assert stored["allowed_purposes"] == ["intake"]
    assert stored["provenance"] == {
        "source": "chrome-structured-address",
        "source_profile_id": street["source_profile_id"],
        "source_guid": "guid",
        "field_type": "street",
    }
    assert "value" not in str(broker.list_profile_fields())


def test_save_recipe_holds_stale_updates_and_never_overwrites_an_existing_add(tmp_path):
    ns = _recipe_namespace()
    profile = _web_data(tmp_path, [("guid", 77, "SYNTHETIC STREET")])
    candidate = ns["read_structured_addresses"](profile)[0]
    broker = Broker(Store(":memory:", key=b"s" * 32))
    broker.put_profile(
        candidate["candidate_id"],
        "chrome-address",
        "street",
        "ORIGINAL",
        allowed_origins=[],
        allowed_purposes=[],
        provenance={"source": "existing"},
    )
    add = _proposal(candidate, value="REPLACEMENT")
    held = ns["save_approved_profiles"](broker, [add], [candidate["candidate_id"]])
    assert held["saved_ids"] == []
    assert held["held_ids"] == [candidate["candidate_id"]]
    assert broker.store.get("profile", candidate["candidate_id"])["value"] == "ORIGINAL"

    metadata = broker.list_profile_fields()[0]
    update = _proposal(
        candidate,
        action="update",
        value="UPDATED",
        expected_metadata={
            "field_id": metadata["field_id"],
            "updated_at": metadata["updated_at"],
        },
    )
    updated = ns["save_approved_profiles"](broker, [update], [candidate["candidate_id"]])
    assert updated["saved_ids"] == [candidate["candidate_id"]]
    assert broker.store.get("profile", candidate["candidate_id"])["value"] == "UPDATED"

    stale = _proposal(
        candidate,
        action="update",
        value="STALE",
        expected_metadata={
            "field_id": metadata["field_id"],
            "updated_at": metadata["updated_at"],
        },
    )
    stale_result = ns["save_approved_profiles"](
        broker, [stale], [candidate["candidate_id"]]
    )
    assert stale_result["saved_ids"] == []
    assert stale_result["held_ids"] == [candidate["candidate_id"]]
    assert broker.store.get("profile", candidate["candidate_id"])["value"] == "UPDATED"


def test_save_recipe_keeps_source_candidate_and_approved_target_ids_distinct(tmp_path):
    ns = _recipe_namespace()
    profile = _web_data(tmp_path, [("guid", 77, "SYNTHETIC STREET")])
    candidate = ns["read_structured_addresses"](profile)[0]
    target_id = "home_street"
    broker = Broker(Store(":memory:", key=b"t" * 32))
    proposal = _proposal(candidate, field_id=target_id)
    result = ns["save_approved_profiles"](broker, [proposal], [candidate["candidate_id"]])
    assert result["saved_ids"] == [target_id]
    assert broker.store.get("profile", target_id)["value"] == "SYNTHETIC STREET"
    assert broker.store.get("profile", candidate["candidate_id"]) is None


def test_save_recipe_rejects_duplicate_or_noncanonical_approved_targets(tmp_path):
    ns = _recipe_namespace()
    profile = _web_data(tmp_path, [("guid", 77, "SYNTHETIC STREET")])
    candidate = ns["read_structured_addresses"](profile)[0]
    broker = Broker(Store(":memory:", key=b"d" * 32))
    first = _proposal(candidate, field_id="home_street")
    duplicate = _proposal(
        {**candidate, "candidate_id": "chrome_addr_other_aaaa_bbbb_cccc_dddd_eeee_ffff"},
        field_id="home_street",
    )
    duplicate["source_guid"] = "other"
    duplicate["provenance"] = {
        **duplicate["provenance"],
        "source_guid": "other",
    }
    rejected = ns["save_approved_profiles"](
        broker,
        [first, duplicate],
        [first["candidate_id"], duplicate["candidate_id"]],
    )
    assert rejected["status"] == "rejected"
    assert rejected["saved_ids"] == []
    assert broker.list_profile_fields() == []

    uppercase = _proposal(candidate, field_id="Home_Street")
    bad = ns["save_approved_profiles"](broker, [uppercase], [candidate["candidate_id"]])
    assert bad["status"] == "rejected"
    assert bad["saved_ids"] == []


def test_save_recipe_stops_on_failure_and_reports_only_metadata(tmp_path):
    ns = _recipe_namespace()
    profile = _web_data(
        tmp_path,
        [("guid-a", 77, "SYNTHETIC A"), ("guid-b", 33, "SYNTHETIC B")],
    )
    candidates = ns["read_structured_addresses"](profile)

    class FailingBroker:
        def __init__(self):
            self.calls = []

        def list_profile_fields(self):
            return []

        def put_profile(self, field_id, *args, **kwargs):
            self.calls.append(field_id)
            if len(self.calls) == 2:
                raise RuntimeError("SYNTHETIC_SECRET_MUST_NOT_LEAK")

    broker = FailingBroker()
    proposals = [_proposal(candidate) for candidate in candidates]
    result = ns["save_approved_profiles"](
        broker, proposals, [candidate["candidate_id"] for candidate in candidates]
    )
    assert result["status"] == "partial"
    assert result["saved_ids"] == [candidates[0]["candidate_id"]]
    assert result["failed_ids"] == [candidates[1]["candidate_id"]]
    assert result["pending_ids"] == []
    assert "SYNTHETIC_SECRET_MUST_NOT_LEAK" not in str(result)
    assert broker.calls == [candidate["candidate_id"] for candidate in candidates]


@pytest.mark.parametrize(
    "missing", ["value", "category", "sensitivity", "allowed_origins", "allowed_purposes", "provenance"]
)
def test_save_recipe_rejects_incomplete_approved_rows(tmp_path, missing):
    ns = _recipe_namespace()
    profile = _web_data(tmp_path, [("guid", 9, "synthetic@example.invalid")])
    candidate = ns["read_structured_addresses"](profile)[0]
    proposal = _proposal(candidate)
    del proposal[missing]
    broker = Broker(Store(":memory:", key=b"i" * 32))
    result = ns["save_approved_profiles"](broker, [proposal], [candidate["candidate_id"]])
    assert result["status"] == "rejected"
    assert broker.list_profile_fields() == []


@pytest.mark.parametrize("duplicate", ["candidate", "approval"])
def test_save_recipe_rejects_ambiguous_approval_mapping(tmp_path, duplicate):
    ns = _recipe_namespace()
    profile = _web_data(tmp_path, [("guid", 9, "synthetic@example.invalid")])
    candidate = ns["read_structured_addresses"](profile)[0]
    proposals = [_proposal(candidate)]
    approvals = [candidate["candidate_id"]]
    if duplicate == "candidate":
        proposals.append(_proposal(candidate, field_id="other_email"))
    else:
        approvals.append(candidate["candidate_id"])
    broker = Broker(Store(":memory:", key=b"j" * 32))
    result = ns["save_approved_profiles"](broker, proposals, approvals)
    assert result["status"] == "rejected"
    assert broker.list_profile_fields() == []


def test_recipe_distinguishes_profiles_and_holds_unavailable_metadata(tmp_path):
    ns = _recipe_namespace()
    first = _web_data(tmp_path / "first", [("guid", 9, "synthetic@example.invalid")])
    second = _web_data(tmp_path / "second", [("guid", 9, "synthetic@example.invalid")])
    a = ns["read_structured_addresses"](first)[0]
    b = ns["read_structured_addresses"](second)[0]
    assert a["candidate_id"] != b["candidate_id"]
    assert a["source_profile_id"] != b["source_profile_id"]

    class UnavailableBroker:
        def list_profile_fields(self):
            raise RuntimeError("SYNTHETIC_PRIVATE_FAILURE")

        def put_profile(self, *args, **kwargs):
            pytest.fail("unavailable metadata must prevent writes")

    result = ns["save_approved_profiles"](
        UnavailableBroker(), [_proposal(a)], [a["candidate_id"]]
    )
    assert result["saved_ids"] == []
    assert result["held_ids"] == [a["candidate_id"]]
    assert "SYNTHETIC_PRIVATE_FAILURE" not in str(result)
