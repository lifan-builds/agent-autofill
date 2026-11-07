"""Small loopback-only review surface for the broker.

The first view is deliberately a value-free overview. It answers what is
stored and what needs attention without making the operator read raw grant or
audit JSON. Full metadata remains available in collapsed advanced sections
for troubleshooting.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer

from .form_policy import get_form_submission_policy
from .service import Broker


def _grant_counts(grants: list[dict]) -> dict[str, int]:
    now = datetime.now(UTC)
    result = {"active": 0, "revoked": 0, "expired": 0, "expiring_24h": 0}
    for grant in grants:
        if grant.get("revoked"):
            result["revoked"] += 1
            continue
        try:
            expires_at = datetime.fromisoformat(str(grant.get("expires_at")))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
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
    for source in source_overview:
        if source["status"] == "needs attention":
            attention.append(f"{source['label']}: {source['detail']}.")
    if not any(not agent.get("revoked") for agent in agents):
        attention.append("No agent identity is configured. Create one before pairing a consumer.")
    return attention


def build_html(broker: Broker) -> str:
    """Render the metadata-only Agent Inbox page."""

    sources = list(broker.sources.values())
    profile_fields = broker.list_profile_fields()
    profile_groups = broker.list_profile_groups()
    credentials = broker.list_credentials()
    financial_records = broker.list_financial_records()
    record_counts = {
        "Sources": len(sources),
        "Profile fields": broker.store.count("profile"),
        "Context records": broker.store.count("context"),
        "Credentials": broker.store.count("credential"),
        "Financial records": broker.store.count("financial"),
    }
    from .context_memory import ContextMemory

    context_memory = ContextMemory(broker)
    context_rows = [
        {"kind": row["kind"], "revision": row["revision"],
         "fields": len(row["field_ids"]),
         "status": "Current evidence" if context_memory._fresh(row) else "Review changed evidence"}
        for row in context_memory._rows()
    ]
    context_counts = {kind: sum(row["kind"] == kind for row in context_rows)
                      for kind in ("bundle", "document", "preference", "observation")}
    context_review_count = sum(row["status"] == "Review changed evidence" for row in context_rows)
    agents = broker.list_agents()
    grants = broker.list_grants()
    recent_audit = broker.store.audits()[-50:]
    submission_policy = get_form_submission_policy(broker)
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
        "record_counts": record_counts,
        "metadata_preview_limit": 100,
        "sources": sources,
        "profile_fields": profile_fields,
        "profile_groups": profile_groups,
        "context_counts": context_counts,
        "credentials": credentials,
        "financial_records": financial_records,
        "agents": agents,
        "grants": grants,
        "recent_audit": recent_audit,
        "form_submission": submission_policy.to_dict(),
    }

    def cell(value: object) -> str:
        if isinstance(value, (dict, list, tuple)):
            value = json.dumps(value, sort_keys=True, default=str)
        return escape(str(value))

    def table(title: str, rows: list[dict], columns: tuple[str, ...]) -> str:
        total = record_counts.get(title, len(rows))
        heading = f"<h3>{escape(title)} <small>{total}</small></h3>"
        if total > len(rows):
            heading += (
                f'<p class="muted">Showing {len(rows)} of {total}. '
                'Use the CLI list command with <code>--offset 100</code> '
                'to continue; this page is a preview.</p>'
            )
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
        f"{', '.join(item.get('field_types', [])[:2]) or 'Profile group'} · {item.get('field_count', 0)} fields"
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
            overview_card("Profile fields", "⌁", record_counts["Profile fields"], "Saved profile metadata", "blue", field_lines),
            overview_card("Credentials", "▣", record_counts["Credentials"], "Website credential metadata", "amber", credential_lines),
            overview_card(
                "Financial metadata", "◇", record_counts["Financial records"], "Payment and financial record metadata", "rose", financial_lines
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
        ".hero-panel,.hero-stat,.section-panel,.overview-card,details{background:rgba(255,255,255,.9);border:1px solid rgba(215,221,235,.85);border-radius:18px;"
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
        ".matching-flow{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.75rem}.matching-step{border:1px solid var(--line);border-radius:14px;padding:.9rem;background:#fbfcff}.matching-step strong{display:block;font-size:.86rem}.matching-step p{margin:.3rem 0 0;color:var(--muted);font-size:.78rem}.matching-step .tag{margin-top:.65rem}"
        ".steps{display:grid;grid-template-columns:repeat(3,1fr);gap:.75rem;margin-top:1rem}.step{display:flex;gap:.7rem;align-items:flex-start}.step-number{flex:none;display:grid;place-items:center;width:1.55rem;height:1.55rem;border-radius:50%;background:var(--blue-soft);color:var(--blue);font-size:.75rem;font-weight:800}.step strong{display:block;font-size:.82rem}.step span{display:block;color:var(--muted);font-size:.76rem;margin-top:.15rem}.advanced-title{margin-top:2rem}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:1rem}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:.82rem}th,td{text-align:left;vertical-align:top;border-bottom:1px solid var(--line);padding:.5rem}th{color:var(--muted);font-weight:700;white-space:nowrap}td{max-width:280px;word-break:break-word}details{margin-top:1rem;padding:1rem 1.15rem}summary{cursor:pointer;font-weight:750;list-style:none}summary::-webkit-details-marker{display:none}summary:before{content:\"＋\";display:inline-block;margin-right:.45rem;color:var(--blue)}details[open] summary:before{content:\"−\"}pre{white-space:pre-wrap;overflow:auto;max-height:26rem;margin:.8rem 0 0;padding:.85rem;background:#f6f8fc;border-radius:10px;color:#4d5870;font-size:.75rem}"
        "nav{display:flex;flex-wrap:wrap;gap:.65rem;margin:1.25rem 0}a{color:#3e51c6;text-underline-offset:3px}a:focus-visible,summary:focus-visible{outline:3px solid #5367e8;outline-offset:4px}code{overflow-wrap:anywhere}.section-panel{margin-bottom:1rem}.delivery-options{grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr))}"
        "@media(max-width:700px){.hero-panel:after{display:none}body{padding:1.5rem .85rem 3rem}.page-header{display:block}.trust-pill{margin-top:1rem}.hero{grid-template-columns:1fr}.steps{grid-template-columns:1fr}.grid{grid-template-columns:1fr}}"
        "</style></head><body>"
        '<header class="page-header"><div><div class="eyebrow">✦ Private local workspace</div><h1>Agent Inbox</h1><p class="subtitle">A read-only dashboard for what is stored and what needs attention. Agent workflows stay in the CLI.</p></div><div class="trust-pill"><span class="spark">✧</span> Values stay hidden in this view</div></header>'
        '<div class="hero"><section class="hero-panel"><div class="eyebrow">A calmer way to review autofill</div><h2>Everything important, at a glance.</h2><p>Metadata is organized into small, reviewable cards. Passwords, tokens, card numbers, and one-time codes are intentionally never rendered here. Use the CLI when an agent needs information.</p><div class="hero-tags"><span class="tag green">● Loopback only</span><span class="tag">▣ Encrypted vault</span><span class="tag purple">✦ CLI is the agent surface</span></div></section>'
        f'<section class="hero-stat"><span class="stat-label">Saved records</span><strong>{sum(record_counts.values())}</strong><p>records across your local vault</p></section></div>'
        f'<section class="section-panel attention"><h2>Needs attention</h2>{list_items(attention)}</section>'
        '<nav aria-label="Dashboard sections"><a href="#inventory">Stored data</a> · <a href="#next-task">Next task</a> · <a href="#access">Agent access</a> · <a href="/">Refresh dashboard</a></nav>'
        '<div id="inventory"></div>'
        '<div class="section-kicker"><h2>What is stored</h2><p>Counts and labels only · no secret values</p></div>'
        f'<div class="cards">{cards}</div>'
        '<details><summary>How record matching works</summary><section class="section-panel"><div class="section-kicker"><h2>LLM-guided record matching</h2><p>Rank · clarify · remember</p></div><p class="muted">The CLI gives the acting agent a value-free candidate table. It tries the best record, asks one focused question only when meaning is unclear, and remembers choices you explicitly approve for the next form.</p><div class="matching-flow"><div class="matching-step"><strong>1 · Rank</strong><p>Metadata and learned mappings put the strongest record first.</p><span class="tag purple">Value-free table</span></div><div class="matching-step"><strong>2 · Clarify</strong><p>The agent uses page context, then asks the returned question if needed.</p><span class="tag">Human when unclear</span></div><div class="matching-step"><strong>3 · Remember</strong><p>Confirmed choices store only IDs, scope, and outcome.</p><span class="tag green">Improves next time</span></div></div></section></details>'
        '<section class="section-panel" id="context-memory"><h2>Information with context</h2>'
        f'<p>Groups: <strong>{context_counts["bundle"]}</strong> · Supporting notes: <strong>{context_counts["document"]}</strong> · Confirmed preferences: <strong>{context_counts["preference"]}</strong> · Task observations: <strong>{context_counts["observation"]}</strong></p>'
        f'<p>Context records needing review after changed evidence: <strong>{context_review_count}</strong>.</p>'
        '<p>Your agent can organize supporting notes automatically. Changing facts, identity relationships or defaults needs your confirmation. A one-time choice stays one-time.</p>'
        '<p>Ask your agent: “Show the address groups relevant to this delivery.” It checks access before showing context and asks when meaning is unclear. Changed evidence retires old notes until reviewed. Note bodies stay off this metadata dashboard.</p></section>'
        '<section class="section-panel" id="next-task"><h2>Choose your next task</h2><p>Use a saved address: <code>agent-autofill doctor --task profile</code>. Fill a login: <code>agent-autofill doctor --task login</code>. Check email: <code>agent-autofill doctor --task email</code>.</p><p>Start with one field or account. Mail, Messages, and financial records are optional. Try <code>agent-autofill demo</code> for a synthetic read, handoff, and revocation walkthrough.</p></section>'
        '<section class="section-panel" id="access"><h2>Who can access what?</h2><p><strong>trusted-local-read</strong> means ongoing read access until revoked. It is broader than approval for one task. <strong>grant-required</strong> means only operations covered by a matching active grant. Review each agent and its grants below.</p><p>Stop future reads with <code>agent-autofill revoke-agent AGENT_ID</code>. This does not log out website sessions or recall information already delivered. Expired grants and revoked agents are history, not setup failures.</p></section>'
        '<section class="section-panel"><div class="section-kicker"><h2>Agent workflow</h2><p>This dashboard explains policy; the CLI performs the information work.</p></div><div class="delivery-options">'
        '<div class="delivery-option"><strong>Direct CLI delivery</strong><p>The authenticated acting agent requests approved fields through the CLI/MCP path. It owns navigation, submission, and any follow-up verification.</p><span class="tag">Agent sees approved fields</span></div>'
        '<div class="delivery-option extension"><strong>Optional bridge delivery</strong><p>The CLI can request a one-time, origin-bound handle for the local extension bridge. The password stays out of agent input.</p><span class="tag green">Transport helper only</span></div>'
        '</div><div class="steps"><div class="step"><span class="step-number">1</span><div><strong>Review here</strong><span>Check stored metadata and attention items.</span></div></div><div class="step"><span class="step-number">2</span><div><strong>Use the CLI</strong><span>Match, grant, and request the exact information.</span></div></div><div class="step"><span class="step-number">3</span><div><strong>Act elsewhere</strong><span>The acting agent decides what happens next.</span></div></div></div></section>'
        f'<section class="section-panel boundary"><h2>Consent and verification boundary</h2><p class="muted">The exact-origin and purpose grant preview is the consent gate. The extension handle is a scoped, one-time verification handoff, not a reusable link or proof that the site accepted anything. The broker does not navigate, submit, or verify the result after delivery; the acting agent owns browser submission and must obtain a fresh confirmation immediately before clicking. Financial information always requires it. Current policy: <strong>{escape(submission_policy.confirmation)}</strong>.</p></section>'
        '<h2 class="advanced-title">Advanced details</h2>'
        "<details><summary>Metadata tables</summary><div class=grid>"
        + table("Sources", sources, ("source_id", "kind", "label"))
        + table("Context records", context_rows[:100], ("kind", "revision", "fields", "status"))
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
        "</body></html>"
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
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            return

    HTTPServer((host, port), Handler).serve_forever()
