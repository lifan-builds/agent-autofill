"""Read-only IMAP adapter for user-owned mailboxes.

Credentials are supplied by a caller that owns them (normally Keychain). This
adapter intentionally has no send or mutation methods.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
import imaplib
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


def _body(msg) -> str:
    parts = list(msg.walk()) if msg.is_multipart() else [msg]
    text = []
    for part in parts:
        if part.get_content_type() == "text/plain" and "attachment" not in str(
            part.get("Content-Disposition", "")
        ):
            try:
                text.append(
                    part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                )
            except Exception:
                pass
    return "\n".join(text)[:200_000]


def _date(value: str | None) -> str:
    try:
        return parsedate_to_datetime(value or "").astimezone(timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


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
        except Exception:
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
            since = (
                datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
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
                    age = (
                        datetime.now(timezone.utc)
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
