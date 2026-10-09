"""MCP tool catalog for the authenticated local transport."""

TOOLS = [
    {
        "name": "list_sources",
        "description": "List bounded configured source metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "kind": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "list_profile_fields",
        "description": "List bounded profile metadata without values.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "category": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "list_profile_groups",
        "description": "List metadata-only profile/address groups and field types.",
        "inputSchema": {
            "type": "object",
            "properties": {"agent_id": {"type": "string"}},
        },
    },
    {
        "name": "list_credentials",
        "description": "List bounded credential metadata without secrets.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "query": {"type": "string"},
                "target_origin": {"type": "string"},
                "username_hint": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "match_credentials",
        "description": "Find credential metadata for an exact site host without returning secrets.",
        "inputSchema": {
            "type": "object",
            "required": ["target_origin"],
            "properties": {
                "agent_id": {"type": "string"},
                "target_origin": {"type": "string"},
                "username_hint": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "request_profile_fields",
        "description": "Read selected profile values; local services may omit workflow scope.",
        "inputSchema": {
            "type": "object",
            "required": ["field_ids"],
            "properties": {
                "agent_id": {"type": "string"},
                "field_ids": {"type": "array", "items": {"type": "string"}},
                "target_origin": {"type": "string"},
                "purpose": {"type": "string"},
            },
        },
    },
    {
        "name": "list_financial_records",
        "description": "List bounded payment and financial metadata without values.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "kind": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "match_financial_records",
        "description": "Match payment metadata by safe hints such as network, last four, and expiration; values are never returned.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "kind": {"type": "string"},
                "matching_hints": {"type": "object"},
            },
        },
    },
    {
        "name": "write_records",
        "description": "Persist explicitly confirmed profile, credential, or financial records.",
        "inputSchema": {
            "type": "object",
            "required": ["target_origin", "purpose", "confirmed", "entries"],
            "properties": {
                "agent_id": {"type": "string"},
                "target_origin": {"type": "string"},
                "purpose": {"type": "string"},
                "confirmed": {"type": "boolean", "const": True},
                "entries": {"type": "array", "items": {"type": "object"}},
            },
        },
    },
    {
        "name": "request_credential_fields",
        "description": "Read selected credential fields; local services may omit workflow scope.",
        "inputSchema": {
            "type": "object",
            "required": ["credential_id", "field_ids"],
            "properties": {
                "agent_id": {"type": "string"},
                "credential_id": {"type": "string"},
                "field_ids": {"type": "array", "items": {"type": "string"}},
                "target_origin": {"type": "string"},
                "purpose": {"type": "string"},
            },
        },
    },
    {
        "name": "request_credential",
        "description": "Choose direct selected-field delivery or an origin-bound extension handle.",
        "inputSchema": {
            "type": "object",
            "required": ["delivery", "credential_id"],
            "properties": {
                "agent_id": {"type": "string"},
                "delivery": {
                    "type": "string",
                    "enum": ["direct", "extension"],
                },
                "credential_id": {"type": "string"},
                "field_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 5,
                },
                "target_origin": {"type": "string"},
                "purpose": {"type": "string"},
                "expires_in": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 300,
                },
            },
        },
    },
    {
        "name": "request_financial_fields",
        "description": "Read selected payment or financial fields; local services may omit workflow scope.",
        "inputSchema": {
            "type": "object",
            "required": ["record_id", "field_ids"],
            "properties": {
                "agent_id": {"type": "string"},
                "record_id": {"type": "string"},
                "field_ids": {"type": "array", "items": {"type": "string"}},
                "target_origin": {"type": "string"},
                "purpose": {"type": "string"},
            },
        },
    },
    {
        "name": "search_email",
        "description": "Search a configured email account; local services do not need a separate grant.",
        "inputSchema": {
            "type": "object",
            "required": ["account_id", "query"],
            "properties": {
                "agent_id": {"type": "string"},
                "account_id": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "get_email_message",
        "description": "Read one message from a configured email account; local services do not need a separate grant.",
        "inputSchema": {
            "type": "object",
            "required": ["account_id", "message_id"],
            "properties": {
                "agent_id": {"type": "string"},
                "account_id": {"type": "string"},
                "message_id": {"type": "string"},
            },
        },
    },
    {
        "name": "request_auth_code",
        "description": "Retrieve one recent target-bound authentication code.",
        "inputSchema": {
            "type": "object",
            "required": ["account_id", "target_origin", "purpose"],
            "properties": {
                "agent_id": {"type": "string"},
                "account_id": {"type": "string"},
                "target_origin": {"type": "string"},
                "purpose": {"type": "string"},
                "max_age_seconds": {"type": "integer"},
            },
        },
    },
    {
        "name": "request_auth_code_for_recipient",
        "description": "Retrieve one recent target-bound authentication code for an exact configured recipient email.",
        "inputSchema": {
            "type": "object",
            "required": ["recipient", "target_origin", "purpose"],
            "properties": {
                "agent_id": {"type": "string"},
                "recipient": {"type": "string"},
                "target_origin": {"type": "string"},
                "purpose": {"type": "string"},
                "max_age_seconds": {"type": "integer"},
            },
        },
    },
    {
        "name": "match_email_sources",
        "description": "Match exact configured email recipients to value-free source metadata.",
        "inputSchema": {
            "type": "object",
            "required": ["recipient"],
            "properties": {
                "recipient": {"type": "string"},
            },
        },
    },
    {
        "name": "request_verification_link_for_recipient",
        "description": "Retrieve one recent target-origin-bound verification link without mailbox content.",
        "inputSchema": {
            "type": "object",
            "required": ["recipient", "target_origin", "purpose"],
            "properties": {
                "agent_id": {"type": "string"},
                "recipient": {"type": "string"},
                "target_origin": {"type": "string"},
                "purpose": {"type": "string"},
                "max_age_seconds": {"type": "integer", "minimum": 1, "maximum": 3600},
            },
        },
    },
    {
        "name": "list_conversations",
        "description": "List available read-only message conversations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "source_id": {"type": "string"},
            },
        },
    },
    {
        "name": "search_messages",
        "description": "Search read-only messages; local services do not need a separate grant.",
        "inputSchema": {
            "type": "object",
            "required": ["conversation_scope", "query"],
            "properties": {
                "agent_id": {"type": "string"},
                "conversation_scope": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "get_message",
        "description": "Read one message; local services do not need a separate grant.",
        "inputSchema": {
            "type": "object",
            "required": ["message_id"],
            "properties": {
                "agent_id": {"type": "string"},
                "message_id": {"type": "string"},
            },
        },
    },
    {
        "name": "request_credential_use",
        "description": "Request a short-lived origin-bound handle; the password is never returned.",
        "inputSchema": {
            "type": "object",
            "required": ["credential_id", "target_origin", "purpose"],
            "properties": {
                "agent_id": {"type": "string"},
                "credential_id": {"type": "string"},
                "target_origin": {"type": "string"},
                "purpose": {"type": "string"},
                "expires_in": {"type": "integer"},
            },
        },
    },
]
