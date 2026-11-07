"""MCP tool catalog for the authenticated local transport."""

TOOLS = [
    {
        "name": "write_context",
        "description": "Store a revisioned contextual bundle, document, preference or observation. Bundle meanings and defaults require exact user confirmation plus profile.write grants. Documents are untrusted supporting notes over readable ordinary profile facts; never include passwords, OTPs, mailbox content or financial values. Use observations for one-time choices. Profile intake and bundle creation can commit atomically. expected_revision=0 creates, later writes require the last revision. Restore only document revisions with unchanged evidence.",
        "inputSchema": {
            "type": "object",
            "required": ["kind", "record_id", "data", "expected_revision", "target_origin", "purpose"],
            "properties": {
                "kind": {"enum": ["bundle", "document", "preference", "observation"]},
                "record_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "target_origin": {"type": "string"},
                "purpose": {"type": "string"},
                "confirmed": {"type": "boolean", "default": False},
                "shared": {"type": "boolean", "default": False, "description": "Bundle meanings only: explicit approval to reuse this meaning across tasks. Does not grant access, change fact scopes or establish a default. Documents and preferences remain task-scoped."},
                "restore_revision": {"type": "integer", "minimum": 1},
                "profile_entries": {"type": "array", "maxItems": 100, "items": {"type": "object"}, "description": "Exact approved ordinary profile entries: {record: write_records profile entry, expected_updated_at: null for new or exact prior timestamp}. Only with a confirmed bundle."},
                "data": {"type": "object", "description": "bundle: fields (semantic name to profile ID), subject, role, title, currentness (current/historical/unknown). document: bundle_refs, title, body (max 3000 characters). preference: one bundle_refs item, matching subject and role, optional valid_until (ISO date). observation: bundle_refs, outcome (used_once/accepted/corrected/rejected). Scope is exactly target_origin and purpose. No extra keys."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_task_context",
        "description": "Retrieve authorized contextual bundles and bounded supporting notes without profile values. Map requested form fields to explicit semantic names used in the bundles (e.g. street, city, postal_code, country). Supply all components required by the current form; an address is not complete just because country matches. Subject and role must be known or clarified. Pass selected_bundle_id only for an explicit current-task choice. Notes and observations never set defaults. Ready results still require scoped request_profile_fields to read values. Legacy fields without bundles remain available through match_form_fields; no context is not proof of missing facts.",
        "inputSchema": {
            "type": "object", "required": ["target_origin", "purpose", "requested_fields"],
            "properties": {
                "target_origin": {"type": "string"}, "purpose": {"type": "string"},
                "requested_fields": {"type": "array", "minItems": 1, "maxItems": 30, "items": {"type": "string"}},
                "subject": {"type": "string"}, "role": {"type": "string"},
                "selected_bundle_id": {"type": "string"},
                "task_summary": {"type": "string", "maxLength": 500, "description": "Optional transient description to rank supporting notes; never changes defaults or gets persisted."},
                "field_label": {"type": "string", "maxLength": 120, "description": "Actual form label, required to reuse an exact legacy mapping. Billing and delivery labels must not be interchanged."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
            }, "additionalProperties": False,
        },
    },
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
                "offset": {"type": "integer", "minimum": 0, "default": 0},
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
                "offset": {"type": "integer", "minimum": 0, "default": 0},
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
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
        },
    },
    {
        "name": "request_profile_fields",
        "description": "Read selected profile values; optionally constrain the read to one explicitly selected profile group.",
        "inputSchema": {
            "type": "object",
            "required": ["field_ids"],
            "properties": {
                "agent_id": {"type": "string"},
                "field_ids": {"type": "array", "items": {"type": "string"}},
                "target_origin": {"type": "string"},
                "purpose": {"type": "string"},
                "group_id": {"type": "string"},
                "bundle_id": {"type": "string", "description": "Constrain the read to one fresh authorized contextual bundle in this origin and purpose."},
            },
        },
    },
    {
        "name": "match_form_fields",
        "description": "Build a value-free ranked record table for an acting LLM: it can use the top saved record, ask one focused question when the table is unclear, and record a confirmed choice for the next match. Shipping/task hints, consent hints, and page defaults stay separate from saved data.",
        "inputSchema": {
            "type": "object",
            "required": ["fields"],
            "properties": {
                "agent_id": {"type": "string"},
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field_id": {"type": "string"},
                            "label": {"type": "string"},
                            "display_name": {"type": "string"},
                            "required": {"type": "boolean"},
                            "name": {"type": "string"},
                            "autocomplete": {"type": "string"},
                            "type": {"type": "string"},
                            "role": {"type": "string"},
                            "obscured": {"type": "boolean"},
                            "blocked": {"type": "boolean"},
                            "validation_error": {"type": "boolean"},
                            "task_specific": {"type": "boolean"},
                            "consent": {"type": "boolean"},
                            "page_default": {"type": "boolean"},
                            "value_state": {
                                "type": "string",
                                "enum": ["page_default", "user_entered", "stored_profile"],
                            },
                        },
                    },
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "target_origin": {"type": "string"},
                "purpose": {"type": "string"},
            },
        },
    },
    {
        "name": "record_form_mapping_feedback",
        "description": "Store a value-free, origin- and purpose-scoped mapping choice after a user-confirmed outcome.",
        "inputSchema": {
            "type": "object",
            "required": [
                "target_origin",
                "purpose",
                "field_label",
                "selected_field_ids",
                "confirmed",
            ],
            "properties": {
                "agent_id": {"type": "string"},
                "target_origin": {"type": "string"},
                "purpose": {"type": "string"},
                "field_label": {"type": "string"},
                "selected_field_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 100,
                },
                "group_id": {"type": "string"},
                "outcome": {"type": "string", "enum": ["accepted", "corrected"]},
                "confirmed": {"type": "boolean", "const": True},
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
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
        },
    },
    {
        "name": "match_financial_records",
        "description": "Match payment metadata by name query, network, last four, expiration, or a four/five-digit card_suffix. Suffix comparison stays inside the vault; values are never returned. Multiple matches require disambiguation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "kind": {"type": "string"},
                "matching_hints": {"type": "object"},
                "card_suffix": {"type": "string", "pattern": "^[0-9]{4,5}$"},
                "query": {"type": "string"},
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
        "description": "Retrieve one recent code matched to target_origin by sender domain. Explicit mailbox-read mode omits service matching. Sender matching is not cryptographic email authentication.",
        "inputSchema": {
            "type": "object",
            "required": ["account_id"],
            "properties": {
                "agent_id": {"type": "string"},
                "account_id": {"type": "string"},
                "target_origin": {"type": "string"},
                "purpose": {"type": "string"},
                "max_age_seconds": {"type": "integer"},
                "origin_policy": {
                    "type": "string",
                    "enum": ["target-origin", "mailbox-read"],
                    "default": "target-origin",
                },
            },
        },
    },
    {
        "name": "request_auth_code_for_recipient",
        "description": "Retrieve one recent code for an exact configured recipient, matched to target_origin by default. Requires target_origin unless mailbox-read is explicitly selected.",
        "inputSchema": {
            "type": "object",
            "required": ["recipient"],
            "properties": {
                "agent_id": {"type": "string"},
                "recipient": {"type": "string"},
                "target_origin": {"type": "string"},
                "purpose": {"type": "string"},
                "max_age_seconds": {"type": "integer"},
                "origin_policy": {
                    "type": "string",
                    "enum": ["target-origin", "mailbox-read"],
                    "default": "target-origin",
                },
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
