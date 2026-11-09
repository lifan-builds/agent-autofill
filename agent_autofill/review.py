"""Owner-operated review of ordinary profile meanings in an isolated trial.

The server never opens a live vault for writing. Browser capabilities are
ephemeral; trial data and confirmed meanings use the existing encrypted store.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .context_memory import ContextMemory
from .form_memory import _field_semantics
from .local import database_key_name, default_database_path
from .service import Broker, BrokerError, _normalize_origin, now_iso
from .storage import KeyringProvider, Store

AGENT = "service:profile-review-trial"
ORIGIN = "https://profile-review.invalid"
PURPOSE = "review saved profile meanings"
ASSETS = Path(__file__).with_name("review_assets")
MARKER = "profile-review-trial-v1"
CHOICES = {"who": {"self", "other", "unknown"},
           "role": {"home", "work", "contact", "other", "unknown"},
           "currentness": {"current", "historical", "unknown"}}


def create_trial(source=None, *, provider=None, destination=None):
    """Freeze only ordinary profile records; source connection is read-only."""
    provider = provider or KeyringProvider()
    source = (Path(source) if source else default_database_path()).resolve()
    key = provider.load_existing(database_key_name(source))
    if destination is None:
        directory = Path.home() / ".agent-autofill" / "reviews"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = directory / f"trial-{secrets.token_hex(8)}.sqlite3"
    destination = Path(destination).resolve()
    if destination.exists() or destination == source:
        raise ValueError("trial destination must be a new file")
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("pragma query_only=on")
        rows = connection.execute("select key,value from kv where namespace='profile'").fetchall()
    decoder = Store(key=key)
    try:
        records = [("profile", fid, decoder._open(value)) for fid, value in rows]
    finally:
        decoder.db.close()
    trial = Store(destination, key_provider=provider, key_name=database_key_name(destination))
    trial.put_many(records + [("review-meta", "trial", {"kind": MARKER, "created_at": now_iso(), "source": "profile snapshot"})])
    return trial, destination


def resume_trial(path, *, provider=None):
    """Validate the encrypted trial marker before opening anything writable."""
    provider = provider or KeyringProvider()
    path = Path(path).resolve()
    key = provider.load_existing(database_key_name(path))
    decoder = Store(key=key)
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
            row = connection.execute("select value from kv where namespace='review-meta' and key='trial'").fetchone()
        if not row or decoder._open(row[0]).get("kind") != MARKER:
            raise ValueError("this file is not a profile review trial")
    finally:
        decoder.db.close()
    return Store(path, key=key)


def sample_trial():
    store = Store()
    broker = Broker(store)
    samples = [
        ("home", {"full_name": "Alex Morgan", "street": "12 Cedar Lane", "city": "Portland", "state": "OR", "postcode": "97205", "country": "US", "email": "alex@example.test"}),
        ("office", {"full_name": "Alex Morgan", "street": "88 Market Street, Floor 4", "city": "Portland", "state": "OR", "postcode": "97204", "country": "US", "email": "alex@studio.example"}),
        ("previous", {"full_name": "Alex Morgan", "street": "7 Lake Road", "city": "Seattle", "state": "WA", "postcode": "98101", "country": "US"}),
        ("contact", {"full_name": "Taylor Morgan", "email": "taylor@example.test", "phone": "+1 202 555 0142"}),
    ]
    for guid, fields in samples:
        for semantic, value in fields.items():
            broker.put_profile(f"sample_{guid}_{semantic}", "profile", semantic.replace("_", " ").title(), value,
                               provenance={"source": "chrome-structured-address", "source_profile_id": "sample", "source_guid": guid, "field_type": "name" if semantic == "full_name" else semantic})
    store.put("review-meta", "trial", {"kind": MARKER, "source": "synthetic sample", "created_at": now_iso()})
    return store


def apply_trial_to_live(trial_store: Store, live_store: Store) -> dict:
    """Promote confirmed shared bundle meanings from an isolated trial to a live vault."""
    if (trial_store.get("review-meta", "trial") or {}).get("kind") != MARKER:
        raise ValueError("apply_trial_to_live requires an isolated trial")
    if (live_store.get("review-meta", "trial") or {}).get("kind") == MARKER:
        raise ValueError("destination cannot be an isolated trial")
    session = ReviewSession(trial_store)
    bundles = [
        row
        for row in session.memory._rows()
        if row.get("kind") == "bundle"
        and row.get("scope") == "shared"
        and row.get("authority") == "confirmed"
        and session.memory._fresh(row)
    ]
    if not bundles:
        raise BrokerError("No confirmed group meanings to apply.")
    puts, expected, audits = [], [], []
    seen_fields: set[str] = set()
    for row in bundles:
        for fid, version in row.get("field_versions", {}).items():
            trial_profile = trial_store.get("profile", fid)
            live_profile = live_store.get("profile", fid)
            if (
                trial_profile is None
                or live_profile is None
                or live_profile.get("updated_at") != version
                or live_profile != trial_profile
            ):
                raise BrokerError(
                    "Live vault changed since the trial snapshot. Create a new snapshot and review again."
                )
            if fid not in seen_fields:
                seen_fields.add(fid)
                expected.append(("profile", fid, live_profile))
        key = f"bundle:{row['id']}"
        live_current = live_store.get("context", key)
        expected.append(("context", key, live_current))
        next_revision = ((live_current or {}).get("revision", 0)) + 1
        timestamp = now_iso()
        promoted = {
            **row,
            "revision": next_revision,
            "updated_at": timestamp,
        }
        puts.append(("context", key, promoted))
        audits.append(
            (
                "owner:review-apply",
                "context.write",
                key,
                timestamp,
                {
                    "kind": "bundle",
                    "revision": next_revision,
                    "field_ids": row["field_ids"],
                },
            )
        )
    try:
        live_store.apply_changes(puts, [], expected=expected, audits=audits)
    except ValueError:
        raise BrokerError(
            "Live vault changed since the trial snapshot. Create a new snapshot and review again."
        ) from None
    return {"applied_groups": len(puts), "live_vault_changed": True}


class ReviewSession:
    def __init__(self, store):
        if (store.get("review-meta", "trial") or {}).get("kind") != MARKER:
            raise ValueError("review requires an isolated trial")
        self.store = store
        self.broker = Broker(store)
        self.broker.create_agent(AGENT)
        self.broker.enable_trusted_local(AGENT)
        self.memory = ContextMemory(self.broker)
        self.signing_key = secrets.token_bytes(32)

    def _group(self, group, index):
        fid_list = group["field_ids"]
        originals = {fid: self.store.get("profile", fid) for fid in fid_list}
        values = self.broker.request_profile_fields(AGENT, fid_list, ORIGIN, PURPOSE, group_id=group["group_id"])
        if any(originals[fid] is None or values[fid]["value"] != originals[fid]["value"] or self.store.get("profile", fid) != originals[fid] for fid in fid_list):
            raise BrokerError("A group changed. Reload and review before saving again.")
        mapping, fields, repeated = {}, [], False
        for position, fid in enumerate(fid_list):
            record = self.broker._profile_defaults(fid, originals[fid])
            meanings = sorted(_field_semantics(record))
            semantic = meanings[0] if len(meanings) == 1 else f"field_{position}"
            if semantic in mapping:
                repeated = True
                semantic = f"field_{position}"
            mapping[semantic] = fid
            fields.append({"semantic": semantic, "label": record["display_name"], "value": values[fid]["value"]})
        bid = "review_" + group["group_id"]
        current = self.store.get("context", f"bundle:{bid}")
        data = (current or {}).get("data", {})
        revision = hmac.new(self.signing_key, json.dumps([originals, current], sort_keys=True).encode(), hashlib.sha256).hexdigest()
        origins = [{_normalize_origin(value, "origin") for value in item.get("allowed_origins", [])}
                   for item in originals.values() if item.get("allowed_origins")]
        purposes = [set(item["allowed_purposes"]) for item in originals.values() if item.get("allowed_purposes")]
        common_origins = set.intersection(*origins) if origins else {ORIGIN}
        common_purposes = set.intersection(*purposes) if purposes else {PURPOSE}
        blocked = "Multiple fields have the same meaning. Leave this group for a closer review." if repeated else None
        if not common_origins or not common_purposes:
            blocked = "These fields have different access scopes. Review them separately."
        subject = data.get("subject", "unknown")
        who = "self" if subject == "self" else "unknown" if subject == "unknown" else "other"
        public = {"id": group["group_id"], "letter": _letter(index), "fields": fields,
                  "who": who, "role": data.get("role", "unknown"),
                  "currentness": data.get("currentness", "unknown"), "revision": revision,
                  "reviewed": current is not None and self.memory._fresh(current), "blocked": blocked,
                  "has_address": bool(set(mapping) & {"street", "city", "state", "postcode", "country"}), "has_contact": bool(set(mapping) & {"email", "phone", "full_name"})}
        origin = current["target_origin"] if current else min(common_origins) if common_origins else ORIGIN
        purpose = current["purpose"] if current else min(common_purposes) if common_purposes else PURPOSE
        return public, mapping, current, origin, purpose, originals

    def state(self):
        groups = [self._group(group, index)[0] for index, group in enumerate(self.broker.list_profile_groups())]
        return {"source": self.store.get("review-meta", "trial")["source"], "groups": groups,
                "field_count": self.store.count("profile"), "reviewed_count": sum(group["reviewed"] for group in groups)}

    def confirm(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"confirmed", "changes"} or payload["confirmed"] is not True:
            raise BrokerError("Review the exact changes before confirming.")
        changes = payload["changes"]
        if not isinstance(changes, list) or not 1 <= len(changes) <= 100:
            raise BrokerError("Choose between 1 and 100 groups.")
        groups = {group["group_id"]: (group, i) for i, group in enumerate(self.broker.list_profile_groups())}
        puts, expected, audits, seen = [], [], [], set()
        for change in changes:
            if not isinstance(change, dict) or set(change) != {"id", "revision", *CHOICES}:
                raise BrokerError("Only group meanings can be changed here.")
            gid = change["id"]
            if not isinstance(gid, str) or gid not in groups or gid in seen:
                raise BrokerError("Unknown or duplicate group.")
            seen.add(gid)
            for key, options in CHOICES.items():
                if not isinstance(change[key], str) or change[key] not in options:
                    raise BrokerError("Choose a listed meaning or leave it unknown.")
            public, fields, current, origin, purpose, originals = self._group(*groups[gid])
            if public["blocked"]:
                raise BrokerError(public["blocked"])
            if not isinstance(change["revision"], str) or not hmac.compare_digest(public["revision"], change["revision"]):
                raise BrokerError("A group changed since you reviewed it. Reload and review the current version.")
            # These short-lived grants exist only inside the isolated trial.
            for fid in fields.values():
                self.broker.grant(AGENT, "profile.write", fid, origin, purpose)
            who, role, status = (change[key] for key in ("who", "role", "currentness"))
            subject = f"other:{gid}" if who == "other" else who
            data = {"fields": fields, "subject": subject, "role": role, "currentness": status,
                    "title": f"{'Your' if who == 'self' else 'Another person’s' if who == 'other' else 'Unassigned'} {role if role != 'unknown' else 'details'}"}
            staged, checks, audit, _ = self.memory._prepare_write(
                AGENT, kind="bundle", record_id="review_" + gid, data=data,
                expected_revision=(current or {}).get("revision", 0), target_origin=origin,
                purpose=purpose, confirmed=True, shared=True,
            )
            puts.extend(staged)
            expected.extend(("profile", fid, record) for fid, record in originals.items())
            expected.extend(checks)
            audits.append(audit)
        try:
            self.store.apply_changes(puts, [], expected=expected, audits=audits)
        except ValueError:
            raise BrokerError("A group changed. Reload and review before saving again.") from None
        return {"saved": len(seen), "state": self.state()}


def _letter(index):
    out = ""
    while index >= 0:
        out = chr(65 + index % 26) + out
        index = index // 26 - 1
    return out


def make_server(session, *, port=0):
    capability = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, body, content_type="application/json", *, headers=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def authorized(self):
            return self.valid_host() and secrets.compare_digest(self.headers.get("X-Review-Token", ""), capability)

        def do_GET(self):
            if not self.valid_host():
                self.reply(403, b'{"error":"This review is local to this device."}')
                return
            url = urlsplit(self.path)
            files = {"/": ("index.html", "text/html; charset=utf-8"),
                     "/review.css": ("review.css", "text/css"), "/review.js": ("review.js", "application/javascript")}
            if url.path in files:
                filename, kind = files[url.path]
                self.reply(200, (ASSETS / filename).read_bytes(), kind)
            elif url.path == "/api/state":
                if not self.authorized():
                    self.reply(403, b'{"error":"Reopen this review from the app to continue."}')
                    return
                try:
                    self.reply(200, json.dumps(session.state()).encode())
                except Exception:  # noqa: BLE001 - never return provider data in error text
                    self.reply(500, b'{"error":"Could not load the trial. Your live vault is unchanged."}')
            else:
                self.reply(404, b"{}")

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            body = self.rfile.read(length) if 0 < length <= 65536 else b""
            expected_origin = f"http://127.0.0.1:{self.server.server_port}"
            if not self.authorized() or self.headers.get("Origin") != expected_origin or self.headers.get("X-Review-Action") != "confirm":
                self.reply(403, b'{"error":"Reopen the review and confirm from this page."}')
                return
            if self.path != "/api/confirm":
                self.reply(404, b"{}")
                return
            try:
                if not 0 < length <= 65536 or self.headers.get("Content-Type") != "application/json":
                    raise ValueError("invalid request")
                payload = json.loads(body)
                result = session.confirm(payload)
            except BrokerError as exc:
                # Only the fixed public validation messages from this workflow.
                message = str(exc) if str(exc) in {
                    "Review the exact changes before confirming.", "Choose between 1 and 100 groups.",
                    "Only group meanings can be changed here.", "Unknown or duplicate group.",
                    "Choose a listed meaning or leave it unknown.",
                    "A group changed since you reviewed it. Reload and review the current version.",
                    "A group changed. Reload and review before saving again.",
                } else "This change needs a closer review. Nothing was saved."
                self.reply(409, json.dumps({"error": message}).encode())
                return
            except Exception:  # noqa: BLE001 - no private values in errors
                self.reply(400, b'{"error":"Could not save these changes. Reload and try again."}')
                return
            self.reply(200, json.dumps(result).encode())

    server = HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 30
    return server, f"http://127.0.0.1:{server.server_port}/#access={capability}"
