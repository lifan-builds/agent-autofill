# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Agent Autofill; Section 7 attribution terms in ATTRIBUTION.md.
"""Read-only IMAP adapter for user-owned mailboxes.

Credentials are supplied by a caller that owns them (normally Keychain). This
adapter intentionally has no send or mutation methods.
"""

import imaplib
import re
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email import message_from_bytes, policy
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import ClassVar

from .models import EmailMessage


class IMAPAdapterError(RuntimeError):
    """Base class for safe, operator-facing IMAP failures."""


class IMAPConnectionError(IMAPAdapterError):
    """The TLS connection could not be established."""


class IMAPAuthenticationError(IMAPAdapterError):
    """The configured password or bearer token was rejected."""


class IMAPMailboxError(IMAPAdapterError):
    """The configured mailbox could not be selected or read."""


class IMAPConfigurationError(IMAPAdapterError):
    """The adapter has no usable connector secret."""


def _header(value: str | None) -> str:
    return str(policy.default.header_fetch_parse("Subject", str(value or "")))


_SUPPRESSED_HTML_BLOCKS = re.compile(
    r"<(style|script|head|title|noscript|svg|template|xml)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_UNCLOSED_SUPPRESSED_HTML = re.compile(
    r"<(style|script|head)\b[^>]*>.*\Z",
    re.IGNORECASE | re.DOTALL,
)
_ZERO_WIDTH_SPACERS = re.compile(
    r"[\u200b-\u200f\u2060\ufeff\u034f\u00ad\u061c\u180e]+"
)
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")


def _sanitize_email_text(value: str) -> str:
    had_trailing_newline = value.endswith(("\n", "\r"))
    cleaned = _ZERO_WIDTH_SPACERS.sub("", value)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in cleaned.split("\n")]
    # Preserve single paragraph breaks in plain text while collapsing spacer runs.
    compacted: list[str] = []
    previous_blank = True
    for line in lines:
        if line:
            compacted.append(line)
            previous_blank = False
        elif not previous_blank:
            compacted.append("")
            previous_blank = True
    while compacted and not compacted[-1]:
        compacted.pop()
    if not compacted:
        return ""
    result = _EXCESS_BLANK_LINES.sub("\n\n", "\n".join(compacted))
    return f"{result}\n" if had_trailing_newline else result


class _HTMLTextParser(HTMLParser):
    """Extract readable text from HTML-only mail without retaining markup."""

    _IGNORED_TAGS: ClassVar[set[str]] = {
        "head",
        "noscript",
        "script",
        "style",
        "svg",
        "template",
        "title",
        "xml",
    }
    _BLOCK_TAGS: ClassVar[set[str]] = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._ignored_stack: list[str] = []

    def handle_data(self, data: str) -> None:
        if self._ignored_stack:
            return
        cleaned = _ZERO_WIDTH_SPACERS.sub("", data)
        if cleaned.strip():
            self._chunks.append(cleaned)
        elif cleaned and self._chunks and not self._chunks[-1][-1:].isspace():
            self._chunks.append(" ")

    def handle_starttag(self, tag: str, _attrs) -> None:
        lower = tag.lower()
        if lower in self._IGNORED_TAGS:
            self._ignored_stack.append(lower)
            return
        if self._ignored_stack:
            return
        if lower in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_startendtag(self, tag: str, _attrs) -> None:
        lower = tag.lower()
        if lower in self._IGNORED_TAGS or self._ignored_stack:
            return
        if lower in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower in self._IGNORED_TAGS:
            if lower in self._ignored_stack:
                while self._ignored_stack:
                    popped = self._ignored_stack.pop()
                    if popped == lower:
                        break
            return
        if self._ignored_stack:
            return
        if lower in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def text(self) -> str:
        raw = _ZERO_WIDTH_SPACERS.sub("", "".join(self._chunks))
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)


def _html_body(value: str) -> str:
    stripped = _SUPPRESSED_HTML_BLOCKS.sub(" ", value)
    stripped = _UNCLOSED_SUPPRESSED_HTML.sub(" ", stripped)
    parser = _HTMLTextParser()
    try:
        parser.feed(stripped)
        parser.close()
    except (ValueError, AssertionError):
        # A malformed provider template should not make the entire message
        # unreadable; the partial parser output is still safer than markup.
        return parser.text()
    return parser.text()


def _body(msg) -> str:
    parts = [msg]
    plain: list[str] = []
    html: list[str] = []
    while parts:
        part = parts.pop()
        content_type = part.get_content_type()
        if part.get_content_disposition() == "attachment" or content_type == "message/rfc822":
            continue
        if part.is_multipart():
            parts.extend(reversed(part.get_payload()))
            continue
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                raw = part.get_payload()
                value = raw if isinstance(raw, str) else ""
            else:
                value = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except (LookupError, ValueError, TypeError):
            # Skip malformed MIME without logging message data.
            continue
        if content_type == "text/plain":
            cleaned = _sanitize_email_text(value)
            if cleaned:
                plain.append(cleaned)
        else:
            parsed = _html_body(value)
            if parsed:
                html.append(parsed)
    # Prefer the provider's plain-text alternative. HTML-only mail is common
    # for login-code messages, so fall back to readable text from that part.
    return "\n".join(plain or html)[:200_000]


def _date(value: str | None) -> str | None:
    try:
        return parsedate_to_datetime(value or "").astimezone(UTC).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        # A missing or malformed provider date must never look newly received.
        return None


@dataclass
class IMAPEmailAdapter:
    account_id: str
    host: str
    username: str
    password: str | None = field(default=None, repr=False)
    port: int = 993
    mailbox: str = "INBOX"
    oauth2_token: str | None = field(default=None, repr=False)
    oauth_provider: object | None = field(default=None, repr=False)
    token_provider: object | None = field(default=None, repr=False)

    def _current_oauth2_token(self) -> str | None:
        provider = (
            self.oauth_provider
            if self.oauth_provider is not None
            else self.token_provider
        )
        if provider is not None:
            try:
                getter = getattr(provider, "get_access_token", None)
                if getter is None:
                    getter = getattr(provider, "access_token", None)
                if getter is None:
                    raise TypeError("OAuth provider has no access-token method")
                token = getter() if callable(getter) else getter
            except Exception:
                # Provider exceptions can contain an HTTP response or token;
                # translate them before they reach CLI, MCP, or audit output.
                raise IMAPAuthenticationError(
                    "IMAP OAuth authentication failed"
                ) from None
            if not isinstance(token, str) or not token:
                raise IMAPAuthenticationError("IMAP OAuth authentication failed")
            return token
        return self.oauth2_token

    def _connect(self):
        oauth2_token = self._current_oauth2_token()
        if not oauth2_token and self.password is None:
            raise IMAPConfigurationError(
                "IMAP adapter needs a Keychain-provided password or OAuth token"
            )
        try:
            conn = imaplib.IMAP4_SSL(
                self.host, self.port, ssl_context=ssl.create_default_context(), timeout=30.0
            )
        except Exception:
            raise IMAPConnectionError("IMAP connection failed") from None

        try:
            if oauth2_token:

                def auth(_):
                    return f"user={self.username}\1auth=Bearer {oauth2_token}\1\1".encode()

                status, _ = conn.authenticate("XOAUTH2", auth)
            else:
                status, _ = conn.login(self.username, self.password)
        except Exception:
            self._logout(conn)
            raise IMAPAuthenticationError("IMAP authentication failed") from None
        if status != "OK":
            self._logout(conn)
            raise IMAPAuthenticationError("IMAP authentication failed")

        try:
            mailbox = self.mailbox
            if any(char in mailbox for char in "\r\n\0"):
                raise ValueError("Invalid mailbox name")
            if re.search(r'[\x00-\x20\x7f(){%*"\\]', mailbox):
                escaped = mailbox.replace("\\", "\\\\").replace('"', '\\"')
                mailbox = f'"{escaped}"'
            status, _ = conn.select(mailbox, readonly=True)
        except Exception:
            self._logout(conn)
            raise IMAPMailboxError("IMAP mailbox selection failed") from None
        if status != "OK":
            self._logout(conn)
            raise IMAPMailboxError("IMAP mailbox selection failed")
        return conn

    @staticmethod
    def _logout(conn) -> None:
        try:
            conn.logout()
        except (imaplib.IMAP4.error, OSError):
            return

    def check_connection(self) -> None:
        """Authenticate and select the configured mailbox without reading it."""

        conn = self._connect()
        self._logout(conn)

    def _parse(self, uid: bytes, raw: bytes, internal_date: str | None = None) -> EmailMessage:
        msg = message_from_bytes(raw)
        return EmailMessage(
            uid.decode(),
            self.account_id,
            _header(msg.get("From")),
            _header(msg.get("Subject")),
            _body(msg),
            _date(msg.get("Date") if internal_date is None else internal_date),
        )

    @staticmethod
    def _uid(conn, command, *args):
        try:
            status, data = conn.uid(command, *args)
        except (imaplib.IMAP4.error, OSError):
            raise IMAPMailboxError(f"IMAP {command} failed") from None
        if status != "OK":
            raise IMAPMailboxError(f"IMAP {command} failed")
        return data

    def _fetch(self, conn, uid: bytes) -> EmailMessage | None:
        if not re.fullmatch(rb"[1-9][0-9]{0,9}", uid) or int(uid) > 0xFFFFFFFF:
            raise IMAPMailboxError("Invalid IMAP message UID")
        fetched = iter(self._uid(conn, "fetch", uid, "(INTERNALDATE RFC822)"))
        for part in fetched:
            if isinstance(part, tuple):
                metadata, raw = part
                if not raw:
                    return None
                # imaplib splits FETCH metadata around the message literal.
                metadata += next(fetched, b"")
                date = re.search(rb'[( ]INTERNALDATE(?= |\)|$)(?: +"([^"]* [+-]\d{4})")?', metadata, re.I)
                internal_date = (date[1] or b"").decode("ascii", "replace") if date else None
                if internal_date and internal_date.endswith(" -0000"):
                    # The email date parser treats -0000 as a naive datetime.
                    internal_date = internal_date[:-5] + "+0000"
                return self._parse(uid, raw, internal_date)
        return None

    def search(self, query: str, limit: int = 20) -> list[EmailMessage]:
        conn = self._connect()
        try:
            conn.literal = query.encode("utf-8")
            charset = () if query.isascii() else ("CHARSET", "UTF-8")
            data = self._uid(conn, "search", *charset, "TEXT")
            uids = (data[0] or b"").split()[-limit:][::-1]
            return [msg for uid in uids if (msg := self._fetch(conn, uid)) is not None]
        finally:
            self._logout(conn)

    def recent(self, max_age_seconds: int = 300) -> list[EmailMessage]:
        conn = self._connect()
        try:
            # IMAP SINCE compares calendar dates, ignoring the mailbox timezone.
            # Include the previous day; the timestamp filter below stays exact.
            since = (
                datetime.now(UTC) - timedelta(seconds=max_age_seconds, days=1)
            ).strftime("%d-%b-%Y")
            data = self._uid(conn, "search", "SINCE", since)
            result = []
            for uid in (data[0] or b"").split()[-100:][::-1]:
                msg = self._fetch(conn, uid)
                if msg is None or msg.received_at is None:
                    continue
                age = (
                    datetime.now(UTC) - datetime.fromisoformat(msg.received_at)
                ).total_seconds()
                if 0 <= age <= max_age_seconds:
                    result.append(msg)
            return result
        finally:
            self._logout(conn)

    def get(self, message_id: str) -> EmailMessage:
        conn = self._connect()
        try:
            msg = self._fetch(conn, message_id.encode("ascii", "replace"))
            if msg is None:
                raise KeyError(message_id)
            return msg
        finally:
            self._logout(conn)
