import io
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from agent_autofill import cli, server
from agent_autofill.form_memory import profile_group_id
from agent_autofill.imap_adapter import IMAPEmailAdapter
from agent_autofill.models import EmailMessage
from agent_autofill.server import _call
from agent_autofill.service import (
    AuthCodeError,
    AuthorizationError,
    Broker,
    BrokerError,
)
from agent_autofill.status import (
    _installation_status,
    _startup_failure_snapshot,
    _status_snapshot,
)
from agent_autofill.storage import Store


def broker():
    b = Broker(Store(":memory:", key=b"s" * 32))
    b._test_token = b.create_agent("service:test")
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


@pytest.mark.parametrize("origin", ["https://www.facebook.com", "https://m.facebook.com", "https://facebook.com"])
def test_documented_facebook_sender_alias(origin):
    b = broker()
    b.email["mail"] = [message("registration@facebookmail.com", body="FB-85673 is your confirmation code")]
    assert b.request_auth_code("service:test", "mail", origin, "account_registration")["code"] == "85673"


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


def _put_chrome_group(
    b: Broker,
    guid: str,
    *,
    first_name: str,
    last_name: str,
    street: str,
    city: str,
    state: str,
    postcode: str,
    country: str,
) -> str:
    fields = {
        "first_name": first_name,
        "last_name": last_name,
        "street": street,
        "city": city,
        "state": state,
        "postal_code": postcode,
        "country": country,
    }
    for ftype, value in fields.items():
        b.put_profile(
            f"chrome_{guid}_{ftype}",
            "chrome-address",
            f"Chrome {ftype}",
            value,
            provenance={
                "source": "chrome-structured-address",
                "source_profile_id": "chrome_profile_default",
                "source_guid": guid,
                "field_type": ftype,
            },
        )
    return profile_group_id(
        {
            "field_id": f"chrome_{guid}_street",
            "provenance": {
                "source": "chrome-structured-address",
                "source_profile_id": "chrome_profile_default",
                "source_guid": guid,
                "field_type": "street",
            },
        }
    )


def test_ten_plus_chrome_groups_expose_safe_disambiguation_and_primary_resolution():
    b = broker()
    b.put_profile("self_name", "identity", "Full name", "Alice Vance")
    people = [
        ("g01", "Bob", "Miller", "101 Pine St", "Austin", "TX", "78701", "US"),
        ("g02", "Carol", "Davis", "202 Oak Ave", "Seattle", "WA", "98101", "US"),
        ("g03", "David", "Kim", "303 Cedar Rd", "Boston", "MA", "02108", "US"),
        ("g04", "Eva", "Chen", "404 Birch Ln", "Miami", "FL", "33101", "US"),
        ("g05", "Frank", "Lopez", "505 Elm Blvd", "Denver", "CO", "80202", "US"),
        ("g06", "Grace", "Patel", "606 Maple Dr", "Chicago", "IL", "60601", "US"),
        ("g07", "Henry", "Nguyen", "707 Spruce Way", "Phoenix", "AZ", "85001", "US"),
        ("g08", "Iris", "Scott", "808 Walnut Ct", "Portland", "OR", "97201", "US"),
        ("g09", "Jack", "White", "909 Ash Pl", "Toronto", "ON", "M5H 2N2", "CA"),
        ("g10", "Alice", "Vance", "1010 Market St", "New York", "NY", "10001", "US"),
        ("g11", "Alice", "Vance", "2020 Castro St", "Mountain View", "CA", "94041", "US"),
        ("g12", "Liam", "Young", "3030 King St", "Vancouver", "BC", "V6B 1P1", "CA"),
    ]
    gids: dict[str, str] = {}
    for guid, fn, ln, st, ci, region, zip_code, cc in people:
        gids[guid] = _put_chrome_group(
            b,
            guid,
            first_name=fn,
            last_name=ln,
            street=st,
            city=ci,
            state=region,
            postcode=zip_code,
            country=cc,
        )

    groups = b.list_profile_groups()
    chrome_groups = [g for g in groups if g["source"] == "chrome-structured-address"]
    assert len(chrome_groups) == 12
    serialized_groups = str(chrome_groups)
    # Never leak full names, streets, cities, or zip codes in metadata listings
    for secret in ("Alice", "Vance", "Bob", "Miller", "Mountain View", "2020 Castro St", "94041"):
        assert secret not in serialized_groups

    g11 = next(g for g in chrome_groups if g["group_id"] == gids["g11"])
    g01 = next(g for g in chrome_groups if g["group_id"] == gids["g01"])
    assert g11["matches_self_profile"] is True
    assert g11["recipient_initials"] == "A. V."
    assert g11["state"] == "CA"
    assert g11["country"] == "US"
    assert "Self match · A. V. · CA, US" in g11["summary"]
    assert g01["matches_self_profile"] is False
    assert g01["recipient_initials"] == "B. M."
    assert g01["state"] == "TX"

    # Preflight across 12 groups returns all 12 candidates (not capped at 5)
    preflight = b.match_form_fields(
        [
            {"label": "Street address", "autocomplete": "street-address"},
            {"label": "City", "autocomplete": "address-level2"},
            {"label": "State", "autocomplete": "address-level1"},
            {"label": "ZIP code", "autocomplete": "postal-code"},
        ],
        target_origin="https://aca.aaamembershipoffers.com",
        purpose="membership-purchase",
    )
    street_candidates = preflight[0]["candidate_rows"]
    assert len(street_candidates) == 12
    # Both of Alice's self-profile groups rank above unrelated friends/household rows
    assert {street_candidates[0]["group_id"], street_candidates[1]["group_id"]} == {
        gids["g10"],
        gids["g11"],
    }
    assert "A. V." in (street_candidates[0]["group_summary"] or "")
    assert "Mountain View" not in str(preflight)

    # Mark g11 as primary home via user-confirmed update_record_scope without touching values
    patched = _call(
        b,
        "update_record_scope",
        {
            "agent_id": "service:test",
            "agent_token": b._test_token,
            "record_type": "profile",
            "group_id": gids["g11"],
            "group_label": "Primary home",
            "is_primary": True,
            "confirmed": True,
        },
    )
    assert patched["status"] == "updated"
    assert patched["updated_count"] == 7

    resolved = b.match_form_fields(
        [
            {"label": "Street address", "autocomplete": "street-address"},
            {"label": "City", "autocomplete": "address-level2"},
            {"label": "State", "autocomplete": "address-level1"},
            {"label": "ZIP code", "autocomplete": "postal-code"},
        ],
        target_origin="https://aca.aaamembershipoffers.com",
        purpose="membership-purchase",
    )
    for field_row in resolved:
        assert field_row["status"] == "matched"
        assert field_row["confidence"] == "high"
        assert field_row["group_id"] == gids["g11"]
        assert field_row["candidate_rows"][0]["group_label"] == "Primary home"
        assert field_row["candidate_rows"][0]["is_primary"] is True


def test_match_form_fields_excludes_origin_mismatched_records_and_keeps_group_coherence():
    b = broker()
    group_id = _put_chrome_group(
        b,
        "home",
        first_name="Alice",
        last_name="Vance",
        street="742 Evergreen Terrace",
        city="Springfield",
        state="CA",
        postcode="94041",
        country="US",
    )
    # Single-field hotel-scoped records restricted to hilton.com
    b.put_profile(
        "hilton_city",
        "address",
        "City",
        "Honolulu",
        allowed_origins=["https://www.hilton.com"],
        allowed_purposes=["hotel-reservation"],
    )
    b.put_profile(
        "hilton_state",
        "address",
        "State",
        "HI",
        allowed_origins=["https://www.hilton.com"],
        allowed_purposes=["hotel-reservation"],
    )
    b.put_profile(
        "hilton_country",
        "address",
        "Country",
        "US",
        allowed_origins=["https://www.hilton.com"],
        allowed_purposes=["hotel-reservation"],
    )
    # Also add an unrestricted standalone single-field "city" record to ensure it does not split the Chrome group
    b.put_profile(
        "loose_city",
        "address",
        "City",
        "Oakland",
    )

    form_fields = [
        {"label": "Street address", "name": "street"},
        {"label": "City", "name": "city"},
        {"label": "State", "name": "state"},
        {"label": "ZIP code", "name": "zip"},
        {"label": "Country", "name": "country"},
    ]

    result = _call(
        b,
        "match_form_fields",
        {
            "agent_id": "service:test",
            "agent_token": b._test_token,
            "target_origin": "https://aca.aaamembershipoffers.com",
            "purpose": "membership-purchase",
            "fields": form_fields,
        },
    )

    serialized = str(result)
    assert "hilton_city" not in serialized
    assert "hilton_state" not in serialized
    assert "hilton_country" not in serialized

    for item in result:
        assert item["status"] == "matched"
        assert item["llm_guidance"]["action"] == "use_top_candidate"
        assert item["group_id"] == group_id
        assert item["candidate_rows"][0]["group_id"] == group_id

    # Even when called without target_origin, origin-restricted hilton records must never be status="matched"
    unscoped = b.match_form_fields([{"label": "City", "name": "city"}])
    city_row = unscoped[0]
    for cand in city_row["candidate_rows"]:
        if cand["field_id"] == "hilton_city":
            assert cand.get("origin_restricted") is True
            assert city_row["matched_field_ids"] != ["hilton_city"]


def test_default_payment_card_c1vx_and_secret_preserving_scope_patch(monkeypatch):
    b = broker()
    b.put_financial_record(
        "c1vx",
        "payment_card",
        "Capital One Venture X",
        {
            "card_number": "4111222233334444",
            "security_code": "321",
            "expiration_month": "09",
            "expiration_year": "2029",
        },
        matching_hints={"network": "Visa", "last4": "4444"},
        allowed_origins=["https://www.hilton.com"],
        allowed_purposes=["hotel-reservation"],
    )
    b.put_financial_record(
        "older-card",
        "payment_card",
        "Backup Card",
        {"card_number": "5555444433331111", "security_code": "111"},
        matching_hints={"network": "Mastercard", "last4": "1111"},
        is_default=True,
    )

    # Rejects unconfirmed scope patch
    with pytest.raises(BrokerError, match="confirmed"):
        b.update_record_scope(
            "service:test",
            "financial",
            "c1vx",
            confirmed=False,
            is_default=True,
        )

    # Rejects secret payloads in update_record_scope via MCP schema/dispatch
    with pytest.raises(BrokerError, match="raw secret"):
        _call(
            b,
            "update_record_scope",
            {
                "agent_id": "service:test",
                "agent_token": b._test_token,
                "record_type": "financial",
                "record_id": "c1vx",
                "confirmed": True,
                "card_number": "4000000000000002",
            },
        )

    # Patch scope to ["*"] (all websites), set is_default=True, and add hint="C1vx" without re-entering secrets
    patched = _call(
        b,
        "update_record_scope",
        {
            "agent_id": "service:test",
            "agent_token": b._test_token,
            "record_type": "financial",
            "record_id": "c1vx",
            "allowed_origins": ["*"],
            "allowed_purposes": ["*"],
            "is_default": True,
            "matching_hints": {"hint": "C1vx", "institution": "Capital One"},
            "confirmed": True,
        },
    )
    assert patched["status"] == "updated"
    assert patched["is_default"] is True
    assert patched["allowed_origins"] == []
    assert patched["allowed_purposes"] == []
    assert "4111222233334444" not in str(patched)
    assert "321" not in str(patched)

    # Default card sorts first, older card had is_default cleared, and query="C1vx" finds it
    all_cards = b.list_financial_records()
    assert [c["record_id"] for c in all_cards] == ["c1vx", "older-card"]
    assert all_cards[0]["is_default"] is True
    assert all_cards[1]["is_default"] is False
    assert [c["record_id"] for c in b.match_financial_records(query="C1vx")] == ["c1vx"]
    assert [c["record_id"] for c in b.list_financial_records(is_default=True)] == ["c1vx"]

    # Encrypted secrets remain intact and usable on any authorized website
    read = b.request_financial_fields(
        "service:test",
        "c1vx",
        ["card_number", "security_code"],
        target_origin="https://aca.aaamembershipoffers.com",
        purpose="membership-purchase",
    )
    assert read["card_number"] == "4111222233334444"
    assert read["security_code"] == "321"

    # Non-trusted agent still requires financial.write grant for update_record_scope
    remote_token = b.create_agent("remote-agent")
    with pytest.raises(AuthorizationError):
        _call(
            b,
            "update_record_scope",
            {
                "agent_id": "remote-agent",
                "agent_token": remote_token,
                "target_origin": "https://www.hilton.com",
                "purpose": "checkout",
                "record_type": "financial",
                "record_id": "c1vx",
                "is_default": False,
                "confirmed": True,
            },
        )

    # CLI payment-card-edit also updates metadata/default without prompting for secrets
    monkeypatch.setattr(cli, "_broker", lambda _path: b)
    assert (
        cli.main(
            [
                "--db",
                "unused.sqlite",
                "payment-card-edit",
                "older-card",
                "--default",
                "--hint",
                "backup-mc",
                "--yes",
            ]
        )
        == 0
    )
    assert b.list_financial_records(is_default=True)[0]["record_id"] == "older-card"
    assert b.store.get("financial", "older-card")["values"]["card_number"] == "5555444433331111"


def test_oauth_sso_subdomain_credential_matching_and_tenant_isolation():
    b = broker()
    b.put_credential(
        "hertz-www-old",
        "Hertz Legacy",
        "https://www.hertz.com/rentacar/reservation",
        "member@example.com",
        "old-pass",
    )
    b.put_credential(
        "hertz-auth-sso",
        "Hertz Auth0 Login",
        "https://auth.hertz.com/u/login",
        "member@example.com",
        "working-sso-pass",
    )
    b.put_credential(
        "hertz-blog",
        "Hertz Blog",
        "https://blog.hertz.com/wp-login.php",
        "member@example.com",
        "blog-pass",
    )
    b.put_credential(
        "hertz-uk-auth",
        "Hertz UK Auth",
        "https://login.hertz.co.uk/u/login",
        "member@example.com",
        "uk-sso-pass",
    )
    b.put_credential(
        "github-pages-alice",
        "Alice GitHub Pages",
        "https://alice.github.io/login",
        "alice",
        "alice-pass",
    )

    matches = b.match_credentials("https://www.hertz.com")
    assert [m["credential_id"] for m in matches] == [
        "hertz-www-old",
        "hertz-auth-sso",
        "hertz-blog",
    ]
    assert matches[0]["match"] == "exact-host"
    assert matches[1]["match"] == "registrable-domain"
    assert matches[2]["match"] == "registrable-domain"
    assert "working-sso-pass" not in str(matches)

    # Reverse direction (on Auth0 redirect page auth.hertz.com) puts auth.hertz.com first, then www.hertz.com
    auth_matches = b.match_credentials("https://auth.hertz.com/u/login")
    assert [m["credential_id"] for m in auth_matches[:2]] == [
        "hertz-auth-sso",
        "hertz-www-old",
    ]
    assert auth_matches[0]["match"] == "exact-host"
    assert auth_matches[1]["match"] == "registrable-domain"

    # Multi-part ccSLD (.co.uk) matches login.hertz.co.uk from www.hertz.co.uk, not hertz.com
    uk_matches = b.match_credentials("https://www.hertz.co.uk/rentacar")
    assert [m["credential_id"] for m in uk_matches] == ["hertz-uk-auth"]
    assert uk_matches[0]["match"] == "registrable-domain"

    # Shared-hosting public suffixes MUST isolate tenants
    assert b.match_credentials("https://bob.github.io/login") == []

    # Downstream direct read & extension handle consumption allow hertz-auth-sso on www.hertz.com, deny evil.test
    direct = b.request_credential_fields(
        "service:test",
        "hertz-auth-sso",
        ["username", "password"],
        target_origin="https://www.hertz.com",
        purpose="login",
    )
    assert direct["password"] == "working-sso-pass"

    handle = b.request_credential_use(
        "service:test",
        "hertz-auth-sso",
        target_origin="https://www.hertz.com",
        purpose="login",
    )
    consumed = b.consume_credential_handle(
        handle["handle"], "service:test", "https://www.hertz.com"
    )
    assert consumed["password"] == "working-sso-pass"

    # Also works when redirect moves from www.hertz.com handle to auth.hertz.com
    redirect_handle = b.request_credential_use(
        "service:test",
        "hertz-auth-sso",
        target_origin="https://www.hertz.com",
        purpose="login",
    )
    redirect_consumed = b.consume_credential_handle(
        redirect_handle["handle"], "service:test", "https://auth.hertz.com"
    )
    assert redirect_consumed["password"] == "working-sso-pass"

    # Cross-domain handle consumption on evil.test is always rejected
    evil_handle = b.request_credential_use(
        "service:test",
        "hertz-auth-sso",
        target_origin="https://www.hertz.com",
        purpose="login",
    )
    with pytest.raises(BrokerError):
        b.consume_credential_handle(
            evil_handle["handle"], "service:test", "https://evil.test"
        )

    # Grant-scoped agent can read hertz-auth-sso on www.hertz.com when granted www.hertz.com, and is denied on evil.test
    b.create_agent("scoped-agent")
    b.grant("scoped-agent", "credential.read", "hertz-auth-sso", "https://www.hertz.com", "login")
    scoped_read = b.request_credential_fields(
        "scoped-agent",
        "hertz-auth-sso",
        ["username", "password"],
        target_origin="https://www.hertz.com",
        purpose="login",
    )
    assert scoped_read["password"] == "working-sso-pass"
    with pytest.raises(AuthorizationError):
        b.request_credential_fields(
            "scoped-agent",
            "hertz-auth-sso",
            ["username", "password"],
            target_origin="https://evil.test",
            purpose="login",
        )


def test_mcp_jsonrpc_stdio_end_to_end_all_five_fixes(monkeypatch, capsys):
    b = broker()
    # 1. Self profile + two Chrome structured address groups
    b.put_profile("self-name", "identity", "Full name", "Alice Vance")
    for guid, first, last, street, city, state, zipcode in [
        ("g-alice", "Alice", "Vance", "100 Market St", "San Francisco", "California", "94105"),
        ("g-bob", "Bob", "Stone", "500 Congress Ave", "Austin", "Texas", "78701"),
    ]:
        for ftype, val in [
            ("first_name", first),
            ("last_name", last),
            ("street", street),
            ("city", city),
            ("state", state),
            ("postcode", zipcode),
            ("country", "United States"),
        ]:
            b.put_profile(
                f"{guid}-{ftype}",
                "chrome-address",
                f"Chrome {ftype}",
                val,
                provenance={
                    "source": "chrome-structured-address",
                    "source_profile_id": "prof-1",
                    "source_guid": guid,
                    "field_type": ftype,
                },
            )

    # 2. Hilton-restricted single-field record that must NOT leak onto AAA
    b.put_profile(
        "hilton-city",
        "address",
        "City",
        "Beverly Hills",
        allowed_origins=["https://www.hilton.com"],
        allowed_purposes=["hotel-reservation"],
    )

    # 3. Scoped payment card to widen via update_record_scope without re-entering PAN/CVV
    b.put_payment_card(
        record_id="c1vx-e2e",
        display_name="Capital One Venture X",
        card_number="4111222233334444",
        expiration_month="10",
        expiration_year="2030",
        security_code="321",
        network="Visa",
        allowed_origins=["https://www.hilton.com"],
        allowed_purposes=["hotel-reservation"],
    )

    # 4. Hertz www + Auth0 SSO credentials
    b.put_credential(
        "hertz-www",
        "Hertz Web",
        "https://www.hertz.com",
        "alice@example.com",
        "old-pw",
    )
    b.put_credential(
        "hertz-sso",
        "Hertz Auth0",
        "https://auth.hertz.com",
        "alice@example.com",
        "sso-pw",
    )

    # 5. HTML confirmation email with heavy CSS + zero-width preheader padding
    raw_mime = (
        b"From: bookings@example.test\r\n"
        b"Subject: Confirmation #AAA-99\r\n"
        b"Date: Sat, 14 Nov 2026 12:00:00 +0000\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        + (
            "<html><head><style>"
            + ".hero{padding:12px;} @media(max-width:600px){.hero{padding:0;}}" * 80
            + "</style></head><body>"
            + "<div>" + "\u200c\u00a0" * 300 + "</div>"
            + "<p>Membership confirmed for Alice.</p></body></html>"
        ).encode("utf-8")
    )
    parsed_email = IMAPEmailAdapter(
        "mail", "imap.example.test", "alice@example.test"
    )._parse(b"m-e2e", raw_mime)
    b.email["mail"] = [parsed_email]

    connection = SimpleNamespace(
        broker=b,
        keyring=None,
        agent_id="service:test",
        token=b._test_token,
        close=b.store.db.close,
    )
    monkeypatch.setattr(server, "connect_service", lambda *_args: connection)

    rpc_requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "list_profile_groups", "arguments": {}},
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "match_form_fields",
                "arguments": {
                    "target_origin": "https://aca.aaamembershipoffers.com",
                    "purpose": "membership-purchase",
                    "fields": [
                        {"label": "Street Address"},
                        {"label": "City"},
                        {"label": "State"},
                        {"label": "ZIP Code"},
                    ],
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "update_record_scope",
                "arguments": {
                    "target_origin": "https://aca.aaamembershipoffers.com",
                    "purpose": "membership-purchase",
                    "record_type": "financial",
                    "record_id": "c1vx-e2e",
                    "allowed_origins": ["*"],
                    "allowed_purposes": ["*"],
                    "is_default": True,
                    "matching_hints": {"hint": "C1vx"},
                    "confirmed": True,
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "match_financial_records",
                "arguments": {"query": "C1vx"},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "match_credentials",
                "arguments": {"target_origin": "https://www.hertz.com"},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "search_email",
                "arguments": {"account_id": "mail", "query": "Membership confirmed"},
            },
        },
    ]
    monkeypatch.setattr(
        server.sys,
        "stdin",
        io.StringIO("\n".join(json.dumps(r) for r in rpc_requests) + "\n"),
    )
    server.main(["--service", "test"])

    out = capsys.readouterr().out
    replies = {
        row["id"]: row for row in (json.loads(line) for line in out.splitlines())
    }
    assert set(replies) == {1, 2, 3, 4, 5, 6, 7, 8}
    for req_id, reply in replies.items():
        assert "error" not in reply, f"Request {req_id} failed: {reply}"

    tool_names = {t["name"] for t in replies[2]["result"]["tools"]}
    assert "update_record_scope" in tool_names

    def payload(req_id: int):
        res = replies[req_id]["result"]
        assert not res.get("isError"), f"Tool {req_id} returned error: {res}"
        return json.loads(res["content"][0]["text"])

    groups = payload(3)
    chrome_groups = [
        g for g in groups if g["source"] == "chrome-structured-address"
    ]
    alice_gid = profile_group_id(
        {
            "field_id": "g-alice-street",
            "provenance": {
                "source": "chrome-structured-address",
                "source_profile_id": "prof-1",
                "source_guid": "g-alice",
            },
        }
    )
    assert chrome_groups[0]["group_id"] == alice_gid
    assert chrome_groups[0]["matches_self_profile"] is True
    assert "CA, US" in chrome_groups[0]["summary"]
    assert "Alice" not in json.dumps(groups)

    matched_form = payload(4)
    assert "hilton-city" not in json.dumps(matched_form)
    assert {row["candidate_rows"][0]["group_id"] for row in matched_form} == {
        alice_gid
    }
    assert all(
        row["candidate_rows"][0].get("matches_self_profile") is True
        for row in matched_form
    )

    updated_card = payload(5)
    assert updated_card["is_default"] is True
    assert updated_card["allowed_origins"] == []
    assert "4111222233334444" not in json.dumps(updated_card)

    fin_matches = payload(6)
    assert [m["record_id"] for m in fin_matches] == ["c1vx-e2e"]
    assert fin_matches[0]["is_default"] is True

    cred_matches = payload(7)
    assert [(m["credential_id"], m["match"]) for m in cred_matches] == [
        ("hertz-www", "exact-host"),
        ("hertz-sso", "registrable-domain"),
    ]

    emails = payload(8)
    assert len(emails) == 1
    assert emails[0]["body"] == "Membership confirmed for Alice."
    assert "@media" not in emails[0]["body"]
    assert "\u200c" not in emails[0]["body"]


