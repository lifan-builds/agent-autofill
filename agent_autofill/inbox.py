"""Small loopback-only review surface for the broker.

The first view is deliberately a value-free overview. It answers what is
stored and what needs attention without making the operator read raw grant or
audit JSON. Full metadata remains available in collapsed advanced sections
for troubleshooting.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

from .service import Broker


def _grant_counts(grants: list[dict]) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    result = {"active": 0, "revoked": 0, "expired": 0, "expiring_24h": 0}
    for grant in grants:
        if grant.get("revoked"):
            result["revoked"] += 1
            continue
        try:
            expires_at = datetime.fromisoformat(str(grant.get("expires_at")))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            result["expired"] += 1
            continue
        if expires_at <= now:
            result["expired"] += 1
        else:
            result["active"] += 1
            if expires_at - now <= timedelta(hours=24):
                result["expiring_24h"] += 1
    return result


def _source_overview(broker: Broker, sources: list[dict]) -> list[dict[str, str]]:
    unavailable: set[str] = getattr(broker, "unavailable_email_sources", set())
    email_adapters = getattr(broker, "email_adapters", {})
    message_adapters = getattr(broker, "message_adapters", {})
    result = []
    for source in sources:
        source_id = source.get("source_id")
        source_key = str(source_id) if source_id is not None else ""
        kind = str(source.get("kind", "unknown"))
        status = "configured"
        detail = "Configured; verify before use"
        if kind == "email":
            if source_key in unavailable:
                status = "needs attention"
                detail = "Unavailable; run the read-only email check"
            elif source_key in email_adapters:
                status = "ready"
                detail = "Read-only adapter connected"
            elif broker.store.get("connection", source_key):
                detail = "Saved connector; run the read-only email check"
            else:
                status = "needs attention"
                detail = "No connector configuration found"
        elif kind == "messages":
            if source_key in message_adapters:
                status = "ready"
                detail = "Read-only adapter connected"
            else:
                detail = "Configured; open the local source before reading"
        result.append(
            {
                "label": str(source.get("label") or kind.title()),
                "kind": kind,
                "status": status,
                "detail": detail,
            }
        )
    return result


def _attention_items(
    sources: list[dict],
    source_overview: list[dict[str, str]],
    profile_fields: list[dict],
    credentials: list[dict],
    financial_records: list[dict],
    agents: list[dict],
    grants: list[dict],
) -> list[str]:
    attention: list[str] = []
    if not sources:
        attention.append("No local sources are configured. Run quickstart or setup to connect one.")
    for source in source_overview:
        if source["status"] == "needs attention":
            attention.append(f"{source['label']}: {source['detail']}.")
    if not profile_fields:
        attention.append("No profile fields are stored. Add one with profile add when needed.")
    if not credentials:
        attention.append("No website credentials are stored. Add them only through an approved local flow.")
    if not financial_records:
        attention.append("No financial metadata is stored. This is optional for ordinary profile fills.")
    if not agents:
        attention.append("No agent identity is configured. Create one before pairing a consumer.")
    revoked_agents = sum(bool(agent.get("revoked")) for agent in agents)
    if revoked_agents:
        attention.append(f"{revoked_agents} agent identity(ies) are revoked; review the agent list.")
    counts = _grant_counts(grants)
    if counts["revoked"] or counts["expired"]:
        attention.append(
            f"Review {counts['revoked']} revoked and {counts['expired']} expired grant(s)."
        )
    if counts["expiring_24h"]:
        attention.append(
            f"{counts['expiring_24h']} grant(s) expire within 24 hours; renew only if still needed."
        )
    return attention


def build_html(broker: Broker) -> str:
    """Render the metadata-only Agent Inbox page."""

    sources = broker.list_sources()
    profile_fields = broker.list_profile_fields()
    profile_groups = broker.list_profile_groups()
    credentials = broker.list_credentials()
    financial_records = broker.list_financial_records()
    agents = broker.list_agents()
    grants = broker.list_grants()
    recent_audit = broker.store.audits()[-50:]
    source_overview = _source_overview(broker, sources)
    grant_counts = _grant_counts(grants)
    attention = _attention_items(
        sources,
        source_overview,
        profile_fields,
        credentials,
        financial_records,
        agents,
        grants,
    )
    summary = {
        "sources": sources,
        "profile_fields": profile_fields,
        "profile_groups": profile_groups,
        "credentials": credentials,
        "financial_records": financial_records,
        "agents": agents,
        "grants": grants,
        "recent_audit": recent_audit,
    }

    def cell(value: object) -> str:
        if isinstance(value, (dict, list, tuple)):
            value = json.dumps(value, sort_keys=True, default=str)
        return escape(str(value))

    def table(title: str, rows: list[dict], columns: tuple[str, ...]) -> str:
        heading = f"<h3>{escape(title)} <small>{len(rows)}</small></h3>"
        if not rows:
            return f"<section>{heading}<p class=empty>None configured.</p></section>"
        headers = "".join(f"<th>{escape(column)}</th>" for column in columns)
        body = "".join(
            "<tr>"
            + "".join(f"<td>{cell(row.get(column, ''))}</td>" for column in columns)
            + "</tr>"
            for row in rows
        )
        return (
            f"<section>{heading}<div class=scroll><table><thead><tr>{headers}"
            f"</tr></thead><tbody>{body}</tbody></table></div></section>"
        )

    def list_items(items: list[str]) -> str:
        if not items:
            return '<p class=empty>Nothing needs attention.</p>'
        return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"

    def overview_card(
        title: str,
        icon: str,
        count: int,
        detail: str,
        tone: str = "blue",
        items: list[str] | None = None,
    ) -> str:
        """Render a compact, value-free card for the first dashboard view."""

        content = f'<strong class="card-count">{count}</strong><p>{escape(detail)}</p>'
        if items:
            content += '<ul class="compact">' + "".join(
                f"<li>{escape(item)}</li>" for item in items[:3]
            ) + "</ul>"
        return (
            f'<section class="overview-card tone-{escape(tone)}" aria-label="{escape(title)}">'
            f'<div class="card-heading"><span class="card-icon" aria-hidden="true">{escape(icon)}</span>'
            f'<h3>{escape(title)}</h3></div>{content}</section>'
        )

    source_lines = [f"{item['label']} — {item['status']}" for item in source_overview]
    profile_lines = [
        f"{item.get('group_id', 'Profile group')} · {item.get('field_count', 0)} fields"
        for item in profile_groups
    ]
    field_lines = [
        f"{item.get('display_name', item.get('field_id', 'Profile field'))} · {item.get('category', 'custom')}"
        for item in profile_fields
    ]
    credential_lines = [
        f"{item.get('name', 'Credential')} · {item.get('username', '')}"
        for item in credentials
    ]
    financial_lines = [
        f"{item.get('display_name', 'Financial record')} · {item.get('kind', 'record')}"
        for item in financial_records
    ]
    agent_lines = [
        f"{item.get('agent_id', 'Agent')} · {'revoked' if item.get('revoked') else item.get('access_mode', 'configured')}"
        for item in agents
    ]
    raw = escape(json.dumps(summary, indent=2, default=str))
    cards = "".join(
        (
            overview_card("Sources", "◉", len(sources), "Local sources", "mint", source_lines),
            overview_card(
                "Profile groups", "✦", len(profile_groups), "Saved identity and address groups", "violet", profile_lines
            ),
            overview_card("Profile fields", "⌁", len(profile_fields), "Saved profile metadata", "blue", field_lines),
            overview_card("Credentials", "▣", len(credentials), "Website credential metadata", "amber", credential_lines),
            overview_card(
                "Financial metadata", "◇", len(financial_records), "Payment and financial record metadata", "rose", financial_lines
            ),
            overview_card("Agents", "◎", len(agents), "Configured local consumers", "blue", agent_lines),
            overview_card(
                "Expiring grants",
                "↗",
                grant_counts["expiring_24h"],
                f"{grant_counts['active']} active · {grant_counts['revoked']} revoked · {grant_counts['expired']} expired",
                "amber",
            ),
            overview_card("Recent activity", "✧", len(recent_audit), "Recent redacted audit events", "mint"),
        )
    )
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content=width=device-width,initial-scale=1>"
        "<title>Agent Inbox · Local vault</title>"
        "<style>"
        ":root{color-scheme:light;--ink:#172033;--muted:#68758a;--line:#e6eaf2;--panel:#fff;--canvas:#f5f7fb;"
        "--blue:#5367e8;--blue-soft:#eef0ff;--mint:#0a9674;--mint-soft:#e6f8f2;--violet:#8a5bd4;"
        "--violet-soft:#f4edff;--amber:#b96d00;--amber-soft:#fff4df;--rose:#d34d70;--rose-soft:#fff0f4}"
        "*{box-sizing:border-box}body{font:14px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,\"Segoe UI\",sans-serif;"
        "max-width:1240px;margin:0 auto;padding:2.5rem 1.25rem 4rem;background:radial-gradient(circle at 92% 0,#e7ebff 0,transparent 27rem),"
        "var(--canvas);color:var(--ink)}h1,h2,h3,p{margin-top:0}h1{font-size:clamp(2rem,4vw,3rem);letter-spacing:-.055em;line-height:1.05;margin-bottom:.55rem}"
        "h2{font-size:1.15rem;letter-spacing:-.02em;margin:0 0 .75rem}h3{font-size:.94rem;margin:0}"
        ".page-header{display:flex;align-items:flex-start;justify-content:space-between;gap:1.5rem;margin-bottom:1.5rem}"
        ".eyebrow{color:var(--blue);font-size:.73rem;font-weight:800;letter-spacing:.13em;text-transform:uppercase;margin-bottom:.7rem}"
        ".subtitle,.muted,.empty{color:var(--muted)}.subtitle{font-size:1rem;max-width:42rem;margin-bottom:0}"
        ".trust-pill{display:inline-flex;align-items:center;gap:.45rem;border:1px solid #b7c0ff;background:#fff;padding:.55rem .75rem;"
        "border-radius:999px;color:#3e51c6;font-size:.78rem;font-weight:750;white-space:nowrap;box-shadow:0 5px 18px #dfe4ff}"
        ".spark{font-size:1rem}.hero{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(230px,1fr);gap:1rem;margin:1.6rem 0 2rem}"
        ".hero-panel,.section-panel,.overview-card,details{background:rgba(255,255,255,.9);border:1px solid rgba(215,221,235,.85);border-radius:18px;"
        "box-shadow:0 12px 35px rgba(54,67,105,.08)}.hero-panel{padding:1.35rem 1.45rem;position:relative;overflow:hidden}"
        ".hero-panel:after{content:\"✦\";position:absolute;right:1.5rem;top:-.8rem;color:#dbe0ff;font-size:7rem;line-height:1}"
        ".hero-panel h2{font-size:1.5rem;margin-bottom:.35rem}.hero-panel p{max-width:38rem;color:var(--muted);margin-bottom:1rem}"
        ".hero-tags{display:flex;flex-wrap:wrap;gap:.5rem}.tag{display:inline-flex;align-items:center;gap:.35rem;border-radius:999px;padding:.35rem .6rem;font-size:.73rem;font-weight:750;"
        "background:#f2f4f9;color:#4f5b70}.tag.green{background:var(--mint-soft);color:#08755c}.tag.purple{background:var(--violet-soft);color:#7041bb}"
        ".hero-stat{padding:1.35rem;background:linear-gradient(145deg,#1d2750,#4c5cdd);color:#fff;border:0;position:relative;overflow:hidden}"
        ".hero-stat:before{content:\"\";position:absolute;width:12rem;height:12rem;border-radius:50%;right:-4rem;top:-4rem;background:rgba(255,255,255,.14)}"
        ".hero-stat .stat-label{color:#d8dcff;font-size:.75rem;font-weight:750;letter-spacing:.08em;text-transform:uppercase}.hero-stat strong{display:block;font-size:2.7rem;letter-spacing:-.06em;line-height:1;margin:.7rem 0 .25rem}.hero-stat p{color:#e9ebff;margin:0;font-size:.82rem}"
        ".section-kicker{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin:0 0 .75rem}.section-kicker p{color:var(--muted);font-size:.8rem;margin:0}"
        ".cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:.8rem;margin-bottom:1.6rem}.overview-card{padding:1rem;min-height:145px;transition:transform .16s ease,box-shadow .16s ease}"
        ".overview-card:hover{transform:translateY(-2px);box-shadow:0 16px 35px rgba(54,67,105,.13)}.card-heading{display:flex;align-items:center;gap:.55rem;margin-bottom:.75rem}"
        ".card-icon{display:grid;place-items:center;width:2rem;height:2rem;border-radius:10px;font-size:1.1rem;font-weight:800}.card-count{display:block;font-size:2rem;letter-spacing:-.06em;line-height:1}.overview-card p{margin:.3rem 0 0;color:var(--muted);font-size:.8rem}.overview-card ul{margin:.7rem 0 0;padding-left:1rem}.compact{font-size:.76rem;color:#4f5b70}.compact li{margin:.15rem 0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.tone-blue .card-icon{background:var(--blue-soft);color:var(--blue)}.tone-mint .card-icon{background:var(--mint-soft);color:var(--mint)}.tone-violet .card-icon{background:var(--violet-soft);color:var(--violet)}.tone-amber .card-icon{background:var(--amber-soft);color:var(--amber)}.tone-rose .card-icon{background:var(--rose-soft);color:var(--rose)}"
        ".section-panel{padding:1.15rem 1.25rem;margin-top:1rem}.attention{border-left:4px solid #d38a16;background:linear-gradient(105deg,#fffaf0,#fff)}.attention ul{margin:.25rem 0 0;padding-left:1.25rem}.attention li{margin:.35rem 0;color:#5f4a22}.attention .empty{margin:0}.boundary{border-left:4px solid var(--blue);background:linear-gradient(105deg,#f7f8ff,#fff)}"
        ".delivery-options{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:.8rem}.delivery-option{border:1px solid var(--line);border-radius:14px;padding:1rem;background:#fbfcff;position:relative}.delivery-option:before{content:\"\";display:block;width:2.2rem;height:3px;border-radius:3px;background:var(--blue);margin-bottom:.75rem}.delivery-option.extension:before{background:var(--mint)}.delivery-option strong{font-size:.95rem}.delivery-option p{margin:.35rem 0 0;color:var(--muted);font-size:.82rem}.delivery-option .tag{margin-top:.75rem}"
        ".steps{display:grid;grid-template-columns:repeat(3,1fr);gap:.75rem;margin-top:1rem}.step{display:flex;gap:.7rem;align-items:flex-start}.step-number{flex:none;display:grid;place-items:center;width:1.55rem;height:1.55rem;border-radius:50%;background:var(--blue-soft);color:var(--blue);font-size:.75rem;font-weight:800}.step strong{display:block;font-size:.82rem}.step span{display:block;color:var(--muted);font-size:.76rem;margin-top:.15rem}.advanced-title{margin-top:2rem}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:1rem}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:.82rem}th,td{text-align:left;vertical-align:top;border-bottom:1px solid var(--line);padding:.5rem}th{color:var(--muted);font-weight:700;white-space:nowrap}td{max-width:280px;word-break:break-word}details{margin-top:1rem;padding:1rem 1.15rem}summary{cursor:pointer;font-weight:750;list-style:none}summary::-webkit-details-marker{display:none}summary:before{content:\"＋\";display:inline-block;margin-right:.45rem;color:var(--blue)}details[open] summary:before{content:\"−\"}pre{white-space:pre-wrap;overflow:auto;max-height:26rem;margin:.8rem 0 0;padding:.85rem;background:#f6f8fc;border-radius:10px;color:#4d5870;font-size:.75rem}"
        "@media(max-width:700px){body{padding:1.5rem .85rem 3rem}.page-header{display:block}.trust-pill{margin-top:1rem}.hero{grid-template-columns:1fr}.steps{grid-template-columns:1fr}.grid{grid-template-columns:1fr}}"
        "</style></head><body>"
        '<header class="page-header"><div><div class="eyebrow">✦ Private local workspace</div><h1>Agent Inbox</h1><p class="subtitle">A read-only dashboard for what is stored and what needs attention. Agent workflows stay in the CLI.</p></div><div class="trust-pill"><span class="spark">✧</span> Values stay hidden in this view</div></header>'
        '<div class="hero"><section class="hero-panel"><div class="eyebrow">A calmer way to review autofill</div><h2>Everything important, at a glance.</h2><p>Metadata is organized into small, reviewable cards. Passwords, tokens, card numbers, and one-time codes are intentionally never rendered here. Use the CLI when an agent needs information.</p><div class="hero-tags"><span class="tag green">● Loopback only</span><span class="tag">▣ Encrypted vault</span><span class="tag purple">✦ CLI is the agent surface</span></div></section>'
        f'<section class="hero-stat"><span class="stat-label">Ready to review</span><strong>{len(sources) + len(profile_fields) + len(credentials) + len(financial_records)}</strong><p>safe metadata records across your local vault</p></section></div>'
        '<div class="section-kicker"><h2>What is stored</h2><p>Counts and labels only · no secret values</p></div>'
        f'<div class="cards">{cards}</div>'
        f'<section class="section-panel attention"><h2>Needs attention</h2>{list_items(attention)}</section>'
        '<section class="section-panel"><div class="section-kicker"><h2>Agent workflow</h2><p>This dashboard explains policy; the CLI performs the information work.</p></div><div class="delivery-options">'
        '<div class="delivery-option"><strong>Direct CLI delivery</strong><p>The authenticated acting agent requests approved fields through the CLI/MCP path. It owns navigation, submission, and any follow-up verification.</p><span class="tag">Agent sees approved fields</span></div>'
        '<div class="delivery-option extension"><strong>Optional bridge delivery</strong><p>The CLI can request a one-time, origin-bound handle for the local extension bridge. The password stays out of agent input.</p><span class="tag green">Transport helper only</span></div>'
        '</div><div class="steps"><div class="step"><span class="step-number">1</span><div><strong>Review here</strong><span>Check stored metadata and attention items.</span></div></div><div class="step"><span class="step-number">2</span><div><strong>Use the CLI</strong><span>Match, grant, and request the exact information.</span></div></div><div class="step"><span class="step-number">3</span><div><strong>Act elsewhere</strong><span>The acting agent decides what happens next.</span></div></div></div></section>'
        '<section class="section-panel boundary"><h2>Consent and verification boundary</h2><p class="muted">The exact-origin and purpose grant preview is the consent gate. The extension handle is a scoped, one-time verification handoff, not a reusable link or proof that the site accepted anything. Agent Autofill does not navigate, submit, or verify the result after delivery; review the page and submit it yourself.</p></section>'
        '<h2 class="advanced-title">Advanced details</h2>'
        "<details><summary>Metadata tables</summary><div class=grid>"
        + table("Sources", sources, ("source_id", "kind", "label"))
        + table("Agents", agents, ("agent_id", "created_at", "access_mode", "revoked"))
        + table(
            "Grants",
            grants,
            (
                "agent_id",
                "operation",
                "resource_scope",
                "target_origin",
                "purpose",
                "expires_at",
                "revoked",
            ),
        )
        + table(
            "Profile groups",
            profile_groups,
            ("group_id", "source", "source_profile_id", "field_count", "field_types"),
        )
        + table(
            "Profile fields",
            profile_fields,
            (
                "field_id",
                "category",
                "display_name",
                "sensitivity",
                "allowed_origins",
                "allowed_purposes",
                "updated_at",
            ),
        )
        + table(
            "Credentials",
            credentials,
            ("credential_id", "name", "origin", "username", "provenance"),
        )
        + table(
            "Financial records",
            financial_records,
            ("record_id", "kind", "display_name", "field_ids", "matching_hints"),
        )
        + "</div></details>"
        "<details><summary>Recent audit metadata</summary><pre>"
        + escape(json.dumps(recent_audit, indent=2, default=str))
        + "</pre></details>"
        "<details><summary>Machine-readable metadata</summary><pre>"
        + raw
        + "</pre></details>"
    )


def serve(broker: Broker, host: str = "127.0.0.1", port: int = 8765) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("inbox-web only permits loopback hosts")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/":
                self.send_error(404)
                return
            payload = build_html(broker).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            return

    HTTPServer((host, port), Handler).serve_forever()
