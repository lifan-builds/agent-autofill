from datetime import UTC, datetime, timedelta

from agent_autofill.inbox import build_html
from agent_autofill.service import Broker
from agent_autofill.status import _status_snapshot
from agent_autofill.storage import Store


def test_profile_readiness_ignores_optional_sources_and_access_history():
    broker = Broker(Store(":memory:", key=b"r" * 32))
    broker.put_profile("name", "identity", "Name", "Synthetic Person")
    broker.create_agent("active")
    broker.create_agent("old")
    broker.grant("old", "profile.read", "name", "https://example.test", "test")
    broker.revoke_agent("old")
    broker.grant("active", "profile.read", "name", "https://example.test", "test")
    grant = next(item for item in broker.list_grants() if item["agent_id"] == "active")
    grant_id = grant.pop("grant_id")
    grant.pop("active")
    grant["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    broker.store.put("grant", grant_id, grant)
    snapshot = _status_snapshot(broker, "profile")
    assert snapshot["status"] == "ready"
    assert snapshot["grants"]["expired"] == 1
    assert snapshot["grants"]["revoked"] == 1
    assert _status_snapshot(broker, "login")["status"] == "needs-attention"
    assert _status_snapshot(broker, "email")["status"] == "needs-attention"
    page = build_html(broker)
    assert "No local sources are configured" not in page
    assert "No website credentials" not in page
    assert "No financial metadata" not in page
    assert "ongoing read access" in page
    assert "Synthetic Person" not in page


def test_revoked_only_identity_is_not_ready():
    broker = Broker(Store(":memory:", key=b"r" * 32))
    broker.put_profile("name", "identity", "Name", "Synthetic Person")
    broker.create_agent("old")
    broker.revoke_agent("old")
    assert _status_snapshot(broker)["status"] == "needs-attention"
