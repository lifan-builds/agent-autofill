from datetime import UTC, datetime, timedelta

import pytest

from agent_autofill.models import EmailMessage
from agent_autofill.service import AuthCodeError, Broker, BrokerError
from agent_autofill.status import (
    _installation_status,
    _startup_failure_snapshot,
    _status_snapshot,
)
from agent_autofill.storage import Store


def broker():
    b = Broker(Store(":memory:", key=b"s" * 32))
    b.create_agent("service:test")
    b.enable_trusted_local("service:test")
    b.add_source("mail", "email", "Synthetic")
    return b


def message(sender="otp@example.test", *, age=0, body="Your code is 123456", mid="one"):
    return EmailMessage(mid, "mail", sender, "Synthetic verification", body,
                        (datetime.now(UTC) - timedelta(seconds=age)).isoformat())


def test_target_matching_ignores_newer_other_service_and_consumes_only_match():
    b = broker()
    b.email["mail"] = [message(age=10), message("otp@other.test", mid="two")]
    result = b.request_auth_code("service:test", "mail", "https://example.test", "login")
    assert result["provenance"]["message_id"] == "one"
    with pytest.raises(AuthCodeError) as error:
        b.request_auth_code("service:test", "mail", "https://example.test", "login")
    assert error.value.reason == "otp_consumed"


@pytest.mark.parametrize("sender", [
    '"otp@example.test" <otp@evil.test>', "otp@example.test.evil",
    "otp@evilexample.test", "otp@example.test, otp@evil.test",
])
def test_sender_spoof_strings_do_not_match(sender):
    b = broker()
    b.email["mail"] = [message(sender, body="https://example.test Your code is 123456")]
    with pytest.raises(AuthCodeError) as error:
        b.request_auth_code("service:test", "mail", "https://example.test", "login")
    assert error.value.reason == "otp_service_mismatch"
    assert b.store.count("otp-consumed") == 0
    assert "123456" not in str(error.value)


@pytest.mark.parametrize("sender", ["noreply@tm.openai.com", "otp@tm1.openai.com"])
def test_documented_openai_sender_aliases(sender):
    b = broker()
    b.email["mail"] = [message(sender)]
    assert b.request_auth_code("service:test", "mail", "https://auth.openai.com", "login")["code"] == "123456"


@pytest.mark.parametrize(("messages", "reason"), [
    ([], "otp_not_found"), ([message(age=400)], "otp_stale"),
    ([message(body="Your code is 123456 and your code is 654321")], "otp_ambiguous"),
])
def test_distinct_safe_otp_failure_reasons(messages, reason):
    b = broker()
    b.email["mail"] = messages
    with pytest.raises(AuthCodeError) as error:
        b.request_auth_code("service:test", "mail", "https://example.test", "login")
    assert error.value.reason == reason
    assert error.value.next_action
    assert "123456" not in str(error.value)
    assert b.store.count("otp-consumed") == 0


def test_default_requires_target_and_unknown_policy_never_falls_back():
    b = broker()
    b.email["mail"] = [message()]
    with pytest.raises(BrokerError, match="requires target_origin"):
        b.request_auth_code("service:test", "mail")
    with pytest.raises(BrokerError, match="origin_policy"):
        b.request_auth_code("service:test", "mail", origin_policy="typo")


def test_inventory_paging_grouping_and_mapping_beyond_100():
    b = broker()
    for i in range(105):
        fid = f"field_{i:03}"
        b.add_source(fid, "email", "Synthetic")
        b.put_profile(fid, "custom", fid, "synthetic")
        b.store.put("credential", fid, {"origin": "https://example.test", "username": fid, "password": "synthetic"})
        b.store.put("financial", fid, {"record_id": fid, "kind": "custom_financial", "display_name": fid, "values": {"reference": "synthetic"}})
    status = _status_snapshot(b)
    assert status["stored"]["sources"] == 106
    for key in ("profile_fields", "credentials", "financial_records"):
        assert status["stored"][key] == 105
    assert sum(g["field_count"] for g in b.list_profile_groups()) == 105
    assert len(b.list_profile_fields()) == 100
    assert len(b.list_profile_fields(offset=100)) == 5
    assert len(b.list_credentials(offset=100)) == 5
    assert len(b.list_credentials(target_origin="https://example.test", offset=100)) == 5
    assert len(b.list_financial_records(offset=100)) == 5
    assert len(b.match_financial_records()) == 105
    group = next(g for g in b.list_profile_groups() if "field_104" in g["field_ids"])
    assert b.request_profile_fields("service:test", ["field_104"], group_id=group["group_id"])
    assert "field_104" in str(b.match_form_fields([{"label": "field_104"}]))


def test_installation_identity_present_even_when_keychain_fails():
    identity = _installation_status()
    assert len(identity["build"]) == 16
    assert identity["package_path"].endswith("agent_autofill")
    assert identity["resolved_python"]
    assert _startup_failure_snapshot(RuntimeError("Unavailable"))["installation"] == identity
