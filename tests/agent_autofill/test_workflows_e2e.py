"""Synthetic cross-layer checks for the three agent-facing workflows."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_autofill.models import EmailMessage, TextMessage
from agent_autofill.server import _call
from agent_autofill.service import Broker, BrokerError
from agent_autofill.storage import Store


def _broker() -> tuple[Broker, str, str]:
    broker = Broker(Store(":memory:", key=b"e" * 32))
    agent_id = "synthetic-e2e-agent"
    token = broker.create_agent(agent_id)
    return broker, agent_id, token


def test_use_saved_data_and_scoped_sources_end_to_end() -> None:
    broker, agent_id, token = _broker()
    origin = "https://example.test"
    purpose = "synthetic checkout"

    broker.put_profile(
        "full_name",
        "identity",
        "Full name",
        "Synthetic User",
        allowed_origins=[origin],
        allowed_purposes=[purpose],
    )
    broker.put_credential(
        "example-login",
        "Example login",
        f"{origin}/login",
        "synthetic@example.test",
        "synthetic-password",
    )
    broker.put_payment_card(
        "travel-card",
        "Travel Platinum",
        "4111 1111 1111 1111",
        "05",
        "2031",
        security_code="123",
        network="Visa",
        allowed_origins=[origin],
        allowed_purposes=[purpose],
    )

    broker.grant(agent_id, "profile.read", "full_name", origin, purpose)
    broker.grant(agent_id, "credential.read", "example-login", "example.test", purpose)
    broker.grant(agent_id, "credential.use", "example-login", "example.test", purpose)
    broker.grant(agent_id, "financial.read", "travel-card", "example.test", purpose)

    metadata = _call(broker, "list_profile_fields", {"agent_id": agent_id})
    assert metadata[0]["field_id"] == "full_name"
    assert "value" not in metadata[0]

    profile = _call(
        broker,
        "request_profile_fields",
        {
            "agent_id": agent_id,
            "agent_token": token,
            "field_ids": ["full_name"],
            "target_origin": origin,
            "purpose": purpose,
        },
    )
    assert profile["full_name"]["value"] == "Synthetic User"

    direct = _call(
        broker,
        "request_credential",
        {
            "agent_id": agent_id,
            "agent_token": token,
            "delivery": "direct",
            "credential_id": "example-login",
            "field_ids": ["username"],
            "target_origin": origin,
            "purpose": purpose,
        },
    )
    assert direct["username"] == "synthetic@example.test"
    assert "password" not in direct

    extension = _call(
        broker,
        "request_credential",
        {
            "agent_id": agent_id,
            "agent_token": token,
            "delivery": "extension",
            "credential_id": "example-login",
            "target_origin": origin,
            "purpose": purpose,
        },
    )
    assert "handle" in extension
    assert "password" not in extension
    consumed = broker.consume_credential_handle(
        extension["handle"], agent_id, f"{origin}/checkout"
    )
    assert consumed["password"] == "synthetic-password"

    matches = _call(
        broker,
        "match_financial_records",
        {
            "agent_id": agent_id,
            "agent_token": token,
            "kind": "payment_card",
            "matching_hints": {"network": "Visa", "last4": "1111"},
        },
    )
    assert matches[0]["record_id"] == "travel-card"
    assert "values" not in matches[0]

    financial = _call(
        broker,
        "request_financial_fields",
        {
            "agent_id": agent_id,
            "agent_token": token,
            "record_id": "travel-card",
            "field_ids": ["card_number", "expiration_month"],
            "target_origin": f"{origin}/checkout",
            "purpose": purpose,
        },
    )
    assert financial["card_number"] == "4111111111111111"
    assert financial["expiration_month"] == "05"

    broker.add_source("synthetic-mail", "email", "Synthetic mailbox")
    broker.store.put(
        "connection",
        "synthetic-mail",
        {
            "source_id": "synthetic-mail",
            "kind": "imap",
            "username": "owner@example.test",
        },
    )
    broker.email["synthetic-mail"] = [
        EmailMessage(
            "mail-1",
            "synthetic-mail",
            "no-reply@auth.example.test",
            "Your login code",
            "Use 654321 at https://auth.example.test",
            datetime.now(UTC).isoformat(),
        )
    ]
    broker.grant(
        agent_id,
        "email.otp",
        "synthetic-mail",
        "https://auth.example.test",
        purpose,
    )
    otp = _call(
        broker,
        "request_auth_code_for_recipient",
        {
            "agent_id": agent_id,
            "agent_token": token,
            "recipient": "owner@example.test",
            "target_origin": "https://auth.example.test",
            "purpose": purpose,
        },
    )
    assert otp["code"] == "654321"
    assert "654321" not in str(broker.store.audits())

    broker.texts = [
        TextMessage(
            "text-1",
            "support-thread",
            "+1-555-0100",
            "Your support code is 777888",
            datetime.now(UTC).isoformat(),
        )
    ]
    broker.grant(agent_id, "messages.search", "support-thread", None, None)
    broker.grant(agent_id, "messages.read", "support-thread", None, None)
    messages = _call(
        broker,
        "search_messages",
        {
            "agent_id": agent_id,
            "agent_token": token,
            "conversation_scope": "support-thread",
            "query": "support code",
        },
    )
    assert messages[0]["message_id"] == "text-1"
    message = _call(
        broker,
        "get_message",
        {
            "agent_id": agent_id,
            "agent_token": token,
            "message_id": "text-1",
        },
    )
    assert message["body"] == "Your support code is 777888"


def test_propose_new_entry_requires_review_and_exact_write_confirmation() -> None:
    broker, agent_id, token = _broker()
    origin = "https://example.test"
    purpose = "synthetic profile setup"
    broker.grant(agent_id, "profile.write", "shipping_city", "example.test", purpose)
    entry = {
        "record_type": "profile",
        "record_id": "shipping_city",
        "category": "address",
        "display_name": "Shipping city",
        "value": "Example City",
        "allowed_origins": [origin],
        "allowed_purposes": [purpose],
    }

    with pytest.raises(BrokerError, match="confirmed must be true"):
        _call(
            broker,
            "write_records",
            {
                "agent_id": agent_id,
                "agent_token": token,
                "target_origin": origin,
                "purpose": purpose,
                "entries": [entry],
            },
        )
    assert broker.list_profile_fields() == []

    result = _call(
        broker,
        "write_records",
        {
            "agent_id": agent_id,
            "agent_token": token,
            "target_origin": origin,
            "purpose": purpose,
            "confirmed": True,
            "entries": [entry],
        },
    )
    assert result == {
        "records": [
            {"record_type": "profile", "record_id": "shipping_city", "status": "created"}
        ]
    }
    assert "Example City" not in str(broker.list_profile_fields())


def test_missing_field_use_once_stays_transient_before_save_promotion() -> None:
    """A just-in-time answer is usable now, but only an approved row is saved."""

    broker, agent_id, token = _broker()
    origin = "https://example.test"
    purpose = "synthetic application"
    field_id = "company_name"
    value = "Synthetic Example Co"

    # This is the use-once branch of the agent check-in. The acting agent can
    # continue with ``value`` in its current form, but the broker has no row.
    assert broker.store.get("profile", field_id) is None

    proposal = {
        "record_type": "profile",
        "record_id": field_id,
        "category": "work",
        "display_name": "Company name",
        "value": value,
        "provenance": {
            "source": "user-supplied-during-form",
            "target_origin": origin,
            "purpose": purpose,
        },
    }

    # Choosing save promotes the answer to an exact row, but no mutation is
    # allowed until the user has approved the visible proposal and the agent
    # has the matching type-specific write grant.
    with pytest.raises(BrokerError, match="confirmed must be true"):
        _call(
            broker,
            "write_records",
            {
                "agent_id": agent_id,
                "agent_token": token,
                "target_origin": origin,
                "purpose": purpose,
                "entries": [proposal],
            },
        )
    assert broker.store.get("profile", field_id) is None

    with pytest.raises(BrokerError, match="grant"):
        _call(
            broker,
            "write_records",
            {
                "agent_id": agent_id,
                "agent_token": token,
                "target_origin": origin,
                "purpose": purpose,
                "confirmed": True,
                "entries": [proposal],
            },
        )
    assert broker.store.get("profile", field_id) is None

    broker.grant(agent_id, "profile.write", field_id, "example.test", purpose)
    result = _call(
        broker,
        "write_records",
        {
            "agent_id": agent_id,
            "agent_token": token,
            "target_origin": origin,
            "purpose": purpose,
            "confirmed": True,
            "entries": [proposal],
        },
    )
    assert result["records"] == [
        {"record_type": "profile", "record_id": field_id, "status": "created"}
    ]
    assert broker.store.get("profile", field_id)["value"] == value
    assert broker.store.get("profile", field_id)["allowed_origins"] == []
    assert broker.store.get("profile", field_id)["allowed_purposes"] == []

    # The saved profile is reusable at another origin, but the later read still
    # needs its own exact read grant and purpose.
    later_origin = "https://another.example"
    later_purpose = "synthetic follow-up form"
    broker.grant(agent_id, "profile.read", field_id, "another.example", later_purpose)
    later = _call(
        broker,
        "request_profile_fields",
        {
            "agent_id": agent_id,
            "agent_token": token,
            "field_ids": [field_id],
            "target_origin": later_origin,
            "purpose": later_purpose,
        },
    )
    assert later[field_id]["value"] == value


def test_missing_field_promotion_rejects_transient_values() -> None:
    broker, agent_id, token = _broker()
    origin = "https://example.test"
    purpose = "synthetic verification"
    broker.grant(agent_id, "profile.write", "verification_code", "example.test", purpose)

    with pytest.raises(BrokerError, match="transient"):
        _call(
            broker,
            "write_records",
            {
                "agent_id": agent_id,
                "agent_token": token,
                "target_origin": origin,
                "purpose": purpose,
                "confirmed": True,
                "entries": [
                    {
                        "record_type": "profile",
                        "record_id": "verification_code",
                        "category": "verification",
                        "display_name": "Verification code",
                        "value": "synthetic-code",
                    }
                ],
            },
        )
    assert broker.store.get("profile", "verification_code") is None


def _recipe_namespace() -> dict[str, object]:
    reference = (
        Path(__file__).parents[2]
        / ".agents"
        / "skills"
        / "agent-autofill-chrome-intake"
        / "references"
        / "local-intake.md"
    )
    blocks = re.findall(r"```python\n(.*?)```", reference.read_text(), re.DOTALL)
    assert len(blocks) == 1
    namespace: dict[str, object] = {}
    exec(compile(blocks[0], str(reference), "exec"), namespace)  # noqa: S102 - exercise the repository's documented recipe
    return namespace


def test_import_from_chrome_is_review_first_and_supports_partial_approval(
    tmp_path: Path,
) -> None:
    namespace = _recipe_namespace()
    profile = tmp_path / "Default"
    profile.mkdir()
    db_path = profile / "Web Data"
    db = sqlite3.connect(db_path)
    db.execute("create table addresses (guid text primary key)")
    db.execute("create table address_type_tokens (guid text, type integer, value text)")
    db.execute("insert into addresses values (?)", ("synthetic-home",))
    db.executemany(
        "insert into address_type_tokens values (?, ?, ?)",
        [("synthetic-home", 33, "Example City"), ("synthetic-home", 36, "US")],
    )
    db.commit()
    db.close()
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    candidates = namespace["read_structured_addresses"](profile)
    assert {row["field_type"] for row in candidates} == {"city", "country"}
    proposals = []
    for candidate in candidates:
        proposals.append(
            {
                "candidate_id": candidate["candidate_id"],
                "field_id": f"chrome_{candidate['field_type']}",
                "action": "add",
                "category": "address",
                "display_name": candidate["field_type"],
                "value": candidate["value"],
                "sensitivity": "normal",
                "allowed_origins": [],
                "allowed_purposes": [],
                "source_profile_id": candidate["source_profile_id"],
                "source_guid": candidate["source_guid"],
                "field_type": candidate["field_type"],
                "provenance": {
                    "source": "chrome-structured-address",
                    "source_profile_id": candidate["source_profile_id"],
                    "source_guid": candidate["source_guid"],
                    "field_type": candidate["field_type"],
                },
            }
        )

    broker = Broker(Store(":memory:", key=b"c" * 32))
    cancelled = namespace["save_approved_profiles"](broker, proposals)
    assert cancelled["status"] == "cancelled"
    assert cancelled["saved_ids"] == []
    assert broker.list_profile_fields() == []

    saved = namespace["save_approved_profiles"](
        broker, proposals, [proposals[0]["candidate_id"]]
    )
    assert saved["status"] == "complete"
    assert saved["saved_ids"] == ["chrome_city"]
    assert saved["pending_ids"] == [proposals[1]["candidate_id"]]
    assert broker.store.get("profile", "chrome_city")["value"] == "Example City"
    assert before == hashlib.sha256(db_path.read_bytes()).hexdigest()
