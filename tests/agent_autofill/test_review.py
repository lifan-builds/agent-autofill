import json
import queue
import threading
from http.client import HTTPConnection
from urllib.parse import parse_qs, urlsplit

import pytest

from agent_autofill.context_memory import ContextMemory
from agent_autofill.review import (
    AGENT,
    ORIGIN,
    PURPOSE,
    ReviewSession,
    apply_trial_to_live,
    create_trial,
    make_server,
    resume_trial,
    sample_trial,
)
from agent_autofill.service import Broker, BrokerError
from agent_autofill.storage import Store


def change(group, **values):
    return {key: group[key] for key in ("id", "revision", "who", "role", "currentness")} | values


def test_save_reviewed_meanings_without_changing_facts_or_defaults():
    session = ReviewSession(sample_trial())
    before = session.broker.store.db.execute("select key,value from kv where namespace='profile' order by key").fetchall()
    groups = session.state()["groups"]
    result = session.confirm({"confirmed": True, "changes": [change(groups[0], who="self", role="home", currentness="current")]})
    assert result["saved"] == result["state"]["reviewed_count"] == 1
    after = session.broker.store.db.execute("select key,value from kv where namespace='profile' order by key").fetchall()
    assert [tuple(row) for row in before] == [tuple(row) for row in after]
    rows = ContextMemory(session.broker)._rows()
    assert len(rows) == 1 and rows[0]["scope"] == "shared"
    assert rows[0]["kind"] == "bundle"
    assert "Cedar" not in json.dumps(session.store.audits())


def test_batch_is_atomic_and_rejects_stale_or_extra_fields():
    session = ReviewSession(sample_trial())
    first, second = session.state()["groups"][:2]
    for payload in (
        {"confirmed": False, "changes": [change(first, who="self")]},
        {"confirmed": True, "changes": [change(first, who="self") | {"value": "not permitted"}]},
        {"confirmed": True, "changes": [change(first, who="self"), change(second, revision="stale")]},
    ):
        with pytest.raises(BrokerError):
            session.confirm(payload)
        assert session.store.count("context") == 0
    session.store.db.execute("create trigger fail_review before insert on kv when new.namespace='context' begin select raise(abort,'synthetic failure'); end")
    with pytest.raises(Exception, match="synthetic failure"):
        session.confirm({"confirmed": True, "changes": [change(first, who="self"), change(second, who="self")]})
    assert session.store.count("context") == 0


def test_profile_change_after_preview_preparation_is_not_silently_confirmed(monkeypatch):
    session = ReviewSession(sample_trial())
    group = session.state()["groups"][0]
    original = session.memory._prepare_write

    def interleave(*args, **kwargs):
        fid = next(iter(kwargs["data"]["fields"].values()))
        session.broker.put_profile(fid, "profile", "Updated", "SYNTHETIC NEW VALUE")
        return original(*args, **kwargs)

    monkeypatch.setattr(session.memory, "_prepare_write", interleave)
    with pytest.raises(BrokerError, match="changed"):
        session.confirm({"confirmed": True, "changes": [change(group, who="self")]})
    assert session.store.count("context") == 0


def test_other_people_are_not_merged_by_label():
    session = ReviewSession(sample_trial())
    groups = session.state()["groups"][:2]
    session.confirm({"confirmed": True, "changes": [change(group, who="other") for group in groups]})
    subjects = [item["data"]["subject"] for item in session.memory._rows()]
    assert len(set(subjects)) == 2


def test_shared_meaning_reuses_context_without_widening_field_access():
    session = ReviewSession(sample_trial())
    group = next(group for group in session.state()["groups"] if group["has_address"])
    session.confirm({"confirmed": True, "changes": [change(group, who="self", role="home", currentness="current")]})
    other_origin = "https://another.example.test"
    result = session.memory.retrieve(AGENT, target_origin=other_origin, purpose="delivery", requested_fields=["street"], subject="self", role="home")
    assert result["status"] == "ready"
    selected = result["candidates"][0]
    assert session.broker.request_profile_fields(AGENT, list(selected["fields"].values()), other_origin, "delivery", bundle_id=selected["bundle_id"])
    session.broker.create_agent("outsider")
    assert session.memory.retrieve("outsider", target_origin=other_origin, purpose="delivery", requested_fields=["street"])["candidates"] == []
    session.broker.grant("outsider", "profile.read", "*", other_origin, "delivery")
    fid = next(iter(selected["fields"].values()))
    record = session.store.get("profile", fid)
    record["allowed_origins"] = [ORIGIN]
    session.store.put("profile", fid, record)
    assert session.memory.retrieve("outsider", target_origin=other_origin, purpose="delivery", requested_fields=["street"])["candidates"] == []
    assert session.store.count("form-mapping") == 0


def test_task_preference_can_reference_shared_bundle_but_stays_scoped():
    session = ReviewSession(sample_trial())
    groups = [group for group in session.state()["groups"] if group["has_address"]][:2]
    session.confirm({"confirmed": True, "changes": [change(group, who="self", role="home", currentness="current") for group in groups]})
    session.broker.grant(AGENT, "profile.write", "*", ORIGIN, PURPOSE)
    session.memory.write(AGENT, kind="preference", record_id="delivery", data={"bundle_refs": ["review_" + groups[0]["id"]], "subject": "self", "role": "home"}, expected_revision=0, target_origin=ORIGIN, purpose=PURPOSE, confirmed=True)
    assert session.memory.retrieve(AGENT, target_origin=ORIGIN, purpose=PURPOSE, requested_fields=["street"], subject="self", role="home")["status"] == "ready"
    assert session.memory.retrieve(AGENT, target_origin="https://another.example", purpose=PURPOSE, requested_fields=["street"], subject="self", role="home")["selected_bundle_id"] is None


class MemoryKeys:
    def __init__(self, source):
        self.keys = {source.as_posix(): b"s" * 32}

    def load_existing(self, name):
        return self.keys[name]

    def load_or_create(self, name):
        return self.keys.setdefault(name, b"t" * 32)


def test_trial_snapshot_excludes_secrets_and_cannot_resume_live_database(tmp_path):
    source = tmp_path / "source.db"
    store = Store(source, key=b"s" * 32)
    Broker(store).put_profile("name", "profile", "Full name", "SYNTHETIC PERSON")
    store.put("credential", "secret", {"password": "SYNTHETIC SECRET"})
    store.db.close()
    before = source.read_bytes()
    provider = MemoryKeys(source)
    trial, path = create_trial(source, provider=provider, destination=tmp_path / "trial.db")
    assert trial.count("profile") == 1 and trial.count("credential") == 0
    session = ReviewSession(trial)
    group = session.state()["groups"][0]
    session.confirm({"confirmed": True, "changes": [change(group, who="self")]})
    trial.db.close()
    resumed = resume_trial(path, provider=provider)
    assert ReviewSession(resumed).state()["reviewed_count"] == 1
    resumed.db.close()
    with pytest.raises(ValueError, match="not a profile review trial"):
        resume_trial(source, provider=provider)
    assert source.read_bytes() == before
    assert b"SYNTHETIC PERSON" not in path.read_bytes()


def test_apply_trial_to_live_promotes_confirmed_bundles_and_checks_live_drift(tmp_path):
    source = tmp_path / "live.db"
    live_store = Store(source, key=b"s" * 32)
    live_broker = Broker(live_store)
    live_broker.put_profile(
        "home_street",
        "profile",
        "Street",
        "12 Cedar Lane",
        provenance={
            "source": "chrome-structured-address",
            "source_profile_id": "p1",
            "source_guid": "g1",
            "field_type": "street",
        },
    )
    live_store.db.close()

    provider = MemoryKeys(source)
    trial, _ = create_trial(source, provider=provider, destination=tmp_path / "trial.db")
    session = ReviewSession(trial)
    group = session.state()["groups"][0]
    session.confirm({"confirmed": True, "changes": [change(group, who="self", role="home", currentness="current")]})

    reopened_live = Store(source, key=b"s" * 32)
    result = apply_trial_to_live(trial, reopened_live)
    assert result == {"applied_groups": 1, "live_vault_changed": True}
    groups = Broker(reopened_live).list_profile_groups()
    assert groups[0]["confirmed_meaning"]["role"] == "home"
    assert groups[0]["confirmed_meaning"]["currentness"] == "current"

    # Mutating the live profile after the snapshot must reject promotion
    Broker(reopened_live).put_profile("home_street", "profile", "Street", "99 Changed Ave")
    with pytest.raises(BrokerError, match="Live vault changed"):
        apply_trial_to_live(trial, reopened_live)
    trial.db.close()
    reopened_live.db.close()


def test_http_requires_capability_and_same_origin_and_disallows_rebinding():
    ready = queue.Queue()

    def run():
        session = ReviewSession(sample_trial())
        server, url = make_server(session)
        ready.put((server, url))
        server.serve_forever()
        server.server_close()
        session.store.db.close()

    thread = threading.Thread(target=run)
    thread.start()
    server, url = ready.get(timeout=5)
    parsed = urlsplit(url)
    token = parse_qs(parsed.fragment)["access"][0]
    origin = f"http://127.0.0.1:{parsed.port}"

    def request(method, path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", parsed.port, timeout=5)
        connection.request(method, path, json.dumps(body) if body is not None else None, headers or {})
        response = connection.getresponse()
        content = response.read()
        connection.close()
        return response.status, content, dict(response.getheaders())

    try:
        assert request("GET", "/api/state")[0] == 403
        assert request("GET", "/api/state", headers={"Host": "attacker.test", "X-Review-Token": token})[0] == 403
        status, body, headers = request("GET", "/api/state", headers={"X-Review-Token": token})
        assert status == 200 and headers["Cache-Control"] == "no-store"
        state = json.loads(body)
        payload = {"confirmed": True, "changes": [change(state["groups"][0], who="self")]}
        assert request("POST", "/api/confirm", payload, {"X-Review-Token": token, "Content-Type": "application/json", "Origin": "https://attacker.test", "X-Review-Action": "confirm"})[0] == 403
        assert request("POST", "/api/confirm", payload, {"X-Review-Token": token, "Content-Type": "application/json", "Origin": origin, "X-Review-Action": "confirm"})[0] == 200
        status, body, headers = request("GET", "/")
        assert status == 200 and b"SYNTHETIC" not in body
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    finally:
        server.shutdown()
        thread.join(timeout=5)
