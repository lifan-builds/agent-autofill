from email.message import EmailMessage as MIMEEmailMessage

import pytest

from agent_autofill.imap_adapter import (
    IMAPAuthenticationError,
    IMAPEmailAdapter,
    IMAPMailboxError,
    _date,
)


class FakeConnection:
    def __init__(self, login_error=None, select_status="OK", auth_status="OK"):
        self.login_error = login_error
        self.select_status = select_status
        self.auth_status = auth_status
        self.login_calls = []
        self.auth_calls = []
        self.select_calls = []
        self.logout_calls = 0

    def login(self, username, password):
        self.login_calls.append((username, password))
        if self.login_error:
            raise self.login_error
        return self.auth_status, []

    def authenticate(self, mechanism, callback):
        self.auth_calls.append((mechanism, callback(None)))
        if self.login_error:
            raise self.login_error
        return self.auth_status, []

    def select(self, mailbox, readonly=False):
        self.select_calls.append((mailbox, readonly))
        return self.select_status, []

    def logout(self):
        self.logout_calls += 1
        return "BYE", []


def test_check_connection_authenticates_readonly_and_logs_out(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(
        "agent_autofill.imap_adapter.imaplib.IMAP4_SSL",
        lambda host, port: connection,
    )

    IMAPEmailAdapter(
        "mail",
        "imap.example.test",
        "user@example.test",
        password="fixture-password",
    ).check_connection()

    assert connection.login_calls == [("user@example.test", "fixture-password")]
    assert connection.select_calls == [("INBOX", True)]
    assert connection.logout_calls == 1


def test_check_connection_uses_xoauth2_without_printing_token(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(
        "agent_autofill.imap_adapter.imaplib.IMAP4_SSL",
        lambda host, port: connection,
    )

    IMAPEmailAdapter(
        "mail",
        "imap.example.test",
        "user@example.test",
        oauth2_token="fixture-bearer-token",
    ).check_connection()

    assert connection.auth_calls == [
        (
            "XOAUTH2",
            b"user=user@example.test\x01auth=Bearer fixture-bearer-token\x01\x01",
        )
    ]
    assert "fixture-bearer-token" not in repr(
        IMAPEmailAdapter(
            "mail",
            "imap.example.test",
            "user@example.test",
            oauth2_token="fixture-bearer-token",
        )
    )
    assert connection.logout_calls == 1


def test_check_connection_reports_safe_auth_and_mailbox_failures(monkeypatch):
    auth_connection = FakeConnection(login_error=RuntimeError("provider detail"))
    monkeypatch.setattr(
        "agent_autofill.imap_adapter.imaplib.IMAP4_SSL",
        lambda host, port: auth_connection,
    )
    with pytest.raises(IMAPAuthenticationError, match="IMAP authentication failed"):
        IMAPEmailAdapter("mail", "imap.example.test", "user", password="fixture").check_connection()
    assert auth_connection.logout_calls == 1

    mailbox_connection = FakeConnection(select_status="NO")
    monkeypatch.setattr(
        "agent_autofill.imap_adapter.imaplib.IMAP4_SSL",
        lambda host, port: mailbox_connection,
    )
    with pytest.raises(IMAPMailboxError, match="IMAP mailbox selection failed"):
        IMAPEmailAdapter("mail", "imap.example.test", "user", password="fixture").check_connection()
    assert mailbox_connection.logout_calls == 1


def test_check_connection_rejects_non_ok_login_and_xoauth2_status(monkeypatch):
    password_connection = FakeConnection(auth_status="NO")
    monkeypatch.setattr(
        "agent_autofill.imap_adapter.imaplib.IMAP4_SSL",
        lambda host, port: password_connection,
    )
    with pytest.raises(IMAPAuthenticationError, match="IMAP authentication failed"):
        IMAPEmailAdapter("mail", "imap.example.test", "user", password="fixture").check_connection()
    assert password_connection.logout_calls == 1

    oauth_connection = FakeConnection(auth_status="BAD")
    monkeypatch.setattr(
        "agent_autofill.imap_adapter.imaplib.IMAP4_SSL",
        lambda host, port: oauth_connection,
    )
    with pytest.raises(IMAPAuthenticationError, match="IMAP authentication failed"):
        IMAPEmailAdapter(
            "mail", "imap.example.test", "user", oauth2_token="fixture-token"
        ).check_connection()
    assert oauth_connection.logout_calls == 1


def test_parse_extracts_auth_text_from_html_only_message():
    message = MIMEEmailMessage()
    message["From"] = "ChatGPT <noreply@tm.openai.com>"
    message["Subject"] = "Your temporary ChatGPT login code"
    message["Date"] = "Sun, 11 Oct 2026 18:13:47 +0000"
    message.set_content(
        "<html><body><p>Your temporary ChatGPT login code</p>"
        "<div>123456</div><p>auth.openai.com</p></body></html>",
        subtype="html",
    )

    parsed = IMAPEmailAdapter("mail", "imap.example.test", "user")._parse(
        b"42", message.as_bytes()
    )

    assert "Your temporary ChatGPT login code" in parsed.body
    assert "123456" in parsed.body
    assert "auth.openai.com" in parsed.body
    assert "<div>" not in parsed.body


def test_parse_prefers_plain_text_alternative_over_html():
    message = MIMEEmailMessage()
    message["From"] = "ChatGPT <noreply@tm1.openai.com>"
    message["Subject"] = "Your temporary ChatGPT login code"
    message["Date"] = "Sun, 11 Oct 2026 18:13:47 +0000"
    message.set_content("Your temporary ChatGPT login code: 654321")
    message.add_alternative(
        "<html><body><div>different rendered copy</div></body></html>",
        subtype="html",
    )

    parsed = IMAPEmailAdapter("mail", "imap.example.test", "user")._parse(
        b"43", message.as_bytes()
    )

    assert parsed.body == "Your temporary ChatGPT login code: 654321\n"


def test_invalid_provider_date_does_not_become_current_time():
    assert _date("not-a-date") is None
    assert _date(None) is None


def test_parse_strips_style_script_head_and_collapses_preheader_spacers():
    css_bloat = (
        "@import url('https://fonts.example.test/css');"
        "@media only screen and (max-width:600px){.hero{color:#111827 !important;}}"
        * 40
    )
    preheader_padding = ("\u200c\u00a0\u034f\u200b\ufeff " * 400)
    html = (
        f"<html><head><title>Hidden Title</title><style>{css_bloat}</style></head>"
        f"<body><style>.inline-css{{background:#991122}}</style>"
        f"<script>window.__SECRET__='ignore-me';</script>"
        f"<div>Preview summary{preheader_padding}</div>"
        f"<p>Confirmation number: <b>AAA-90210</b></p>"
        f"<p>Verification code: <span>482910</span></p></body></html>"
    )
    message = MIMEEmailMessage()
    message["From"] = "AAA Membership <noreply@aaamembershipoffers.com>"
    message["Subject"] = "Your membership confirmation"
    message["Date"] = "Sun, 11 Oct 2026 18:13:47 +0000"
    # Blank/spacer-only text/plain part should fall back to cleaned HTML
    message.set_content("\u200c\u00a0 \n\n\u200b")
    message.add_alternative(html, subtype="html")

    parsed = IMAPEmailAdapter("mail", "imap.example.test", "user")._parse(
        b"44", message.as_bytes()
    )

    assert "@media" not in parsed.body
    assert "@import" not in parsed.body
    assert "#111827" not in parsed.body
    assert "#991122" not in parsed.body
    assert "Hidden Title" not in parsed.body
    assert "ignore-me" not in parsed.body
    assert "\u200c" not in parsed.body
    assert "\u034f" not in parsed.body
    assert "\ufeff" not in parsed.body
    assert "Confirmation number: AAA-90210" in parsed.body
    assert "Verification code: 482910" in parsed.body
    assert len(parsed.body) < 200

