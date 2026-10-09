"""Agent Autofill: local-first personal information broker for agent clients."""

from .service import Broker, BrokerError, AuthorizationError, CredentialUseError
from .imap_adapter import IMAPEmailAdapter
from .messages_adapter import MacMessagesAdapter
from .local import LocalConnection, connect_service

__all__ = [
    "Broker",
    "BrokerError",
    "AuthorizationError",
    "CredentialUseError",
    "IMAPEmailAdapter",
    "MacMessagesAdapter",
    "LocalConnection",
    "connect_service",
]
