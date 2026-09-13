from datetime import datetime, timezone
import pytest
from stopaskingme.service import Broker, AuthorizationError, BrokerError
from stopaskingme.models import EmailMessage, TextMessage


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
    assert "123456" not in str(b.store.audits())


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
