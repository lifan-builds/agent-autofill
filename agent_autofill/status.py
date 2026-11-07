"""Value-free readiness and dashboard projections for the local broker.

This module owns presentation-facing status calculations.  It deliberately
depends only on metadata APIs and read-only source presence checks; sensitive
record values never enter a status snapshot.
"""

import hashlib
import sys
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, cast

from .chrome import ChromeImportError, default_profile
from .form_policy import get_form_submission_policy
from .service import Broker
from .storage import CredentialStoreError


def _installation_status() -> dict[str, str]:
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    try:
        package_version = version("agent-autofill")
    except PackageNotFoundError:
        package_version = "uninstalled"
    return {
        "version": package_version,
        "build": digest.hexdigest()[:16],
        "package_path": str(root),
        "python_executable": sys.executable,
        "resolved_python": str(Path(sys.executable).resolve()),
    }


def _keychain_status(broker: Broker) -> dict[str, str]:
    """Return a value-free check of the OS credential-store boundary."""

    provider = getattr(broker, "_keyring_provider", None)
    if provider is None:
        return {
            "status": "not-configured",
            "summary": "This broker was opened with an explicit test key",
            "next_action": "Use the normal encrypted database path for Keychain-backed operation",
        }
    loader = getattr(provider, "_load_vault", None)
    if not callable(loader):
        return {
            "status": "unknown",
            "summary": "The configured credential-store provider cannot be checked",
            "next_action": "Check the operating-system credential store before saving connector data",
        }
    try:
        loader()
    except CredentialStoreError as error:
        return {
            "status": "needs-attention",
            "summary": str(error),
            "code": error.code,
            "next_action": error.next_action,
        }
    except Exception:  # noqa: BLE001 - provider errors must remain value-free
        return {
            "status": "needs-attention",
            "summary": (
                "The operating-system credential store is unavailable or "
                "blocked for this process"
            ),
            "next_action": (
                "Unlock the login Keychain and rerun Agent Autofill in a host "
                "process with Keychain access"
            ),
        }
    return {
        "status": "ready",
        "summary": "The operating-system credential store is available",
        "next_action": "",
    }


def _startup_failure_snapshot(error: RuntimeError) -> dict[str, object]:
    """Render a safe status response when the encrypted broker cannot open."""

    message = str(error).strip() or "The encrypted broker could not be opened"
    next_action = (
        "Unlock the login Keychain and rerun this command in a host process "
        "with Keychain access"
    )
    if isinstance(error, CredentialStoreError):
        next_action = error.next_action
    return {
        "status": "needs-attention",
        "installation": _installation_status(),
        "broker": {
            "status": "unavailable",
            "summary": "The encrypted local broker could not be opened",
        },
        "inventory_status": "unknown",
        "keychain": {
            "status": "needs-attention",
            "summary": message,
            "next_action": next_action,
            **({"code": error.code, "os_status": error.os_status}
               if isinstance(error, CredentialStoreError) else {}),
        },
        "next_action": next_action,
        "stored": {
            "sources": None,
            "profile_fields": None,
            "profile_groups": None,
            "credentials": None,
            "financial_records": None,
            "agents": None,
            "active_grants": None,
            "recent_activity": None,
        },
        "sources": {
            "configured": None,
            "ready": None,
            "needs_attention": None,
            "items": [],
        },
        "agents": {"configured": None, "revoked": None},
        "grants": {
            "active": None,
            "expired": None,
            "expiring_24h": None,
            "invalid_expiry": None,
            "revoked": None,
        },
        "form_submission": {
            "confirmation": "always",
            "financial_confirmation": "always",
        },
        "attention": [next_action],
        "local_sources": {
            "chrome_profiles": None,
            "chrome_status": "unknown",
            "messages_status": "unknown",
        },
    }


def _source_statuses(broker: Broker) -> list[dict[str, str]]:
    """Describe configured sources without exposing connector metadata."""

    statuses: list[dict[str, str]] = []
    unavailable: set[str] = getattr(broker, "unavailable_email_sources", set())
    email_adapters = getattr(broker, "email_adapters", {})
    message_adapters = getattr(broker, "message_adapters", {})
    for source in broker.sources.values():
        source_id = source.get("source_id")
        source_key = str(source_id) if source_id is not None else ""
        kind = source.get("kind", "unknown")
        status = "configured"
        summary = "Configured; run its read-only check before use"
        next_action = ""
        if kind == "email":
            if source_key in unavailable:
                status = "needs-attention"
                summary = "The saved email source is unavailable"
                next_action = "Run email-test for the configured source"
            elif source_key in email_adapters:
                status = "ready"
                summary = "A read-only email adapter is connected"
            elif broker.store.get("connection", source_key):
                next_action = "Run email-test for the configured source"
            else:
                status = "needs-attention"
                summary = "The email source has no saved connector configuration"
                next_action = "Reconnect the source with setup or email-add"
        elif kind == "messages":
            if source_key in message_adapters:
                status = "ready"
                summary = "A read-only Messages adapter is connected"
            else:
                next_action = "Open the local Messages source before requesting data"
        statuses.append(
            {
                "kind": str(kind),
                "status": status,
                "summary": summary,
                "next_action": next_action,
            }
        )
    return statuses


def _safe_email_match_metadata(matches: object) -> list[dict[str, object]]:
    """Project recipient matches to the documented metadata-only fields."""

    if not isinstance(matches, list):
        raise SystemExit("Email metadata matcher returned an invalid result")
    allowed = ("source_id", "provider", "label", "kind")
    safe: list[dict[str, object]] = []
    for item in matches:
        if not isinstance(item, dict):
            raise SystemExit("Email metadata matcher returned an invalid result")
        safe.append({key: item[key] for key in allowed if key in item})
    return safe


def _grant_attention(grants: list[dict]) -> dict[str, object]:
    """Count revoked and soon-to-expire grants using only safe metadata."""

    now = datetime.now(UTC)
    revoked = 0
    expired = 0
    expiring = 0
    invalid_expiry = 0
    for grant in grants:
        if grant.get("revoked"):
            revoked += 1
            continue
        raw_expiry = grant.get("expires_at")
        try:
            expiry = datetime.fromisoformat(str(raw_expiry))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            invalid_expiry += 1
            continue
        if expiry <= now:
            expired += 1
        elif expiry - now <= timedelta(hours=24):
            expiring += 1
    return {
        "revoked": revoked,
        "expired": expired,
        "expiring_24h": expiring,
        "invalid_expiry": invalid_expiry,
    }


def _local_source_snapshot() -> dict[str, object]:
    """Probe optional local source presence without opening provider data."""

    try:
        default_profile()
        chrome_count = 1
        chrome_status = "available"
    except ChromeImportError:
        chrome_count = 0
        chrome_status = "not-found"
    except OSError:
        chrome_count = 0
        chrome_status = "unavailable"
    messages_path = Path.home() / "Library" / "Messages" / "chat.db"
    return {
        "chrome_profiles": chrome_count,
        "chrome_status": chrome_status,
        "messages_status": "available" if messages_path.exists() else "not-found",
    }


def _status_snapshot(broker: Broker, task: str = "profile") -> dict[str, object]:
    """Build the shared value-free status projection for CLI commands."""

    if task not in {"profile", "login", "email", "messages"}:
        raise ValueError("unknown readiness task")
    source_statuses = _source_statuses(broker)
    agents = broker.list_agents()
    grants = broker.list_grants()
    grant_summary = _grant_attention(grants)
    stored = {
        "sources": len(source_statuses),
        "profile_fields": broker.store.count("profile"),
        "profile_groups": len(broker.list_profile_groups()),
        "credentials": broker.store.count("credential"),
        "financial_records": broker.store.count("financial"),
        "agents": len(agents),
        "active_grants": sum(bool(item.get("active")) for item in grants),
        "recent_activity": len(broker.store.audits()[-20:]),
    }
    submission_policy = get_form_submission_policy(broker)
    attention: list[str] = []
    keychain = _keychain_status(broker)
    if keychain["status"] in {"needs-attention", "unknown"}:
        attention.append(keychain["next_action"])
    attention.extend(
        item["next_action"]
        for item in source_statuses
        if item["status"] != "ready" and item["next_action"]
        and item["kind"] == task
    )
    if task in {"email", "messages"} and not any(
        item["kind"] == task for item in source_statuses
    ):
        attention.append(
            f"Run setup to connect the {task} source needed for this task"
        )
    if not any(not item.get("revoked") for item in agents):
        attention.append(
            "Create an agent identity with agent-create before connecting a consumer"
        )
    if task == "profile" and not stored["profile_fields"]:
        attention.append("Add a profile field with profile add when a local value is needed")
    if task == "login" and not stored["credentials"]:
        attention.append("Add one website credential through the approved local import flow")
    status = "needs-attention" if attention else "ready"
    return {
        "task": task,
        "installation": _installation_status(),
        "readiness_scope": "Local prerequisites only; the task still needs authorized access",
        "status": status,
        "next_action": attention[0]
        if attention
        else "The local broker is ready for reviewed reads",
        "broker": {
            "status": "ready",
            "summary": "Encrypted local broker opened successfully",
        },
        "keychain": keychain,
        "sources": {
            "configured": len(source_statuses),
            "ready": sum(item["status"] == "ready" for item in source_statuses),
            "needs_attention": sum(
                item["status"] == "needs-attention" for item in source_statuses
            ),
            "items": source_statuses,
        },
        "stored": stored,
        "agents": {
            "configured": len(agents),
            "revoked": sum(bool(item.get("revoked")) for item in agents),
        },
        "grants": {
            **grant_summary,
            "active": stored["active_grants"],
        },
        "form_submission": submission_policy.to_dict(),
        "attention": attention,
        "local_sources": _local_source_snapshot(),
    }


def _print_status(
    snapshot: dict[str, object], title: str, *, as_json: bool = False
) -> None:
    """Render a concise operator summary, or its machine-readable projection."""

    if as_json:
        import json

        print(json.dumps(snapshot, indent=2, sort_keys=True))
        return
    print(f"{title}: {snapshot['status']}")
    installation = snapshot.get("installation")
    if isinstance(installation, dict):
        print(f"Installation: {installation['version']} build {installation['build']}")
        print(f"Package: {installation['package_path']}")
        print(f"Python: {installation['python_executable']} -> {installation['resolved_python']}")
    print(f"Next action: {snapshot['next_action']}")
    print("Summary is metadata-only; stored values and secrets are omitted.")
    broker = cast(dict[str, Any], snapshot["broker"])
    keychain = cast(dict[str, Any], snapshot["keychain"])
    sources = cast(dict[str, Any], snapshot["sources"])
    stored = cast(dict[str, Any], snapshot["stored"])
    print(f"Broker: {broker['summary']}")
    print(f"Keychain: {keychain['summary']}")
    if broker["status"] == "unavailable":
        print("Inventory: unknown because the broker could not open; saved data was not counted.")
        return
    print(
        "Sources: "
        f"{sources['configured']} configured, {sources['ready']} ready, "
        f"{sources['needs_attention']} need attention"
    )
    print(
        "Stored: "
        f"{stored['profile_fields']} profile fields, "
        f"{stored['credentials']} credentials, "
        f"{stored['financial_records']} financial records, "
        f"{stored['agents']} agents, {stored['active_grants']} active grants"
    )
    policy = cast(dict[str, Any], snapshot.get("form_submission", {}))
    if policy:
        print(
            "Form submission: "
            f"{policy.get('confirmation', 'always')} confirmation; "
            "financial confirmation always required"
        )
    local_sources = cast(dict[str, Any], snapshot["local_sources"])
    print(
        "Local sources: "
        f"{local_sources['chrome_profiles']} Chrome profile(s) ({local_sources['chrome_status']}), "
        f"Messages database ({local_sources['messages_status']})"
    )
    grants = cast(dict[str, Any], snapshot["grants"])
    print(
        "Grants: "
        f"{grants['expiring_24h']} expiring within 24h, "
        f"{grants['expired']} expired, {grants['revoked']} revoked"
    )
    attention = cast(list[str], snapshot["attention"])
    if attention:
        print("Needs attention:")
        for item in attention:
            print(f"- {item}")
    else:
        print("Needs attention: none")
