# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Agent Autofill; Section 7 attribution terms in ATTRIBUTION.md.
"""Agent Autofill: local-first personal information broker for agent clients."""

from .imap_adapter import IMAPEmailAdapter
from .local import LocalConnection, connect_service
from .messages_adapter import MacMessagesAdapter
from .service import AuthorizationError, Broker, BrokerError, CredentialUseError

ATTRIBUTION = "Based on Agent Autofill by lifan-builds (https://github.com/lifan-builds/agent-autofill)"

__all__ = [
    "ATTRIBUTION",
    "AuthorizationError",
    "Broker",
    "BrokerError",
    "CredentialUseError",
    "IMAPEmailAdapter",
    "LocalConnection",
    "MacMessagesAdapter",
    "connect_service",
]
