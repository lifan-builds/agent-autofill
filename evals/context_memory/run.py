"""Progressive no-submit task replay. Reports contain aggregate results only.

Run with PYTHONPATH=. uv run python evals/context_memory/run.py.
Add --real-profile-snapshot to read ONLY encrypted ordinary profile rows from
the installed vault in SQLite read-only mode. The decrypted working copy lives
in memory, is never installed, and is destroyed when the process exits.
"""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from agent_autofill.context_memory import ContextMemory
from agent_autofill.form_memory import _field_semantics
from agent_autofill.local import database_key_name, default_database_path
from agent_autofill.service import Broker, _normalize_origin
from agent_autofill.storage import KeyringProvider, Store

ORIGIN = "https://context-evaluation.invalid"
PURPOSE = "no-submit task replay"
AGENT = "service:context-evaluation"


def prepare(broker):
    broker.create_agent(AGENT)
    broker.enable_trusted_local(AGENT)
    broker.grant(AGENT, "profile.write", "*", ORIGIN, PURPOSE)
    return ContextMemory(broker)


def write(memory, kind, rid, data, **kwargs):
    return memory.write(AGENT, kind=kind, record_id=rid, data=data,
                        expected_revision=0, target_origin=ORIGIN, purpose=PURPOSE, **kwargs)


def retrieve(memory, **kwargs):
    return memory.retrieve(AGENT, target_origin=ORIGIN, purpose=PURPOSE,
                           requested_fields=["street", "city", "postcode", "country"],
                           subject="synthetic-self", role="delivery", **kwargs)


def synthetic(preview=None):
    broker = Broker(Store())
    memory = prepare(broker)
    stages = []
    for name, currentness in (("home", "current"), ("work", "current"), ("old", "historical")):
        fields, entries = {}, []
        for semantic in ("street", "city", "postcode", "country"):
            fid = f"{name}_{semantic}"
            fields[semantic] = fid
            entries.append({"record": {"field_id": fid, "category": "address", "display_name": semantic,
                                        "value": f"SYNTHETIC {name} {semantic}",
                                        "provenance": {"source": "chrome-structured-address", "source_profile_id": "synthetic", "source_guid": name, "field_type": semantic}},
                            "expected_updated_at": None})
        write(memory, "bundle", name, {"fields": fields, "subject": "synthetic-self", "role": "delivery", "title": name, "currentness": currentness},
              confirmed=True, profile_entries=entries)
    assert broker.store.count("profile") == 12
    stages.append({"stage": "intake", "facts_preserved": 12, "bundles": 3})
    baseline = broker.match_form_fields([{"label": "Shipping address"}], target_origin=ORIGIN, purpose=PURPOSE)
    initial = retrieve(memory)
    assert initial["status"] == "needs_clarification"
    assert {c["bundle_id"] for c in initial["candidates"]} == {"home", "work"}
    stages.append({"stage": "retrieve", "historical_excluded": True, "clarification_required": True,
                   "legacy_status": baseline[0]["status"]})
    once = retrieve(memory, selected_bundle_id="work")
    fields = once["candidates"][0]["fields"]
    values = broker.request_profile_fields(AGENT, list(fields.values()), ORIGIN, PURPOSE)
    assert len(values) == 4 and all("work" in value["value"] for value in values.values())
    del values
    stages.append({"stage": "scoped_read", "required_fields": 4, "correct_fields": 4, "mixed_bundles": 0})
    for index in range(5):
        write(memory, "observation", f"one-time-{index}", {"bundle_refs": ["work"], "outcome": "used_once"})
        assert retrieve(memory)["selected_bundle_id"] is None
    stages.append({"stage": "one_time_feedback", "replays": 5, "silent_defaults": 0})
    write(memory, "document", "delivery-guide", {"bundle_refs": ["home", "work"], "title": "Delivery choices", "body": "Both groups have confirmed components. Ask which destination this delivery needs. This note is supporting context, not a default."})
    write(memory, "preference", "home-delivery", {"bundle_refs": ["home"], "subject": "synthetic-self", "role": "delivery"}, confirmed=True)
    repetitions = [retrieve(memory) for _ in range(3)]
    assert all(row["selected_bundle_id"] == "home" for row in repetitions)
    stages.append({"stage": "confirmed_reuse", "tasks": 3, "correct_selections": 3, "repeated_clarifications": 0,
                   "legacy_repeated_clarifications": sum(broker.match_form_fields([{"label": "Shipping address"}], target_origin=ORIGIN, purpose=PURPOSE)[0]["status"] == "ambiguous" for _ in range(3)),
                   "max_context_characters": max(len(json.dumps(row)) for row in repetitions)})
    if preview is not None:
        from agent_autofill.inbox import build_html

        preview.write_text(build_html(broker))
    bundle = broker.store.get("context", "bundle:home")
    broker.put_profile("home_city", "address", "city", "SYNTHETIC CHANGED")
    stale = retrieve(memory)
    assert not stale["untrusted_notes"] and all(c["bundle_id"] != "home" for c in stale["candidates"])
    stages.append({"stage": "evidence_change", "stale_notes_returned": 0, "stale_bundles_returned": 0})
    broker.delete_record("profile", "home_city")
    assert broker.store.get("context", "bundle:home") is None
    assert all("home_city" not in item["field_ids"] for item in memory._rows())
    assert bundle["revision"] == 1
    stages.append({"stage": "forget", "dependent_records_remaining": 0})
    broker.store.db.close()
    return {"status": "passed", "stages": stages, "scope": "deterministic synthetic no-submit tasks; not an LLM or live-site success rate"}


def profile_snapshot(path, provider):
    """Explicit local admin evaluation, never a fallback around broker grants."""
    key = provider.load_existing(database_key_name(path))
    source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        source.execute("pragma query_only=on")
        rows = source.execute("select namespace,key,value from kv where namespace='profile' order by key").fetchall()
        digest = hashlib.sha256(json.dumps(rows).encode()).digest()
        store = Store(key=key)
        store.db.executemany("insert into kv values (?,?,?)", rows)
        store.db.commit()
        return Broker(store), digest
    finally:
        source.close()


def real_snapshot():
    path = default_database_path().resolve()
    broker, before = profile_snapshot(path, KeyringProvider())
    memory = prepare(broker)
    metadata = {row["field_id"]: row for row in broker._iter_profile_fields()}
    counts = Counter()
    statuses = Counter()
    scopes = set()
    for index, group in enumerate(broker.list_profile_groups()):
        counts["source_groups"] += 1
        # Real-data ground truth here is source membership and available fields,
        # never guessed ownership, home/work meaning, currentness or preferences.
        fields = {}
        duplicates = set()
        for fid in group["field_ids"]:
            for semantic in _field_semantics(metadata[fid]):
                if semantic in fields:
                    duplicates.add(semantic)
                fields[semantic] = fid
        for semantic in duplicates:
            fields.pop(semantic)
        counts["ambiguous_semantic_components"] += len(duplicates)
        if not fields:
            counts["groups_without_semantic_fields"] += 1
            continue
        bid = f"snapshot-{index}"
        scoped_origins = [{_normalize_origin(value, "origin") for value in metadata[fid].get("allowed_origins", [])} for fid in fields.values() if metadata[fid].get("allowed_origins")]
        scoped_purposes = [set(metadata[fid]["allowed_purposes"]) for fid in fields.values() if metadata[fid].get("allowed_purposes")]
        origins = set.intersection(*scoped_origins) if scoped_origins else {ORIGIN}
        purposes = set.intersection(*scoped_purposes) if scoped_purposes else {PURPOSE}
        if not origins or not purposes:
            counts["groups_without_shared_write_scope"] += 1
            continue
        origin, purpose = min(origins), min(purposes)
        scopes.add((origin, purpose))
        broker.grant(AGENT, "profile.write", "*", origin, purpose)
        # Shadow-only fixture: these are explicitly UNKNOWN, not confirmed user
        # relationships. Nothing is copied back to the installed vault.
        memory.write(AGENT, kind="bundle", record_id=bid, expected_revision=0,
                     data={"fields": fields, "subject": "unknown", "role": "unknown", "title": bid, "currentness": "unknown"},
                     target_origin=origin, purpose=purpose, confirmed=True)
        counts["shadow_groups"] += 1
        rows = broker.request_profile_fields(AGENT, list(fields.values()), origin, purpose, group_id=group["group_id"])
        assert set(rows) == set(fields.values())
        assert all(rows[fid]["value"] == broker.store.get("profile", fid)["value"] for fid in fields.values())
        counts["values_preserved_and_read"] += len(rows)
        del rows
    for name, semantics in (("delivery", ["street", "city", "postcode", "country"]),
                            ("professional_contact", ["full_name", "email"]),
                            ("telephone_contact", ["phone"])):
        for origin, purpose in scopes:
            result = memory.retrieve(AGENT, target_origin=origin, purpose=purpose, requested_fields=semantics)
            statuses[f"{name}:{result['status']}"] += 1
            assert result["selected_bundle_id"] is None
            counts["task_replays"] += 1
            counts["context_characters"] += len(json.dumps(result))
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as source:
        after = hashlib.sha256(json.dumps(source.execute("select namespace,key,value from kv where namespace='profile' order by key").fetchall()).encode()).digest()
    assert before == after
    counts["profile_fields"] = len(metadata)
    broker.store.db.close()
    return {"status": "passed", "counts": dict(counts), "task_statuses": dict(statuses),
            "production_profile_rows_unchanged": True, "snapshot": "encrypted ordinary profile rows only, in memory, discarded",
            "ground_truth": "source-group membership and exact value preservation; personal meanings unreviewed",
            "limitations": "No site submission, production write, secrets, or inferred personal defaults. No correctness claim for unreviewed home/work identities."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-profile-snapshot", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--preview", type=Path, help="Write the synthetic Inbox HTML; never includes real snapshot data")
    args = parser.parse_args()
    result = {"synthetic": synthetic(args.preview)}
    if args.real_profile_snapshot:
        try:
            result["real_snapshot"] = real_snapshot()
        except Exception as exc:  # noqa: BLE001 - provider errors may contain private values
            result["real_snapshot"] = {"status": "blocked_or_failed", "error_type": type(exc).__name__}
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end="")
    if result.get("real_snapshot", {}).get("status") == "blocked_or_failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
