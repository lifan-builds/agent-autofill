"""Read-only IMAP adapter for user-owned mailboxes.

Credentials are supplied by a caller that owns them (normally Keychain). This
adapter intentionally has no send or mutation methods.
"""

import imaplib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from email.header import decode_header, make_header
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
    """The configured mailbox could not be selected read-only."""


class IMAPConfigurationError(IMAPAdapterError):
    """The adapter has no usable connector secret."""


def _header(value: str | None) -> str:
    return str(make_header(decode_header(value or "")))


class _HTMLTextParser(HTMLParser):
    """Extract readable text from HTML-only mail without retaining markup."""

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

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._chunks.append(data)

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag.lower() in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_startendtag(self, tag: str, _attrs) -> None:
        if tag.lower() in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def text(self) -> str:
        lines = [" ".join(line.split()) for line in "".join(self._chunks).splitlines()]
        return "\n".join(line for line in lines if line)


def _html_body(value: str) -> str:
    parser = _HTMLTextParser()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, AssertionError):
        # A malformed provider template should not make the entire message
        # unreadable; the partial parser output is still safer than markup.
        return parser.text()
    return parser.text()


def _body(msg) -> str:
    parts = list(msg.walk()) if msg.is_multipart() else [msg]
    plain: list[str] = []
    html: list[str] = []
    for part in parts:
        if "attachment" in str(part.get("Content-Disposition", "")).lower():
            continue
        if part.get_content_type() not in {"text/plain", "text/html"}:
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
        if part.get_content_type() == "text/plain":
            plain.append(value)
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
            except Exception as exc:
                # Provider exceptions can contain an HTTP response or token;
                # translate them before they reach CLI, MCP, or audit output.
                raise IMAPAuthenticationError(
                    "IMAP OAuth authentication failed"
                ) from exc
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
            conn = imaplib.IMAP4_SSL(self.host, self.port)
        except Exception as exc:
            raise IMAPConnectionError("IMAP connection failed") from exc

        try:
            if oauth2_token:

                def auth(_):
                    return f"user={self.username}\1auth=Bearer {oauth2_token}\1\1".encode()

                status, _ = conn.authenticate("XOAUTH2", auth)
            else:
                status, _ = conn.login(self.username, self.password)
            if status != "OK":
                raise IMAPAuthenticationError("IMAP authentication failed")
        except Exception as exc:
            self._logout(conn)
            if isinstance(exc, IMAPAuthenticationError):
                raise
            raise IMAPAuthenticationError("IMAP authentication failed") from exc

        try:
            status, _ = conn.select(self.mailbox, readonly=True)
        except Exception as exc:
            self._logout(conn)
            raise IMAPMailboxError("IMAP mailbox selection failed") from exc
        if status != "OK":
            self._logout(conn)
            raise IMAPMailboxError("IMAP mailbox selection failed")
        return conn

    @staticmethod
    def _logout(conn) -> None:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001, S110 - cleanup must not expose provider diagnostics
            # A failed network connection must not mask the useful provider
            # error or leak dependency-specific diagnostics to the caller.
            pass

    def check_connection(self) -> None:
        """Authenticate and select the configured mailbox without reading it."""

        conn = self._connect()
        self._logout(conn)

    def _parse(self, uid: bytes, raw: bytes) -> EmailMessage:
        msg = message_from_bytes(raw)
        return EmailMessage(
            uid.decode(),
            self.account_id,
            _header(msg.get("From")),
            _header(msg.get("Subject")),
            _body(msg),
            _date(msg.get("Date")),
        )

    def search(self, query: str, limit: int = 20) -> list[EmailMessage]:
        conn = self._connect()
        try:
            _, data = conn.uid(
                "search", None, "TEXT", f'"{query.replace(chr(34), "")}"'
            )
            uids = (data[0] or b"").split()[-limit:][::-1]
            result = []
            for uid in uids:
                _, fetched = conn.uid("fetch", uid, "(RFC822)")
                raw = next(
                    (part[1] for part in fetched if isinstance(part, tuple)), None
                )
                if raw:
                    result.append(self._parse(uid, raw))
            return result
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
            _, data = conn.uid("search", None, "SINCE", since)
            result = []
            for uid in (data[0] or b"").split()[-100:][::-1]:
                _, fetched = conn.uid("fetch", uid, "(RFC822)")
                raw = next(
                    (part[1] for part in fetched if isinstance(part, tuple)), None
                )
                if raw:
                    msg = self._parse(uid, raw)
                    if msg.received_at is None:
                        continue
                    age = (
                        datetime.now(UTC)
                        - datetime.fromisoformat(msg.received_at)
                    ).total_seconds()
                    if 0 <= age <= max_age_seconds:
                        result.append(msg)
            return result
        finally:
            self._logout(conn)

    def get(self, message_id: str) -> EmailMessage:
        conn = self._connect()
        try:
            _, fetched = conn.uid("fetch", message_id.encode(), "(RFC822)")
            raw = next((part[1] for part in fetched if isinstance(part, tuple)), None)
            if not raw:
                raise KeyError(message_id)
            return self._parse(message_id.encode(), raw)
        finally:
            self._logout(conn)
