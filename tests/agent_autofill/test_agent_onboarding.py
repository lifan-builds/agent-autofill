import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_autofill.agent_onboarding import AgentOnboarding, run
from agent_autofill.cli import main
from agent_autofill.review import AGENT, sample_trial
from agent_autofill.service import BrokerError


def proposed(group, **meanings):
    return {"id": group["id"], "revision": group["revision"], **meanings}


def profiles(store):
    return [(row[0], store.get("profile", row[0])) for row in
            store.db.execute("select key from kv where namespace='profile' order by key")]


def test_task_inspection_compares_without_assigning_or_merging():
    session = AgentOnboarding(sample_trial())
    report = session.inspect("delivery")
    assert report["total_groups"] == 4 and report["relevant_groups"] == 3
    assert all(g["who"] == g["role"] == g["currentness"] == "unknown" for g in report["groups"])
    assert len(report["comparisons"]) == 3
    assert all(c["evidence"] == ["same_name_text"] for c in report["comparisons"])
    assert session.store.count("context") == 0


def test_equal_address_is_not_identity_or_merge():
    session = AgentOnboarding(sample_trial())
    records = [(fid, record) for fid, record in profiles(session.store) if fid.startswith("sample_home_")]
    for fid, record in records:
        copy = {**record, "field_id": fid.replace("home", "duplicate"),
                "provenance": {**record["provenance"], "source_guid": "different-person"}}
        if fid.endswith("full_name"):
            copy["value"] = "Someone Else"
        session.store.put("profile", fid.replace("home", "duplicate"), copy)
    report = session.inspect("delivery")
    assert report["relevant_groups"] == 4
    assert any(c["evidence"] == ["same_address_text"] for c in report["comparisons"])
    assert session.store.count("context") == 0


def test_proposal_survives_session_restart_and_needs_exact_confirmation():
    store = sample_trial()
    session = AgentOnboarding(store)
    group = session.inspect("delivery")["groups"][0]
    before = profiles(store)
    proposal = session.propose({"changes": [proposed(group, who="self", role="home", currentness="current")]})
    assert store.count("context") == 0
    resumed = AgentOnboarding(store)
    with pytest.raises(BrokerError, match="approve"):
        resumed.confirm(proposal["proposal_id"])
    assert resumed.confirm(proposal["proposal_id"], approved=True)["saved_groups"] == 1
    assert profiles(store) == before
    assert not any(r["kind"] == "preference" for r in resumed.review.memory._rows())
    with pytest.raises(BrokerError, match="changed"):
        resumed.confirm(proposal["proposal_id"], approved=True)


def test_changed_source_rejects_whole_batch():
    session = AgentOnboarding(sample_trial())
    groups = session.inspect("delivery")["groups"][:2]
    proposal = session.propose({"changes": [proposed(g, who="self") for g in groups]})
    fid = next(g["field_ids"][0] for g in session.review.broker.list_profile_groups() if g["group_id"] == groups[1]["id"])
    record = session.store.get("profile", fid)
    session.store.put("profile", fid, {**record, "value": "changed synthetic value"})
    with pytest.raises(BrokerError, match="changed"):
        session.confirm(proposal["proposal_id"], approved=True)
    assert session.store.count("context") == 0


@pytest.mark.parametrize("extra", [{"who": "bad"}, {"preferred": True}, {"value": "changed"}, {"confirmed": True}])
def test_invalid_proposals_never_become_remembered_facts(extra):
    session = AgentOnboarding(sample_trial())
    group = session.inspect()["groups"][0]
    with pytest.raises(BrokerError):
        session.propose({"changes": [proposed(group, **extra)]})
    assert session.store.count("review-proposal") == session.store.count("context") == 0


def test_single_confirmed_answer_makes_requested_task_ready_without_other_labels():
    session = AgentOnboarding(sample_trial())
    report = session.inspect("delivery")
    group = next(g for g in report["groups"] if any(f["value"] == "12 Cedar Lane" for f in g["fields"]))
    proposal = session.propose({"changes": [proposed(group, who="self", role="home", currentness="current")]})
    session.confirm(proposal["proposal_id"], approved=True)
    task = {"target_origin": "https://delivery.example", "purpose": "delivery", "requested_fields": ["street", "city", "postcode"], "subject": "self", "role": "home"}
    assert session.review.memory.retrieve(AGENT, **task)["status"] == "ready"
    # Repeated retrieval uses the confirmed meaning, with no new classification.
    assert session.review.memory.retrieve(AGENT, **task)["status"] == "ready"
    assert sum(g["reviewed"] for g in session.inspect()["groups"]) == 1
    assert session.store.count("context") == 1


def test_cli_sample_is_read_only_for_meanings_and_errors_hide_source(monkeypatch, capsys):
    assert main(["review-agent", "--sample", "--task", "delivery"]) == 0
    assert json.loads(capsys.readouterr().out)["relevant_groups"] == 3
    args = SimpleNamespace(resume=None, profile_snapshot=False, db="agent-autofill.sqlite3", propose=False, confirm=None, approved=False, task="delivery")
    assert run(args) == 0
    assert json.loads(capsys.readouterr().out)["relevant_groups"] == 3
    args.resume = Path("/synthetic/trial")
    args.propose = True
    monkeypatch.setattr("agent_autofill.review.resume_trial", lambda path: sample_trial())
    monkeypatch.setattr("sys.stdin", io.StringIO('{"private synthetic source'))
    with pytest.raises(SystemExit) as exc:
        run(args)
    assert "private synthetic source" not in str(exc.value)


def test_trial_query_returns_only_requested_confirmed_values():
    session = AgentOnboarding(sample_trial())
    group = next(g for g in session.inspect()["groups"] if any(f["value"] == "12 Cedar Lane" for f in g["fields"]))
    proposal = session.propose({"changes": [proposed(group, who="self", role="home", currentness="current")]})
    session.confirm(proposal["proposal_id"], approved=True)
    args = {"target_origin": "https://shop.example", "purpose": "delivery", "requested_fields": ["street"], "subject": "self", "role": "home"}
    assert "values" not in session.query(args)
    assert session.query({**args, "read_values": True})["values"] == {"street": "12 Cedar Lane"}
    assert "values" not in session.query({**args, "role": "work", "read_values": True})
    with pytest.raises(BrokerError):
        session.query({**args, "read_values": "yes"})
    with pytest.raises(BrokerError):
        session.query({**args, "agent_id": "another"})
    fid = next(g["field_ids"][0] for g in session.review.broker.list_profile_groups() if g["group_id"] == group["id"])
    record = session.store.get("profile", fid)
    session.store.put("profile", fid, {**record, "updated_at": "changed"})
    stale = session.query({**args, "read_values": True})
    assert stale["context"]["status"] == "needs_review" and "values" not in stale


def test_trial_query_cli_requires_resume_and_does_not_open_live_vault(monkeypatch, capsys):
    with pytest.raises(SystemExit, match="Resume an existing trial"):
        main(["review-agent", "--query"])
    monkeypatch.setattr("agent_autofill.review.resume_trial", lambda path: sample_trial())
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"target_origin": "https://shop.example", "purpose": "delivery", "requested_fields": ["street"], "read_values": True})))
    assert main(["review-agent", "--resume", "/synthetic/trial", "--query"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["context"]["status"] == "no_authorized_context" and "values" not in result


def test_agent_onboarding_apply_to_live_requires_approval():
    session = AgentOnboarding(sample_trial())
    with pytest.raises(BrokerError, match="approve"):
        session.apply_to_live(sample_trial(), approved=False)
