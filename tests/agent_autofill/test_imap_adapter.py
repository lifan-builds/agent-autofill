# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Agent Autofill; Section 7 attribution terms in ATTRIBUTION.md.
import ssl
import traceback
from email.message import EmailMessage as MIMEEmailMessage

import pytest

from agent_autofill.imap_adapter import (
    IMAPAuthenticationError,
    IMAPEmailAdapter,
    IMAPMailboxError,
    _date,
)


class FakeConnection:
    def __init__(self, login_error=None, select_status="OK", auth_status="OK", responses=()):
        self.login_error = login_error
        self.select_status = select_status
        self.auth_status = auth_status
        self.login_calls = []
        self.auth_calls = []
        self.select_calls = []
        self.uid_calls = []
        self.logout_calls = 0
        self.literal = None
        self.responses = iter(responses)

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

    def uid(self, command, *args):
        self.uid_calls.append((command, args, self.literal))
        self.literal = None
        return next(self.responses)

    def logout(self):
        self.logout_calls += 1
        return "BYE", []


def test_check_connection_authenticates_readonly_and_logs_out(monkeypatch):
    connection = FakeConnection()

    def connect(host, port, *, ssl_context, timeout):
        assert (host, port) == ("imap.example.test", 993)
        assert ssl_context.check_hostname
        assert ssl_context.verify_mode == ssl.CERT_REQUIRED
        assert timeout == 30.0
        return connection

    monkeypatch.setattr(
        "agent_autofill.imap_adapter.imaplib.IMAP4_SSL",
        connect,
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


def test_check_connection_quotes_and_escapes_mailbox_names(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr("agent_autofill.imap_adapter.imaplib.IMAP4_SSL", lambda *_, **__: connection)

    IMAPEmailAdapter(
        "mail", "imap.example.test", "user", password="fixture",
        mailbox=r'Shared Team/"Review"\2026',
    ).check_connection()

    assert connection.select_calls == [(r'"Shared Team/\"Review\"\\2026"', True)]


def test_check_connection_uses_xoauth2_without_printing_token(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(
        "agent_autofill.imap_adapter.imaplib.IMAP4_SSL",
        lambda host, port, **kwargs: connection,
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
        lambda host, port, **kwargs: auth_connection,
    )
    with pytest.raises(IMAPAuthenticationError, match="IMAP authentication failed") as failure:
        IMAPEmailAdapter("mail", "imap.example.test", "user", password="fixture").check_connection()
    assert "provider detail" not in "".join(traceback.format_exception(failure.value))
    assert auth_connection.logout_calls == 1

    mailbox_connection = FakeConnection(select_status="NO")
    monkeypatch.setattr(
        "agent_autofill.imap_adapter.imaplib.IMAP4_SSL",
        lambda host, port, **kwargs: mailbox_connection,
    )
    with pytest.raises(IMAPMailboxError, match="IMAP mailbox selection failed"):
        IMAPEmailAdapter("mail", "imap.example.test", "user", password="fixture").check_connection()
    assert mailbox_connection.logout_calls == 1


def test_check_connection_rejects_non_ok_login_and_xoauth2_status(monkeypatch):
    password_connection = FakeConnection(auth_status="NO")
    monkeypatch.setattr(
        "agent_autofill.imap_adapter.imaplib.IMAP4_SSL",
        lambda host, port, **kwargs: password_connection,
    )
    with pytest.raises(IMAPAuthenticationError, match="IMAP authentication failed"):
        IMAPEmailAdapter("mail", "imap.example.test", "user", password="fixture").check_connection()
    assert password_connection.logout_calls == 1

    oauth_connection = FakeConnection(auth_status="BAD")
    monkeypatch.setattr(
        "agent_autofill.imap_adapter.imaplib.IMAP4_SSL",
        lambda host, port, **kwargs: oauth_connection,
    )
    with pytest.raises(IMAPAuthenticationError, match="IMAP authentication failed"):
        IMAPEmailAdapter(
            "mail", "imap.example.test", "user", oauth2_token="fixture-token"
        ).check_connection()
    assert oauth_connection.logout_calls == 1


def test_oauth_provider_failure_suppresses_private_diagnostic():
    class Provider:
        def get_access_token(self):
            raise RuntimeError("private provider diagnostic")

    with pytest.raises(IMAPAuthenticationError, match="IMAP OAuth authentication failed") as failure:
        IMAPEmailAdapter("mail", "imap.example.test", "user", oauth_provider=Provider()).check_connection()

    assert "private provider diagnostic" not in "".join(traceback.format_exception(failure.value))


@pytest.mark.parametrize("query, criteria", [
    ('Review "receipt"', ("TEXT",)),
    ('Café "receipt"', ("CHARSET", "UTF-8", "TEXT")),
])
def test_search_sends_the_query_as_a_literal(monkeypatch, query, criteria):
    raw = b"Subject: Receipt\r\n\r\nExample message\r\n"
    metadata = f"2 (UID 2 RFC822 {{{len(raw)}}}".encode()
    connection = FakeConnection(responses=[("OK", [b"1 2"]), ("OK", [(metadata, raw), b")"])])
    monkeypatch.setattr(IMAPEmailAdapter, "_connect", lambda _: connection)

    messages = IMAPEmailAdapter("mail", "imap.example.test", "user").search(query, limit=1)

    assert [message.message_id for message in messages] == ["2"]
    assert connection.uid_calls == [
        ("search", criteria, query.encode("utf-8")),
        ("fetch", (b"2", "(INTERNALDATE RFC822)"), None),
    ]
    assert connection.logout_calls == 1


@pytest.mark.parametrize("command, responses", [
    ("search", [("NO", [b"provider detail"])]),
    ("fetch", [("OK", [b"1"]), ("BAD", [b"provider detail"])]),
])
def test_uid_status_failure_is_reported_without_provider_detail(monkeypatch, command, responses):
    connection = FakeConnection(responses=responses)
    monkeypatch.setattr(IMAPEmailAdapter, "_connect", lambda _: connection)

    with pytest.raises(IMAPMailboxError, match=f"^IMAP {command} failed$"):
        IMAPEmailAdapter("mail", "imap.example.test", "user").search("receipt")

    assert connection.logout_calls == 1


@pytest.mark.parametrize("message_id", ["", "mail", "1:2", "1,2", "*", "0", "4294967296", "٢"])
def test_get_rejects_invalid_uids_before_fetching(monkeypatch, message_id):
    connection = FakeConnection()
    monkeypatch.setattr(IMAPEmailAdapter, "_connect", lambda _: connection)

    with pytest.raises(IMAPMailboxError, match="^Invalid IMAP message UID$"):
        IMAPEmailAdapter("mail", "imap.example.test", "user").get(message_id)

    assert not connection.uid_calls
    assert connection.logout_calls == 1


def test_search_rejects_sequence_sets_in_the_uid_response(monkeypatch):
    connection = FakeConnection(responses=[("OK", [b"1:2"])])
    monkeypatch.setattr(IMAPEmailAdapter, "_connect", lambda _: connection)

    with pytest.raises(IMAPMailboxError, match="^Invalid IMAP message UID$"):
        IMAPEmailAdapter("mail", "imap.example.test", "user").search("receipt")

    assert [command for command, _, _ in connection.uid_calls] == ["search"]
    assert connection.logout_calls == 1


def test_get_treats_an_empty_message_literal_as_missing(monkeypatch):
    connection = FakeConnection(responses=[("OK", [(b"1 (RFC822 {0}", b""), b")"])])
    monkeypatch.setattr(IMAPEmailAdapter, "_connect", lambda _: connection)

    with pytest.raises(KeyError, match="1"):
        IMAPEmailAdapter("mail", "imap.example.test", "user").get("1")


def test_parse_decodes_headers_with_unknown_rfc2047_charsets():
    raw = (
        b"From: =?x-unknown?Q?M=C3=BCller?= <sender@example.test>\r\n"
        b"Subject: =?x-unknown?Q?Caf=C3=A9?= receipt\r\n\r\n"
        b"Example message\r\n"
    )
    parsed = IMAPEmailAdapter("mail", "imap.example.test", "user")._parse(b"2", raw)

    assert parsed.sender == "Müller <sender@example.test>"
    assert parsed.subject == "Café receipt"


def test_parse_does_not_include_text_from_attached_subtrees():
    forwarded = MIMEEmailMessage()
    forwarded.set_content("Forwarded message text")
    attachment = MIMEEmailMessage()
    attachment.make_mixed()
    attachment["Content-Disposition"] = "attachment"
    attachment.attach(forwarded)
    message = MIMEEmailMessage()
    message.set_content("<p>Outer message text</p>", subtype="html")
    message.add_attachment(forwarded, disposition="inline")
    message.attach(attachment)

    parsed = IMAPEmailAdapter("mail", "imap.example.test", "user")._parse(b"2", message.as_bytes())

    assert parsed.body == "Outer message text"


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

