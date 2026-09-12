"""Read-only IMAP adapter for user-owned mailboxes.

Credentials are supplied by a caller that owns them (normally Keychain). This
adapter intentionally has no send or mutation methods.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
import imaplib
from .models import EmailMessage


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
    password: str | None = None
    port: int = 993
    mailbox: str = "INBOX"
    oauth2_token: str | None = None

    def _connect(self):
        conn = imaplib.IMAP4_SSL(self.host, self.port)
        if self.oauth2_token:

            def auth(_):
                return f"user={self.username}\1auth=Bearer {self.oauth2_token}\1\1".encode()

            conn.authenticate("XOAUTH2", auth)
        elif self.password is not None:
            conn.login(self.username, self.password)
        else:
            raise RuntimeError(
                "IMAP adapter needs a Keychain-provided password or OAuth token"
            )
        status, _ = conn.select(self.mailbox, readonly=True)
        if status != "OK":
            conn.logout()
            raise RuntimeError(f"cannot select mailbox {self.mailbox}")
        return conn

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
            conn.logout()

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
            conn.logout()

    def get(self, message_id: str) -> EmailMessage:
        conn = self._connect()
        try:
            _, fetched = conn.uid("fetch", message_id.encode(), "(RFC822)")
            raw = next((part[1] for part in fetched if isinstance(part, tuple)), None)
            if not raw:
                raise KeyError(message_id)
            return self._parse(message_id.encode(), raw)
        finally:
            conn.logout()
