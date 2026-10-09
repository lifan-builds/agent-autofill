from datetime import datetime, timedelta, timezone
import pytest
from agent_autofill.service import (
    AuthorizationError,
    Broker,
    BrokerError,
    CredentialUseError,
)
from agent_autofill.models import EmailMessage, TextMessage
from agent_autofill.storage import Store


def setup():
    b = Broker()
    b.add_source("mail1", "email", "Personal")
    b.put_profile(
        "name",
        "identity",
        "Name",
        "Ada",
        allowed_origins=["example.com"],
        allowed_purposes=["form"],
    )
    b.email["mail1"] = [
        EmailMessage(
            "m1",
            "mail1",
            "no-reply@example.com",
            "Login code for example.com",
            "Your code is 123456",
            datetime.now(timezone.utc).isoformat(),
        )
    ]
    b.texts = [
        TextMessage(
            "t1", "chat1", "+1", "hello", datetime.now(timezone.utc).isoformat()
        )
    ]
    return b


def test_profile_is_scoped_and_provenance():
    b = setup()
    b.grant("codex", "profile.read", "name", "example.com", "form")
    assert (
        b.request_profile_fields("codex", ["name"], "example.com", "form")["name"][
            "value"
        ]
        == "Ada"
    )
    with pytest.raises(AuthorizationError):
        b.request_profile_fields("codex", ["name"], "evil.com", "form")


def test_email_and_message_are_scoped():
    b = setup()
    b.grant("codex", "email.search", "mail1", None, None)
    b.grant("codex", "messages.search", "chat1", None, None)
    b.grant("codex", "messages.read", "chat1", None, None)
    assert len(b.search_email("codex", "mail1", "login")) == 1
    assert len(b.search_messages("codex", "chat1", "hello")) == 1
    with pytest.raises(AuthorizationError):
        b.search_messages("other", "chat1", "hello")


def test_email_recipient_resolution_is_exact_and_source_ids_stay_internal():
    b = Broker(Store(":memory:", key=b"r" * 32))
    b.add_source("opaque-mailbox", "email", "Personal")
    b.store.put(
        "connection",
        "opaque-mailbox",
        {
            "source_id": "opaque-mailbox",
            "kind": "imap",
            "username": "Owner@Example.test",
        },
    )
    assert b.resolve_email_account("owner@example.test") == "opaque-mailbox"
    with pytest.raises(BrokerError, match="not configured"):
        b.resolve_email_account("other@example.test")


def test_match_email_sources_returns_exact_value_free_metadata():
    b = Broker(Store(":memory:", key=b"s" * 32))
    b.add_source("opaque-mailbox", "email", "Personal")
    b.store.put(
        "connection",
        "opaque-mailbox",
        {
            "source_id": "opaque-mailbox",
            "kind": "imap",
            "username": "Owner@Example.test",
            "host": "imap.example.test",
            "secret_name": "do-not-return",
        },
    )

    matches = b.match_email_sources("owner@example.test")

    assert matches == [
        {
            "source_id": "opaque-mailbox",
            "provider": "Personal",
            "label": "Personal",
            "kind": "email",
        }
    ]
    assert "Owner@Example.test" not in str(matches)
    assert "do-not-return" not in str(matches)
    assert b.match_email_sources("other@example.test") == []


def test_email_recipient_resolution_rejects_duplicate_addresses():
    b = Broker(Store(":memory:", key=b"q" * 32))
    for source_id in ("mail-a", "mail-b"):
        b.add_source(source_id, "email", source_id)
        b.store.put(
            "connection",
            source_id,
            {"source_id": source_id, "kind": "imap", "username": "same@example.test"},
        )
    with pytest.raises(BrokerError, match="ambiguous"):
        b.resolve_email_account("same@example.test")


def test_request_auth_code_for_recipient_keeps_scope_and_resolves_source():
    b = Broker(Store(":memory:", key=b"o" * 32))
    b.add_source("opaque-mailbox", "email", "Owner")
    b.store.put(
        "connection",
        "opaque-mailbox",
        {"source_id": "opaque-mailbox", "kind": "imap", "username": "owner@example.test"},
    )
    b.email["opaque-mailbox"] = [
        EmailMessage(
            "m1",
            "opaque-mailbox",
            "no-reply@auth.openai.com",
            "OpenAI login",
            "https://auth.openai.com code 123456",
            datetime.now(timezone.utc).isoformat(),
        )
    ]
    b.grant("service:cpa-phoenix", "email.otp", "opaque-mailbox", "https://auth.openai.com", "cpa-phoenix-revive")
    token = b.create_agent("service:cpa-phoenix")
    assert b.authenticate_agent("service:cpa-phoenix", token)
    result = b.request_auth_code_for_recipient(
        "service:cpa-phoenix",
        "OWNER@EXAMPLE.TEST",
        "https://auth.openai.com",
        "cpa-phoenix-revive",
    )
    assert result["code"] == "123456"
    assert result["provenance"]["account_id"] == "opaque-mailbox"


def test_request_verification_link_for_recipient_is_scoped_one_time_and_redacted():
    b = Broker(Store(":memory:", key=b"w" * 32))
    b.add_source("opaque-mailbox", "email", "Owner")
    b.store.put(
        "connection",
        "opaque-mailbox",
        {
            "source_id": "opaque-mailbox",
            "kind": "imap",
            "username": "owner@example.test",
        },
    )
    link = "https://auth.example.test/verify?token=synthetic-link-token"
    body = f"Click this verification link: {link}. Do not share this message."
    b.email["opaque-mailbox"] = [
        EmailMessage(
            "verification-1",
            "opaque-mailbox",
            "no-reply@auth.example.test",
            "Verify your account",
            body,
            datetime.now(timezone.utc).isoformat(),
        )
    ]
    b.grant(
        "codex",
        "email.verification_link",
        "opaque-mailbox",
        "https://auth.example.test",
        "verify",
    )

    result = b.request_verification_link_for_recipient(
        "codex",
        "OWNER@EXAMPLE.TEST",
        "https://auth.example.test/finish",
        "verify",
    )

    assert result["url"] == link
    assert result["provenance"]["message_id"] == "verification-1"
    assert body not in str(result)
    audits = b.store.audits()
    assert link not in str(audits)
    with pytest.raises(BrokerError, match="already consumed"):
        b.request_verification_link_for_recipient(
            "codex",
            "owner@example.test",
            "https://auth.example.test",
            "verify",
        )


def test_request_verification_link_refuses_wrong_origin_ambiguity_and_expiry():
    b = Broker(Store(":memory:", key=b"k" * 32))
    b.add_source("mail", "email", "Owner")
    b.store.put(
        "connection",
        "mail",
        {"source_id": "mail", "kind": "imap", "username": "owner@example.test"},
    )
    received = datetime.now(timezone.utc).isoformat()
    b.email["mail"] = [
        EmailMessage(
            "verification-2",
            "mail",
            "no-reply@auth.example.test",
            "Verify",
            "https://evil.example/verify?token=wrong",
            received,
        )
    ]
    b.grant(
        "codex",
        "email.verification_link",
        "mail",
        "auth.example.test",
        "verify",
    )
    with pytest.raises(BrokerError, match="not bound|no unambiguous"):
        b.request_verification_link_for_recipient(
            "codex", "owner@example.test", "auth.example.test", "verify"
        )
    with pytest.raises(BrokerError, match="between 1 and 3600"):
        b.request_verification_link_for_recipient(
            "codex",
            "owner@example.test",
            "auth.example.test",
            "verify",
            max_age_seconds=0,
        )


def test_verification_link_does_not_reuse_an_otp_grant():
    b = Broker(Store(":memory:", key=b"v" * 32))
    b.add_source("mail", "email", "Owner")
    b.store.put(
        "connection",
        "mail",
        {"source_id": "mail", "kind": "imap", "username": "owner@example.test"},
    )
    b.email["mail"] = [
        EmailMessage(
            "verification-3",
            "mail",
            "no-reply@auth.example.test",
            "Verify",
            "https://auth.example.test/verify?token=synthetic",
            datetime.now(timezone.utc).isoformat(),
        )
    ]
    b.grant("codex", "email.otp", "mail", "auth.example.test", "verify")
    with pytest.raises(AuthorizationError):
        b.request_verification_link_for_recipient(
            "codex", "owner@example.test", "auth.example.test", "verify"
        )


def test_grant_email_recipient_hides_opaque_source_id_from_owner_cli_flow():
    b = Broker(Store(":memory:", key=b"e" * 32))
    b.add_source("opaque-mailbox", "email", "Owner")
    b.store.put(
        "connection",
        "opaque-mailbox",
        {"source_id": "opaque-mailbox", "kind": "imap", "username": "owner@example.test"},
    )
    grant = b.grant_email_recipient(
        "service:cpa-phoenix",
        "email.otp",
        "owner@example.test",
        "https://auth.openai.com",
        "cpa-phoenix-revive",
    )
    assert grant.resource_scope == "opaque-mailbox"


def test_grant_email_all_creates_explicit_wildcard_for_owner_choice():
    b = Broker(Store(":memory:", key=b"a" * 32))
    grant = b.grant_email_all(
        "service:cpa-phoenix",
        "email.otp",
        "https://auth.openai.com",
        "cpa-phoenix-revive",
    )
    assert grant.resource_scope == "*"


def test_otp_is_separate_and_not_logged():
    b = setup()
    b.grant("codex", "email.otp", "mail1", "example.com", "login")
    out = b.request_auth_code("codex", "mail1", "example.com", "login")
    assert out["code"] == "123456"
    assert out["provenance"] == {
        "source": "email",
        "account_id": "mail1",
        "message_id": "m1",
        "received_at": b.email["mail1"][0].received_at,
    }
    assert "expires_in" not in out
    assert "123456" not in str(b.store.audits())
    rows = b.store.db.execute(
        "select namespace,key,value from kv where namespace like 'otp-%'"
    ).fetchall()
    assert "123456" not in str(rows)


def test_otp_ambiguity_rejected():
    b = setup()
    b.email["mail1"].append(
        EmailMessage(
            "m2",
            "mail1",
            "x",
            "x",
            "code 654321",
            datetime.now(timezone.utc).isoformat(),
        )
    )
    b.grant("codex", "email.otp", "mail1", "example.com", "login")
    with pytest.raises(BrokerError):
        b.request_auth_code("codex", "mail1", "example.com", "login")


def test_otp_is_one_time_and_agent_revocation_is_immediate():
    b = setup()
    b.grant("codex", "email.otp", "mail1", "example.com", "login")
    b.request_auth_code("codex", "mail1", "example.com", "login")
    with pytest.raises(BrokerError):
        b.request_auth_code("codex", "mail1", "example.com", "login")
    b.grant("codex2", "email.search", "mail1", None, None)
    b.revoke_agent("codex2")
    with pytest.raises(AuthorizationError):
        b.search_email("codex2", "mail1", "login")


def test_otp_consumption_is_shared_across_broker_instances(tmp_path):
    path = tmp_path / "broker.sqlite"
    key = b"p" * 32
    first = Broker(Store(path, key=key))
    second = Broker(Store(path, key=key))
    message = EmailMessage(
        "m1",
        "mail1",
        "no-reply@example.com",
        "Login code for example.com",
        "Your code is 123456",
        datetime.now(timezone.utc).isoformat(),
    )
    for broker in (first, second):
        broker.add_source("mail1", "email", "Personal")
        broker.email["mail1"] = [message]
    first.grant("codex", "email.otp", "mail1", "example.com", "login")

    first.request_auth_code("codex", "mail1", "example.com", "login")

    with pytest.raises(BrokerError, match="already consumed"):
        second.request_auth_code("codex", "mail1", "example.com", "login")


def test_otp_requires_target_origin_binding():
    b = setup()
    b.grant("codex", "email.otp", "mail1", "evil.example", "login")
    with pytest.raises(BrokerError):
        b.request_auth_code("codex", "mail1", "evil.example", "login")


def test_audit_storage_does_not_expose_search_terms():
    b = setup()
    b.grant("codex", "email.search", "mail1", None, None)
    b.search_email("codex", "mail1", "private phrase")
    raw = b.store.db.execute("select metadata from audit").fetchone()[0]
    assert "private phrase" not in raw


def test_agent_token_authentication_and_revocation():
    b = setup()
    token = b.create_agent("codex")
    assert b.authenticate_agent("codex", token)
    assert not b.authenticate_agent("codex", "wrong")
    b.revoke_agent("codex")
    assert not b.authenticate_agent("codex", token)


def test_credential_use_returns_handle_without_password_and_handle_is_one_time():
    b = setup()
    b.put_credential("c1", "Example", "https://example.com/login", "ada", "secret")
    b.grant("codex", "credential.use", "c1", "https://example.com", "form")
    out = b.request_credential_use("codex", "c1", "https://example.com", "form")
    assert (
        out["username"] == "ada" and "password" not in out and "secret" not in str(out)
    )
    consumed = b.consume_credential_handle(out["handle"], "codex")
    assert consumed["password"] == "secret"
    with pytest.raises(BrokerError):
        b.consume_credential_handle(out["handle"], "codex")


def test_credential_handle_origin_is_checked_before_consumption():
    b = setup()
    b.put_credential("c1", "Example", "https://example.com/login", "ada", "secret")
    b.grant("codex", "credential.use", "c1", "https://example.com", "form")
    out = b.request_credential_use("codex", "c1", "https://example.com", "form")

    with pytest.raises(CredentialUseError, match="target origin"):
        b.consume_credential_handle(out["handle"], "codex", "https://evil.test")

    consumed = b.consume_credential_handle(
        out["handle"], "codex", "https://example.com/checkout"
    )
    assert consumed["password"] == "secret"


def test_unified_credential_direct_returns_only_selected_fields_and_provenance():
    b = Broker(Store(":memory:", key=b"d" * 32))
    b.put_credential(
        "c1", "Example", "https://example.com/login", "ada", "synthetic-password"
    )
    b.grant("codex", "credential.read", "c1", "https://example.com", "form")

    result = b.request_credential(
        "codex",
        "direct",
        "c1",
        ["username"],
        "https://example.com/checkout",
        "form",
    )

    assert result["username"] == "ada"
    assert "password" not in result
    assert result["provenance"]["credential_id"] == "c1"
    assert "synthetic-password" not in str(result)


def test_unified_credential_extension_issues_existing_handle_metadata_only():
    b = Broker(Store(":memory:", key=b"x" * 32))
    b.put_credential(
        "c1", "Example", "https://example.com/login", "ada", "synthetic-password"
    )
    b.grant("codex", "credential.use", "c1", "https://example.com", "form")

    result = b.request_credential(
        "codex",
        "extension",
        "c1",
        target_origin="https://example.com/checkout",
        purpose="form",
        expires_in=30,
    )

    assert set(result) == {"handle", "username", "origin", "expires_in", "provenance"}
    assert result["expires_in"] == 30
    assert "password" not in result
    assert "synthetic-password" not in str(result)


def test_unified_credential_validates_mode_selection_before_delegation():
    b = Broker(Store(":memory:", key=b"v" * 32))
    b.put_credential(
        "c1", "Example", "https://example.com/login", "ada", "synthetic-password"
    )

    with pytest.raises(BrokerError, match="exactly direct or extension"):
        b.request_credential("codex", "automatic", "c1", ["username"])
    with pytest.raises(BrokerError, match="1 to 5"):
        b.request_credential(
            "codex", "direct", "c1", ["name", "origin", "username", "password", "note", "extra"]
        )
    with pytest.raises(BrokerError, match="not accepted"):
        b.request_credential(
            "codex", "extension", "c1", [], "https://example.com", "form"
        )
    with pytest.raises(BrokerError, match="between 1 and 300"):
        b.request_credential(
            "codex",
            "extension",
            "c1",
            target_origin="https://example.com",
            purpose="form",
            expires_in=301,
        )


def test_unified_credential_extension_keeps_grant_origin_expiry_and_replay_boundaries():
    b = Broker(Store(":memory:", key=b"y" * 32))
    b.put_credential(
        "c1", "Example", "https://example.com/login", "ada", "synthetic-password"
    )
    with pytest.raises(AuthorizationError):
        b.request_credential(
            "codex",
            "extension",
            "c1",
            target_origin="https://example.com",
            purpose="form",
        )

    b.grant("codex", "credential.use", "c1", "https://example.com", "form")
    with pytest.raises(AuthorizationError):
        b.request_credential(
            "codex",
            "extension",
            "c1",
            target_origin="https://evil.example",
            purpose="form",
        )
    handle = b.request_credential(
        "codex",
        "extension",
        "c1",
        target_origin="https://example.com",
        purpose="form",
    )
    b.consume_credential_handle(handle["handle"], "codex", "https://example.com")
    with pytest.raises(CredentialUseError, match="invalid, expired, or already consumed"):
        b.consume_credential_handle(handle["handle"], "codex", "https://example.com")


def test_match_credentials_uses_exact_host_and_username_hint_without_password():
    b = Broker(Store(":memory:", key=b"c" * 32))
    b.put_credential(
        "example-ada",
        "Example",
        "https://example.com/login",
        "ada@example.com",
        "synthetic-password",
    )
    b.put_credential(
        "example-bob",
        "Example",
        "https://example.com/login",
        "bob@example.com",
        "other-password",
    )
    b.put_credential(
        "subdomain-ada",
        "Subdomain",
        "https://login.example.com",
        "ada@example.com",
        "sub-password",
    )

    matches = b.match_credentials("https://example.com/checkout", "ADA@EXAMPLE")

    assert [item["credential_id"] for item in matches] == ["example-ada"]
    assert matches[0]["match"] == "exact-host"
    assert "password" not in matches[0]
    assert "synthetic-password" not in str(matches)


def test_profile_groups_are_metadata_only_and_local_profiles_are_fallback_groups():
    b = Broker(Store(":memory:", key=b"g" * 32))
    provenance = {
        "source": "chrome-structured-address",
        "source_profile_id": "chrome-profile-fixture",
        "source_guid_hash": "guid-fixture",
        "field_type": "streetAddress",
    }
    b.put_profile("street", "address", "Street", "123 Secret St", provenance=provenance)
    b.put_profile(
        "city",
        "address",
        "City",
        "Secretville",
        provenance={**provenance, "field_type": "addressLevel2"},
    )
    b.put_profile("nickname", "identity", "Nickname", "Ada")

    groups = b.list_profile_groups()
    chrome_group = next(item for item in groups if item["field_count"] == 2)
    local_group = next(item for item in groups if item["field_count"] == 1)

    assert chrome_group["source"] == "chrome-structured-address"
    assert chrome_group["source_profile_id"] == "chrome-profile-fixture"
    assert chrome_group["field_ids"] == ["city", "street"]
    assert set(chrome_group["field_types"]) == {"addressLevel2", "streetAddress"}
    assert local_group["source"] == "local-profile"
    assert "Secret St" not in str(groups)
    assert "Secretville" not in str(groups)


def test_metadata_list_filters_are_bounded_and_value_free():
    b = Broker(Store(":memory:", key=b"f" * 32))
    b.put_profile("first_name", "identity", "First name", "Ada")
    b.put_profile("street", "address", "Street", "123 Secret St")
    b.put_credential(
        "example-login",
        "Example",
        "https://example.test/login",
        "ada@example.test",
        "synthetic-password",
    )
    b.put_financial_record(
        "card",
        "payment_card",
        "Travel card",
        {"card_number": "4111111111111111", "expiration_year": "2031"},
    )

    identity = b.list_profile_fields(category="identity", limit=1)
    assert [item["field_id"] for item in identity] == ["first_name"]
    assert "Ada" not in str(identity)
    assert [item["credential_id"] for item in b.list_credentials(target_origin="https://example.test")] == [
        "example-login"
    ]
    assert "synthetic-password" not in str(b.list_credentials(query="example"))
    cards = b.list_financial_records(kind="payment_card", query="travel", limit=1)
    assert [item["record_id"] for item in cards] == ["card"]
    assert "4111111111111111" not in str(cards)


def test_source_removal_revokes_local_route():
    b = setup()
    b.grant("codex", "email.search", "mail1", None, None)
    assert b.list_sources()
    b.remove_source("mail1")
    assert b.list_sources() == []
    with pytest.raises(AuthorizationError):
        b.search_email("codex", "mail1", "login")


def test_source_removal_blocks_new_wildcard_grants_from_using_memory_mail():
    b = setup()
    b.remove_source("mail1")
    b.grant("codex", "email.search", "*", None, None)
    with pytest.raises(BrokerError, match="not configured"):
        b.search_email("codex", "mail1", "login")


@pytest.mark.parametrize(
    "binding_text, should_match",
    [
        ("Visit https://example.com/login to finish", True),
        ("Sender: no-reply@example.com", True),
        ("Visit https://notexample.com/login to finish", False),
        ("Visit https://example.com.evil/login to finish", False),
    ],
)
def test_otp_hostname_binding_requires_exact_token_boundaries(
    binding_text, should_match
):
    b = setup()
    b.email["mail1"] = [
        EmailMessage(
            "binding",
            "mail1",
            "no-reply@sender.test",
            "Authentication code",
            f"{binding_text}; your code is 123456",
            datetime.now(timezone.utc).isoformat(),
        )
    ]
    b.grant("codex", "email.otp", "mail1", "https://example.com", "login")
    if should_match:
        assert b.request_auth_code(
            "codex", "mail1", "https://example.com", "login"
        )["code"] == "123456"
    else:
        with pytest.raises(BrokerError, match="not bound"):
            b.request_auth_code("codex", "mail1", "https://example.com", "login")


def test_source_removal_tombstones_unavailable_source_against_memory_fallback():
    b = setup()
    b.mark_email_unavailable("mail1")
    b.remove_source("mail1")
    b.grant("codex", "email.search", "*", None, None)
    with pytest.raises(BrokerError, match="not configured"):
        b.search_email("codex", "mail1", "login")


def _record_broker():
    broker = Broker(Store(":memory:", key=b"r" * 32))
    broker.grant("codex", "profile.write", "legal_name", "example.com", "form")
    broker.grant("codex", "credential.write", "example-login", "example.com", "form")
    broker.grant("codex", "financial.write", "primary-card", "example.com", "form")
    return broker


def _financial_entry(record_id="primary-card"):
    return {
        "record_type": "financial",
        "record_id": record_id,
        "kind": "payment_card",
        "display_name": "Primary card",
        "values": {"card_number": "4111111111111111"},
    }


def _expire_grants(broker, agent_id):
    for row in broker.store.db.execute(
        "select key,value from kv where namespace='grant'"
    ).fetchall():
        grant = broker.store._open(row["value"])
        if grant["agent_id"] == agent_id:
            grant["expires_at"] = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
            broker.store.put("grant", row["key"], grant)


def test_write_records_is_atomic_scoped_and_metadata_only():
    broker = _record_broker()
    result = broker.write_records(
        "codex",
        "https://example.com",
        "form",
        True,
        [
            {
                "record_type": "profile",
                "record_id": "legal_name",
                "category": "identity",
                "display_name": "Legal name",
                "value": "Ada Lovelace",
                "allowed_origins": ["https://example.com"],
                "allowed_purposes": ["form"],
            },
            {
                "record_type": "credential",
                "record_id": "example-login",
                "name": "Example login",
                "origin": "https://example.com/login",
                "username": "ada",
                "password": "synthetic-password",
            },
            {
                "record_type": "financial",
                "record_id": "primary-card",
                "kind": "payment_card",
                "display_name": "Primary card",
                "values": {
                    "card_number": "4111111111111111",
                    "security_code": "123",
                },
                "matching_hints": {"network": "Visa"},
            },
        ],
    )

    assert [item["status"] for item in result["records"]] == [
        "created",
        "created",
        "created",
    ]
    assert "synthetic-password" not in str(broker.list_credentials())
    metadata = broker.list_financial_records()[0]
    assert metadata["matching_hints"] == {"network": "Visa", "last4": "1111"}
    assert "values" not in metadata
    assert "4111111111111111" not in str(metadata)
    raw = str(broker.store.db.execute("select value from kv").fetchall())
    assert "synthetic-password" not in raw and "4111111111111111" not in raw
    audits = str(broker.store.audits())
    assert "synthetic-password" not in audits and "4111111111111111" not in audits

    unchanged = broker.write_records(
        "codex",
        "example.com",
        "form",
        True,
        [
            {
                "record_type": "financial",
                "record_id": "primary-card",
                "kind": "payment_card",
                "display_name": "Primary card",
                "values": {
                    "card_number": "4111111111111111",
                    "security_code": "123",
                },
                "matching_hints": {"network": "Visa"},
            }
        ],
    )
    assert unchanged["records"][0]["status"] == "unchanged"

    updated = broker.write_records(
        "codex",
        "example.com",
        "form",
        True,
        [
            {
                "record_type": "financial",
                "record_id": "primary-card",
                "kind": "payment_card",
                "display_name": "Primary card",
                "values": {
                    "card_number": "5555555555554444",
                    "security_code": "321",
                },
                "matching_hints": {"network": "Mastercard"},
            }
        ],
    )
    assert updated["records"][0]["status"] == "updated"
    assert broker.list_financial_records()[0]["matching_hints"]["last4"] == "4444"


def test_put_payment_card_normalizes_and_keeps_values_out_of_metadata():
    broker = Broker(Store(":memory:", key=b"p" * 32))

    broker.put_payment_card(
        "platinum-card",
        "Travel Platinum",
        "4111 1111-1111 1111",
        "5",
        "2031",
        security_code="123",
        additional_security_code="719",
        cardholder_name="Ada Lovelace",
        billing_postal_code="98101",
        network="Visa",
        allowed_origins=["https://www.example.test"],
        allowed_purposes=["hotel booking"],
    )

    metadata = broker.match_financial_records(
        kind="payment_card",
        matching_hints={
            "network": "visa",
            "last4": "1111",
            "expiration_month": "05",
            "expiration_year": "2031",
        },
    )
    assert metadata[0]["record_id"] == "platinum-card"
    assert "values" not in metadata[0]
    assert "4111111111111111" not in str(metadata)
    raw = str(broker.store.db.execute("select value from kv").fetchall())
    assert "4111111111111111" not in raw

    broker.grant(
        "codex",
        "financial.read",
        "platinum-card",
        "https://www.example.test",
        "hotel booking",
    )
    fields = broker.request_financial_fields(
        "codex",
        "platinum-card",
        [
            "card_number",
            "security_code",
            "additional_security_code",
            "expiration_month",
        ],
        "https://www.example.test/checkout",
        "hotel booking",
    )
    assert fields["card_number"] == "4111111111111111"
    assert fields["security_code"] == "123"
    assert fields["additional_security_code"] == "719"
    assert fields["expiration_month"] == "05"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("card_number", "too-short", "card number"),
        ("expiration_month", "13", "expiration month"),
        ("expiration_year", "31", "expiration year"),
        ("security_code", "12", "security code"),
        ("additional_security_code", "1234", "additional security code"),
    ],
)
def test_put_payment_card_rejects_malformed_sensitive_fields(field, value, message):
    broker = Broker(Store(":memory:", key=b"q" * 32))
    kwargs = {
        "record_id": "card",
        "display_name": "Card",
        "card_number": "4111111111111111",
        "expiration_month": "05",
        "expiration_year": "2031",
    }
    if field == "card_number":
        kwargs["card_number"] = value
    elif field == "expiration_month":
        kwargs["expiration_month"] = value
    elif field == "expiration_year":
        kwargs["expiration_year"] = value
    else:
        kwargs[field] = value

    with pytest.raises(BrokerError, match=message):
        broker.put_payment_card(**kwargs)
    assert broker.list_financial_records() == []


def test_write_records_rejects_confirmation_transient_fields_and_rolls_back():
    broker = _record_broker()
    with pytest.raises(BrokerError, match="confirmed"):
        broker.write_records(
            "codex",
            "example.com",
            "form",
            False,
            [
                {
                    "record_type": "financial",
                    "record_id": "primary-card",
                    "kind": "payment_card",
                    "display_name": "Primary card",
                    "values": {"card_number": "4111111111111111"},
                }
            ],
        )
    with pytest.raises(BrokerError, match="transient") as exc_info:
        broker.write_records(
            "codex",
            "example.com",
            "form",
            True,
            [
                {
                    "record_type": "profile",
                    "record_id": "legal_name",
                    "category": "identity",
                    "display_name": "Legal name",
                    "value": "Ada Lovelace",
                    "otp": "synthetic-code",
                }
            ],
        )
    assert "synthetic-code" not in str(exc_info.value)
    with pytest.raises(BrokerError, match="profile category"):
        broker.write_records(
            "codex",
            "example.com",
            "form",
            True,
            [
                {
                    "record_type": "profile",
                    "record_id": "billing_label",
                    "category": "payment",
                    "display_name": "Payment",
                    "value": "not-a-card",
                }
            ],
        )
    assert broker.store.get("profile", "legal_name") is None
    assert broker.store.get("financial", "primary-card") is None


def test_write_records_rejects_invalid_batches_and_grant_boundaries():
    broker = _record_broker()
    entry = _financial_entry()

    with pytest.raises(BrokerError, match="duplicate"):
        broker.write_records(
            "codex", "example.com", "form", True, [entry, dict(entry)]
        )
    with pytest.raises(BrokerError, match="1 to 100"):
        broker.write_records(
            "codex", "example.com", "form", True, [entry] * 101
        )
    with pytest.raises(BrokerError, match="unsupported"):
        broker.write_records(
            "codex",
            "example.com",
            "form",
            True,
            [{**entry, "kind": "crypto_wallet"}],
        )
    with pytest.raises(AuthorizationError):
        broker.write_records("codex", "other.example", "form", True, [entry])
    with pytest.raises(AuthorizationError):
        broker.write_records("codex", "example.com", "checkout", True, [entry])

    atomic = Broker(Store(":memory:", key=b"a" * 32))
    atomic.grant("codex", "profile.write", "legal_name", "example.com", "form")
    with pytest.raises(AuthorizationError):
        atomic.write_records(
            "codex",
            "example.com",
            "form",
            True,
            [
                {
                    "record_type": "profile",
                    "record_id": "legal_name",
                    "category": "identity",
                    "display_name": "Legal name",
                    "value": "Ada Lovelace",
                },
                entry,
            ],
        )
    assert atomic.store.get("profile", "legal_name") is None

    expired = Broker(Store(":memory:", key=b"e" * 32))
    expired.grant(
        "expired", "financial.write", "primary-card", "example.com", "form"
    )
    _expire_grants(expired, "expired")
    with pytest.raises(AuthorizationError):
        expired.write_records(
            "expired", "example.com", "form", True, [entry]
        )

    revoked = Broker(Store(":memory:", key=b"v" * 32))
    revoked.grant(
        "revoked", "financial.write", "primary-card", "example.com", "form"
    )
    revoked.revoke_agent("revoked")
    with pytest.raises(AuthorizationError):
        revoked.write_records(
            "revoked", "example.com", "form", True, [entry]
        )


def test_direct_sensitive_reads_are_field_selective_and_delete_revokes_access():
    broker = _record_broker()
    broker.write_records(
        "codex",
        "https://example.com",
        "form",
        True,
        [
            {
                "record_type": "credential",
                "record_id": "example-login",
                "name": "Example login",
                "origin": "https://example.com",
                "username": "ada",
                "password": "synthetic-password",
            },
            {
                "record_type": "financial",
                "record_id": "primary-card",
                "kind": "payment_card",
                "display_name": "Primary card",
                "values": {
                    "card_number": "4111111111111111",
                    "security_code": "123",
                },
            },
        ],
    )
    broker.grant("codex", "credential.read", "example-login", "example.com", "form")
    broker.grant("codex", "financial.read", "primary-card", "example.com", "form")

    credential = broker.request_credential_fields(
        "codex", "example-login", ["username"], "https://example.com", "form"
    )
    assert credential["username"] == "ada"
    assert "password" not in credential
    financial = broker.request_financial_fields(
        "codex", "primary-card", ["card_number"], "https://example.com", "form"
    )
    assert financial["card_number"] == "4111111111111111"
    assert "security_code" not in financial
    with pytest.raises(AuthorizationError):
        broker.request_financial_fields(
            "codex", "primary-card", ["card_number"], "https://other.example", "form"
        )

    broker.delete_record("financial", "primary-card")
    assert broker.store.get("financial", "primary-card") is None
    with pytest.raises(BrokerError):
        broker.request_financial_fields(
            "codex", "primary-card", ["card_number"], "https://example.com", "form"
        )
    grants = broker.list_grants()
    assert any(
        item["operation"] == "financial.read"
        and item["resource_scope"] == "primary-card"
        and item["revoked"]
        for item in grants
    )


def test_sensitive_read_refusals_and_audits_do_not_expose_values():
    broker = Broker(Store(":memory:", key=b"s" * 32))
    broker.put_financial_record(
        "primary-card",
        "payment_card",
        "Primary card",
        {"card_number": "4111111111111111", "security_code": "123"},
        allowed_origins=["example.com"],
        allowed_purposes=["form"],
    )
    broker.grant("codex", "financial.read", "primary-card", "example.com", "form")

    with pytest.raises(BrokerError, match="unknown financial field"):
        broker.request_financial_fields(
            "codex", "primary-card", ["missing_field"], "example.com", "form"
        )
    with pytest.raises(AuthorizationError):
        broker.request_financial_fields(
            "codex", "primary-card", ["card_number"], "example.com", "checkout"
        )

    broker.grant(
        "wrong-resource", "financial.read", "other-card", "example.com", "form"
    )
    with pytest.raises(AuthorizationError):
        broker.request_financial_fields(
            "wrong-resource",
            "primary-card",
            ["card_number"],
            "example.com",
            "form",
        )

    broker.grant(
        "expired", "financial.read", "primary-card", "example.com", "form"
    )
    _expire_grants(broker, "expired")
    with pytest.raises(AuthorizationError):
        broker.request_financial_fields(
            "expired", "primary-card", ["card_number"], "example.com", "form"
        )

    broker.grant(
        "revoked", "financial.read", "primary-card", "example.com", "form"
    )
    broker.revoke_agent("revoked")
    with pytest.raises(AuthorizationError):
        broker.request_financial_fields(
            "revoked", "primary-card", ["card_number"], "example.com", "form"
        )

    broker.grant(
        "record-scope",
        "financial.read",
        "primary-card",
        "example.com",
        "other",
    )
    with pytest.raises(AuthorizationError, match="not allowed for record"):
        broker.request_financial_fields(
            "record-scope",
            "primary-card",
            ["card_number"],
            "example.com",
            "other",
        )

    result = broker.request_financial_fields(
        "codex", "primary-card", ["card_number"], "example.com", "form"
    )
    assert result["card_number"] == "4111111111111111"
    audit_metadata = [entry["metadata"] for entry in broker.store.audits()]
    assert "4111111111111111" not in str(audit_metadata)
    assert "123" not in str(audit_metadata)


def test_financial_metadata_matching_disambiguates_expiration_without_values():
    broker = Broker(Store(":memory:", key=b"m" * 32))
    for record_id, month, year in (
        ("amex-1007-old", "05", "2027"),
        ("amex-1007-current", "05", "2031"),
    ):
        broker.put_financial_record(
            record_id,
            "payment_card",
            "Amex Business Platinum",
            {
                "card_number": "371378000000007",
                "expiration_month": month,
                "expiration_year": year,
                "security_code": "1234",
            },
            matching_hints={"network": "amex", "last4": "1007"},
        )

    matches = broker.match_financial_records(
        kind="payment_card",
        matching_hints={
            "network": "amex",
            "last4": "1007",
            "expiration_month": "05",
            "expiration_year": "2031",
        },
    )
    assert [item["record_id"] for item in matches] == ["amex-1007-current"]
    assert "values" not in matches[0]
    assert "371378000000007" not in str(matches[0])


def test_legacy_credential_defaults_preserve_reads_and_one_time_handles():
    broker = Broker(Store(":memory:", key=b"l" * 32))
    broker.store.put(
        "credential",
        "legacy-login",
        {
            "credential_id": "legacy-login",
            "name": "Legacy login",
            "origin": "https://example.com/login",
            "username": "ada",
            "password": "synthetic-password",
            "note": "",
        },
    )
    metadata = broker.list_credentials()[0]
    assert metadata["allowed_origins"] == ["https://example.com"]
    assert metadata["sensitivity"] == "credential"

    broker.grant(
        "codex", "credential.read", "legacy-login", "example.com", "form"
    )
    broker.grant(
        "codex", "credential.use", "legacy-login", "example.com", "form"
    )
    direct = broker.request_credential_fields(
        "codex", "legacy-login", ["password"], "example.com", "form"
    )
    assert direct["password"] == "synthetic-password"
    handle = broker.request_credential_use(
        "codex", "legacy-login", "example.com", "form"
    )
    assert "password" not in handle

    broker.delete_record("credential", "legacy-login")
    with pytest.raises(BrokerError):
        broker.consume_credential_handle(handle["handle"], "codex")
    assert all(
        grant["revoked"]
        for grant in broker.list_grants()
        if grant["resource_scope"] == "legacy-login"
    )
