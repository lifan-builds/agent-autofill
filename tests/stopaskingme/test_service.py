from datetime import datetime, timedelta, timezone
import pytest
from stopaskingme.service import Broker, AuthorizationError, BrokerError
from stopaskingme.models import EmailMessage, TextMessage
from stopaskingme.storage import Store


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
    assert "4111111111111111" not in str(broker.store.audits())
    assert "123" not in str(broker.store.audits())


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
