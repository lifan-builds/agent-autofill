from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

_LAST_NOW: datetime | None = None


def now_iso() -> str:
    global _LAST_NOW
    current = datetime.now(UTC)
    if _LAST_NOW is not None and current <= _LAST_NOW:
        current = _LAST_NOW + timedelta(microseconds=1)
    _LAST_NOW = current
    return current.isoformat()


@dataclass(frozen=True)
class Grant:
    agent_id: str
    operation: str
    resource_scope: str
    target_origin: str | None
    purpose: str | None
    issued_at: str
    expires_at: str
    revoked: bool = False

    def active(self) -> bool:
        return not self.revoked and datetime.fromisoformat(
            self.expires_at
        ) > datetime.now(UTC)


@dataclass(frozen=True)
class EmailMessage:
    message_id: str
    account_id: str
    sender: str
    subject: str
    body: str
    received_at: str | None


@dataclass(frozen=True)
class TextMessage:
    message_id: str
    conversation_id: str
    sender: str
    body: str
    sent_at: str | None
