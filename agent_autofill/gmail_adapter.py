"""Read-only Gmail API adapter. Tokens and message contents stay in memory."""

import base64
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import message_from_bytes
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .google_oauth import GoogleOAuthTokenProvider
from .imap_adapter import IMAPAdapterError, _body, _header
from .models import EmailMessage

API_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me/"
MAX_RESPONSE_BYTES = 40_000_000


class GmailAdapterError(IMAPAdapterError):
    """A value-free Gmail failure, compatible with existing email error handling."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, _req, _fp, _code, _msg, _headers, _newurl):
        # Never forward a bearer token to a provider-selected redirect target.
        return None


@dataclass
class GmailEmailAdapter:
    account_id: str
    username: str
    oauth_provider: GoogleOAuthTokenProvider = field(repr=False)
    mailbox: str = "INBOX"
    _label_id: str | None = field(default=None, init=False, repr=False)

    def _request(self, path: str, **params) -> dict:
        try:
            token = self.oauth_provider.get_access_token()
        except Exception:  # noqa: BLE001 - never expose provider tokens or responses
            raise GmailAdapterError("Gmail OAuth authentication failed; reconnect this source") from None
        request = Request(
            API_ROOT + path + ("?" + urlencode(params) if params else ""),
            headers={"Authorization": "Bearer " + token},
            method="GET",
        )
        try:
            with build_opener(_NoRedirect()).open(request, timeout=30) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("response too large")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise TypeError("invalid response")
            return result
        except HTTPError as exc:
            if exc.code == 404:
                raise GmailAdapterError("Gmail resource is unavailable") from None
            if exc.code in {401, 403}:
                raise GmailAdapterError(
                    "Gmail access denied; enable the Gmail API and reconnect with read-only access"
                ) from None
            raise GmailAdapterError("Gmail request failed") from None
        except Exception:  # noqa: BLE001 - normalize dependency diagnostics
            raise GmailAdapterError("Gmail response could not be read") from None

    def check_connection(self) -> None:
        """Check the selected account and label without reading any messages."""
        self._label_id = None
        profile = self._request("profile")
        if str(profile.get("emailAddress", "")).casefold() != self.username.strip().casefold():
            raise GmailAdapterError("Google signed in to a different account; select the requested email")
        labels = self._request("labels").get("labels", [])
        if not isinstance(labels, list):
            raise GmailAdapterError("Gmail label response is invalid")
        for label in labels:
            if not isinstance(label, dict):
                continue
            if label.get("name") == self.mailbox or label.get("id") == self.mailbox:
                label_id = label.get("id")
                if isinstance(label_id, str) and label_id:
                    self._label_id = label_id
                    return
        raise GmailAdapterError("Gmail mailbox was not found; use its exact Gmail label name")

    def _label(self) -> str:
        if self._label_id is None:
            self.check_connection()
        assert self._label_id is not None
        return self._label_id

    def get(self, message_id: str) -> EmailMessage:
        if not isinstance(message_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", message_id):
            raise GmailAdapterError("Invalid Gmail message id")
        label = self._label()
        data = self._request("messages/" + message_id, format="raw")
        if not isinstance(data.get("labelIds"), list) or label not in data["labelIds"]:
            raise GmailAdapterError("Message is outside the configured Gmail mailbox")
        try:
            raw = data["raw"]
            if not isinstance(raw, str):
                raise TypeError("invalid raw message")
            msg = message_from_bytes(base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True))
            # Gmail's receipt timestamp is more reliable than the sender's Date header.
            received = datetime.fromtimestamp(int(data["internalDate"]) / 1000, UTC).isoformat()
            return EmailMessage(message_id, self.account_id, _header(msg.get("From")),
                                _header(msg.get("Subject")), _body(msg), received)
        except Exception:  # noqa: BLE001 - MIME errors may contain private message data
            raise GmailAdapterError("Gmail message could not be decoded") from None

    def search(self, query: str, limit: int = 20) -> list[EmailMessage]:
        if limit <= 0:
            return []
        # Broker searches are bounded; keep the provider request bounded too.
        remaining = min(limit, 500)
        params = {"q": query, "labelIds": self._label(), "includeSpamTrash": "true"}
        result: list[EmailMessage] = []
        seen_pages: set[str] = set()
        while remaining:
            data = self._request("messages", **params, maxResults=remaining)
            messages = data.get("messages", [])
            if not isinstance(messages, list):
                raise GmailAdapterError("Gmail message list is invalid")
            for message in messages[:remaining]:
                if not isinstance(message, dict) or not isinstance(message.get("id"), str):
                    raise GmailAdapterError("Gmail message list is invalid")
                result.append(self.get(message["id"]))
            remaining -= len(messages[:remaining])
            page = data.get("nextPageToken")
            if not page:
                break
            if not messages or not isinstance(page, str) or page in seen_pages:
                raise GmailAdapterError("Gmail pagination response is invalid")
            seen_pages.add(page)
            params["pageToken"] = page
        return result

    def recent(self, max_age_seconds: int = 300) -> list[EmailMessage]:
        now = datetime.now(UTC).timestamp()
        messages = self.search(f"after:{int(now - max_age_seconds) - 1}", limit=100)
        return [message for message in messages if message.received_at is not None
                and 0 <= now - datetime.fromisoformat(message.received_at).timestamp() <= max_age_seconds]
