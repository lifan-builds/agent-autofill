from dataclasses import dataclass
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        ) > datetime.now(timezone.utc)


@dataclass(frozen=True)
class EmailMessage:
    message_id: str
    account_id: str
    sender: str
    subject: str
    body: str
    received_at: str


@dataclass(frozen=True)
class TextMessage:
    message_id: str
    conversation_id: str
    sender: str
    body: str
    sent_at: str
