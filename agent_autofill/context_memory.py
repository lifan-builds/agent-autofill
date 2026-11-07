"""Encrypted contextual views over ordinary profile facts.

Only typed, confirmed bundles and preferences influence selection. Document
prose and observations are evidence for the acting agent, never authorization.
"""

from __future__ import annotations

import re
from datetime import date

from .form_memory import normalize_form_label
from .service import (
    _RECORD_ENTRY_KEYS,
    AuthorizationError,
    BrokerError,
    _normalize_origin,
    _record_scope_allowed,
    now_iso,
)

NAMESPACE = "context"
HISTORY = "context-history"
KINDS = {"bundle", "document", "preference", "observation"}
_KEYS = {
    "bundle": {"fields", "subject", "role", "currentness", "title"},
    "document": {"bundle_refs", "title", "body"},
    "preference": {"bundle_refs", "subject", "role", "valid_until"},
    "observation": {"bundle_refs", "outcome"},
}


def _text(value, name, maximum=120):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise BrokerError(f"{name} must be nonempty text of at most {maximum} characters")
    return value.strip()


def _id(value):
    value = _text(value, "id")
    if not re.fullmatch(r"[a-zA-Z0-9_.:-]+", value):
        raise BrokerError("id must contain letters, numbers, dots, colons, dashes or underscores")
    return value


def scope_matches(record, origin, purpose):
    return (record.get("kind") == "bundle" and record.get("scope") == "shared") or (
        record["target_origin"], record["purpose"]
    ) == (origin, purpose)


class ContextMemory:
    def __init__(self, broker):
        self.broker = broker
        self.store = broker.store

    def _rows(self):
        return [self.store._open(row["value"]) for row in self.store.db.execute(
            "select value from kv where namespace=? order by key", (NAMESPACE,)
        )]

    def _authorized_fields(self, agent, fields, origin, purpose, *, write=False):
        snapshots = []
        for fid in fields:
            self.broker._authorized(agent, "profile.write" if write else "profile.read", fid, origin, purpose)
            item = self.store.get("profile", fid)
            if item is None:
                raise BrokerError("referenced profile field no longer exists")
            record = self.broker._profile_defaults(fid, item)
            _record_scope_allowed(record["allowed_origins"], record["allowed_purposes"], origin, purpose,
                                  trusted_local=not write and self.broker._is_trusted_local(agent))
            snapshots.append(("profile", fid, item))
        return snapshots

    def write(self, agent_id, **kwargs):
        puts, expected, audit, result = self._prepare_write(agent_id, **kwargs)
        try:
            self.store.apply_changes(puts, [], expected=expected, audits=[audit])
        except ValueError:
            raise BrokerError("record changed; review the current revision") from None
        return result

    def _prepare_write(self, agent_id, *, kind, record_id, data, expected_revision,
              target_origin, purpose, confirmed=False, profile_entries=None,
              restore_revision=None, shared=False):
        """One exact revisioned proposal; optional profile intake is atomic."""
        if not isinstance(kind, str) or kind not in KINDS:
            raise BrokerError("unknown context kind")
        if type(shared) is not bool or (shared and kind != "bundle"):
            raise BrokerError("only confirmed bundle meanings can be shared across tasks")
        rid = _id(record_id)
        key = f"{kind}:{rid}"
        origin = _normalize_origin(target_origin, "target_origin")
        purpose = _text(purpose, "purpose")
        if type(expected_revision) is not int or expected_revision < 0:
            raise BrokerError("expected_revision must be a nonnegative integer")
        if kind in {"bundle", "preference"} and confirmed is not True:
            raise BrokerError("facts, identity relationships and defaults require confirmation")
        current = self.store.get(NAMESPACE, key)
        scope = "shared" if shared else "task"
        if current and current.get("scope", "task") != scope:
            raise BrokerError("context scope is immutable; create a separate reviewed record")
        if (current or {}).get("revision", 0) != expected_revision:
            raise BrokerError("record changed; review the current revision")
        if restore_revision is not None:
            if kind != "document" or type(restore_revision) is not int:
                raise BrokerError("only document revisions can be restored")
            old = self.store.get(HISTORY, f"{key}:{restore_revision}")
            if not old or (old["target_origin"], old["purpose"]) != (origin, purpose):
                raise BrokerError("document revision unavailable in this context")
            data = old["data"]
        if not isinstance(data, dict) or set(data) - _KEYS[kind]:
            raise BrokerError("unknown context data fields")
        # Do not let an update move private notes to a different audience.
        if current and (current["target_origin"], current["purpose"]) != (origin, purpose):
            raise BrokerError("context scope is immutable; create a separate reviewed record")
        expected = [(NAMESPACE, key, current)]
        puts = []
        prepared = {}
        if profile_entries is not None:
            if kind != "bundle" or confirmed is not True or not isinstance(profile_entries, list) or not 1 <= len(profile_entries) <= 100:
                raise BrokerError("profile_entries require a confirmed bundle and 1 to 100 reviewed records")
            for entry in profile_entries:
                if not isinstance(entry, dict) or set(entry) != {"record", "expected_updated_at"}:
                    raise BrokerError("each profile entry requires record and expected_updated_at")
                raw = entry["record"]
                if not isinstance(raw, dict) or set(raw) - _RECORD_ENTRY_KEYS["profile"] or raw.get("record_type", "profile") != "profile":
                    raise BrokerError("only ordinary profile entries are allowed")
                record = self.broker._prepare_profile_record(raw)
                fid = record["field_id"]
                if fid in prepared:
                    raise BrokerError("duplicate profile entry")
                before = self.store.get("profile", fid)
                if (before or {}).get("updated_at") != entry["expected_updated_at"]:
                    raise BrokerError("profile changed; review the current value")
                self.broker._authorized(agent_id, "profile.write", fid, origin, purpose)
                _record_scope_allowed(record["allowed_origins"], record["allowed_purposes"], origin, purpose)
                expected.append(("profile", fid, before))
                prepared[fid] = record
                puts.append(("profile", fid, record))

        data = dict(data)
        refs = []
        if kind == "bundle":
            fields = data.get("fields")
            if not isinstance(fields, dict) or not 1 <= len(fields) <= 100:
                raise BrokerError("fields must map 1 to 100 semantic names to profile IDs")
            data["fields"] = {_id(semantic): _id(fid) for semantic, fid in fields.items()}
            for name in ("subject", "role", "title"):
                data[name] = _text(data.get(name), name)
            if data.get("currentness") not in {"current", "historical", "unknown"}:
                raise BrokerError("currentness must be current, historical or unknown")
            fids = sorted(set(fields.values()))
            if set(prepared) - set(fids):
                raise BrokerError("profile entries must belong to the reviewed bundle")
        else:
            bundles = data.get("bundle_refs")
            if not isinstance(bundles, list) or not 1 <= len(bundles) <= 10:
                raise BrokerError("bundle_refs must contain 1 to 10 bundle IDs")
            if len(set(map(_id, bundles))) != len(bundles):
                raise BrokerError("duplicate bundle reference")
            fids = []
            for bid in bundles:
                bundle = self.store.get(NAMESPACE, f"bundle:{bid}")
                if not bundle:
                    raise BrokerError("referenced bundle unavailable")
                if not scope_matches(bundle, origin, purpose):
                    raise BrokerError("referenced bundle is outside this context")
                refs.append(bundle)
                fids.extend(bundle["field_ids"])
                expected.append((NAMESPACE, f"bundle:{bid}", bundle))
            fids = sorted(set(fids))
            if kind == "document":
                data["title"] = _text(data.get("title"), "title")
                data["body"] = _text(data.get("body"), "body", 3000)
            elif kind == "preference":
                if len(bundles) != 1:
                    raise BrokerError("a preference selects exactly one bundle")
                for name in ("subject", "role"):
                    data[name] = _text(data.get(name), name)
                    if data[name] != refs[0]["data"][name]:
                        raise BrokerError("preference subject and role must match the confirmed bundle")
                if data.get("valid_until") is not None:
                    try:
                        data["valid_until"] = date.fromisoformat(data["valid_until"]).isoformat()
                    except (ValueError, TypeError):
                        raise BrokerError("valid_until must be an ISO date") from None
            elif data.get("outcome") not in {"used_once", "accepted", "corrected", "rejected"}:
                raise BrokerError("unknown observation outcome")
        existing_fids = [fid for fid in fids if fid not in prepared]
        snapshots = self._authorized_fields(agent_id, existing_fids, origin, purpose)
        if kind in {"bundle", "preference"}:
            self._authorized_fields(agent_id, existing_fids, origin, purpose, write=True)
        # Editing a document requires access to its old evidence too.
        if current:
            expected.extend(self._authorized_fields(agent_id, current["field_ids"], origin, purpose))
        expected.extend(snapshots)
        versions = {fid: item.get("updated_at") for _, fid, item in snapshots}
        versions.update({fid: item["updated_at"] for fid, item in prepared.items()})
        for ref in refs:
            if not self._fresh(ref):
                raise BrokerError("bundle evidence changed; review the bundle first")
        if restore_revision is not None and (old["field_versions"] != versions or old["bundle_versions"] != {ref["id"]: ref["revision"] for ref in refs}):
            raise BrokerError("historical evidence changed; cannot restore stale notes")
        record = {"id": rid, "kind": kind, "revision": expected_revision + 1,
                  "data": data, "field_ids": fids, "field_versions": versions,
                  "bundle_versions": {ref["id"]: ref["revision"] for ref in refs},
                  "target_origin": origin, "purpose": purpose, "updated_at": now_iso(),
                  "scope": scope,
                  "authority": "confirmed" if kind in {"bundle", "preference"} else "derived"}
        if current and kind == "document":
            puts.append((HISTORY, f"{key}:{current['revision']}", current))
        puts.append((NAMESPACE, key, record))
        audit = (agent_id, "context.write", key, record["updated_at"],
                 {"kind": kind, "revision": record["revision"], "field_ids": fids})
        return puts, expected, audit, {"id": rid, "kind": kind, "revision": record["revision"], "status": "stored"}

    def _fresh(self, record):
        for fid, version in record["field_versions"].items():
            item = self.store.get("profile", fid)
            if item is None or item.get("updated_at") != version:
                return False
        for bid, version in record["bundle_versions"].items():
            bundle = self.store.get(NAMESPACE, f"bundle:{bid}")
            if bundle is None or bundle["revision"] != version:
                return False
        return True

    def retrieve(self, agent_id, *, target_origin, purpose, requested_fields,
                 subject=None, role=None, selected_bundle_id=None, task_summary=None,
                 field_label=None, limit=5):
        origin = _normalize_origin(target_origin, "target_origin")
        purpose = _text(purpose, "purpose")
        if not isinstance(requested_fields, list) or not 1 <= len(requested_fields) <= 30:
            raise BrokerError("requested_fields must contain 1 to 30 semantic names")
        requested = set(map(_id, requested_fields))
        if type(limit) is not int or not 1 <= limit <= 10:
            raise BrokerError("limit must be between 1 and 10")
        if subject is not None:
            subject = _text(subject, "subject")
        if role is not None:
            role = _text(role, "role")
        if selected_bundle_id is not None:
            selected_bundle_id = _id(selected_bundle_id)
        words = set()
        if task_summary is not None:
            words = set(re.findall(r"\w+", _text(task_summary, "task_summary", 500).casefold()))
        label = normalize_form_label(_text(field_label, "field_label")) if field_label is not None else None
        visible = []
        stale_relevant = False
        # Authorize all referenced facts before exposing labels, notes or existence.
        for row in self._rows():
            if not scope_matches(row, origin, purpose):
                continue
            try:
                self._authorized_fields(agent_id, row["field_ids"], origin, purpose)
            except (AuthorizationError, BrokerError):
                continue
            if self._fresh(row):
                visible.append(row)
            elif row["kind"] in {"bundle", "preference"}:
                data = row["data"]
                if (subject is None or data.get("subject") == subject) and (role is None or data.get("role") == role):
                    stale_relevant = True
        candidates = []
        for row in visible:
            if row["kind"] != "bundle":
                continue
            data = row["data"]
            if data["currentness"] == "historical" or (subject is not None and data["subject"] != subject) or (role is not None and data["role"] != role):
                continue
            if not requested.intersection(data["fields"]):
                continue
            missing = sorted(requested - set(data["fields"]))
            candidates.append({"bundle_id": row["id"], "revision": row["revision"],
                               "title": data["title"], "subject": data["subject"], "role": data["role"],
                               "currentness": data["currentness"], "missing_fields": missing,
                               "fields": {name: fid for name, fid in data["fields"].items() if name in requested},
                               "reason": "confirmed compatible bundle"})
        preferences = {row["data"]["bundle_refs"][0] for row in visible
                       if row["kind"] == "preference" and subject is not None and role is not None
                       and row["data"]["subject"] == subject and row["data"]["role"] == role
                       and (row["data"].get("valid_until") is None or row["data"]["valid_until"] >= date.today().isoformat())}
        eligible = {c["bundle_id"] for c in candidates if not c["missing_fields"] and c["currentness"] == "current"}
        all_preferences = set(preferences)
        preferences &= eligible
        # Legacy mappings retain their exact site, purpose, label and field set. They
        # never become a preference for the rest of a bundle or another task.
        legacy = set()
        for row in self.store.db.execute("select value from kv where namespace='form-mapping'"):
            mapping = self.store._open(row["value"])
            if label and (mapping.get("target_origin"), mapping.get("purpose"), mapping.get("label")) == (origin, purpose, label):
                legacy.update(c["bundle_id"] for c in candidates
                              if c["bundle_id"] in eligible and set(mapping.get("field_ids", [])) == set(c["fields"].values()))
        chosen = None
        if selected_bundle_id is not None:
            if selected_bundle_id in eligible:
                chosen = selected_bundle_id
        elif not stale_relevant and subject not in (None, "unknown") and role not in (None, "unknown"):
            if len(all_preferences) == 1 and len(preferences) == 1:
                chosen = next(iter(preferences))
            elif not all_preferences:
                if len(legacy) == 1:
                    chosen = next(iter(legacy))
                elif not legacy and len(eligible) == 1:
                    chosen = next(iter(eligible))
        for candidate in candidates:
            if candidate["bundle_id"] in preferences:
                candidate["reason"] = "confirmed preference for this origin, purpose, subject and role"
            elif candidate["bundle_id"] in legacy:
                candidate["reason"] = "confirmed legacy mapping for this exact origin, purpose, label and field set"
        relevance = {}
        for row in visible:
            if row["kind"] == "document":
                overlap = len(words.intersection(re.findall(r"\w+", (row["data"]["title"] + " " + row["data"]["body"]).casefold())))
                for bid in row["data"]["bundle_refs"]:
                    relevance[bid] = max(overlap, relevance.get(bid, 0))
        candidates.sort(key=lambda c: (c["bundle_id"] != chosen, c["bundle_id"] not in preferences, -relevance.get(c["bundle_id"], 0), c["bundle_id"]))
        status = "ready" if chosen else "needs_clarification" if candidates else "no_authorized_context"
        if candidates and not eligible:
            status = "missing_information" if all(c["missing_fields"] for c in candidates) else "needs_clarification"
        if stale_relevant and chosen is None:
            status = "needs_review"
        shown = candidates[:limit]
        shown_ids = {c["bundle_id"] for c in shown}
        documents = [{"id": row["id"], "revision": row["revision"], "title": row["data"]["title"],
                      "body": row["data"]["body"], "bundle_refs": row["data"]["bundle_refs"],
                      "authority": "untrusted_supporting_context"} for row in visible
                     if row["kind"] == "document" and set(row["data"]["bundle_refs"]).issubset(shown_ids)][:3]
        self.store.audit(agent_id, "context.read", "context", now_iso(), {"count": len(shown)})
        return {"status": status, "selected_bundle_id": chosen, "candidates": shown,
                "has_more": len(candidates) > limit, "untrusted_notes": documents,
                "evidence_needs_review": stale_relevant,
                "next_action": "read selected fields through request_profile_fields" if chosen else
                "ask for the missing facts or one explicit subject, role or bundle choice",
                "guidance": "Notes and use history never establish facts, identity, defaults or access. No visible context does not establish that a fact is absent."}
