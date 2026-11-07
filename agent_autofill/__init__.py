"""Agent Autofill: local-first personal information broker for agent clients."""

from .imap_adapter import IMAPEmailAdapter
from .local import LocalConnection, connect_service
from .messages_adapter import MacMessagesAdapter
from .service import AuthorizationError, Broker, BrokerError, CredentialUseError

__all__ = [
    "AuthorizationError",
    "Broker",
    "BrokerError",
    "CredentialUseError",
    "IMAPEmailAdapter",
    "LocalConnection",
    "MacMessagesAdapter",
    "connect_service",
]
