# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Agent Autofill; Section 7 attribution terms in ATTRIBUTION.md.
"""Deterministic adversarial regression suite for email & macOS Messages OTP extraction."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_autofill.messages_adapter import MacMessagesAdapter
from agent_autofill.models import EmailMessage, TextMessage
from agent_autofill.service import AuthCodeError, Broker
from agent_autofill.storage import Store

_CORPUS_PATH = Path(__file__).parent / "fixtures" / "adversarial_otp_corpus.json"
_CORPUS = json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))


def _make_broker() -> Broker:
    broker = Broker(Store(":memory:", key=b"a" * 32))
    broker.create_agent("service:local")
    broker.enable_trusted_local("service:local")
    return broker


@pytest.mark.parametrize(
    "case",
    _CORPUS["email_cases"],
    ids=[item["id"] for item in _CORPUS["email_cases"]],
)
def test_adversarial_email_otp_corpus(case: dict):
    broker = _make_broker()
    broker.add_source("mail", "email", "Synthetic Mail")
    received_at = (datetime.now(UTC) - timedelta(seconds=int(case["age_seconds"]))).isoformat()
    broker.email["mail"] = [
        EmailMessage(
            message_id=case["id"],
            account_id="mail",
            sender=case["sender"],
            subject=case["subject"],
            body=case["body"],
            received_at=received_at,
        )
    ]

    if case["expected_outcome"] == "code":
        result = broker.request_auth_code(
            "service:local",
            "mail",
            target_origin=case["target_origin"],
            purpose="login",
            origin_policy=case["origin_policy"],
        )
        assert result["code"] == case["expected_code"], case["description"]
        assert result["provenance"]["message_id"] == case["id"]
        assert case["expected_code"] not in str(broker.store.audits())
        rows = broker.store.db.execute(
            "select namespace,key,value from kv where namespace like 'otp-%'"
        ).fetchall()
        assert case["expected_code"] not in str(rows)
    else:
        with pytest.raises(AuthCodeError) as exc:
            broker.request_auth_code(
                "service:local",
                "mail",
                target_origin=case["target_origin"],
                purpose="login",
                origin_policy=case["origin_policy"],
            )
        assert exc.value.reason == case["expected_error_reason"], (
            f"{case['id']}: {case['description']}"
        )
        assert broker.store.count("otp-consumed") == 0


@pytest.mark.parametrize(
    "case",
    _CORPUS["messages_cases"],
    ids=[item["id"] for item in _CORPUS["messages_cases"]],
)
def test_adversarial_messages_otp_corpus(case: dict):
    broker = _make_broker()
    broker.add_source("messages", "messages", "Mac Messages")
    sent_at = (datetime.now(UTC) - timedelta(seconds=int(case["age_seconds"]))).isoformat()
    broker.texts = [
        TextMessage(
            message_id=case["id"],
            conversation_id=f"SMS;-;{case['sender']}",
            sender=case["sender"],
            body=case["body"],
            sent_at=sent_at,
        )
    ]

    if case["expected_outcome"] == "code":
        result = broker.request_messages_auth_code(
            "service:local",
            target_origin=case["target_origin"],
            purpose="login",
            origin_policy=case["origin_policy"],
        )
        assert result["code"] == case["expected_code"], case["description"]
        assert result["provenance"]["message_id"] == case["id"]
        assert case["expected_code"] not in str(broker.store.audits())
        rows = broker.store.db.execute(
            "select namespace,key,value from kv where namespace like 'otp-%'"
        ).fetchall()
        assert case["expected_code"] not in str(rows)
    else:
        with pytest.raises(AuthCodeError) as exc:
            broker.request_messages_auth_code(
                "service:local",
                target_origin=case["target_origin"],
                purpose="login",
                origin_policy=case["origin_policy"],
            )
        assert exc.value.reason == case["expected_error_reason"], (
            f"{case['id']}: {case['description']}"
        )
        assert broker.store.count("otp-consumed") == 0


@pytest.mark.parametrize(
    "case",
    _CORPUS["ax_snapshot_cases"],
    ids=[item["id"] for item in _CORPUS["ax_snapshot_cases"]],
)
def test_adversarial_messages_ax_snapshot_corpus(case: dict, tmp_path, monkeypatch):
    now_local = datetime.now().astimezone().replace(second=20, microsecond=0)
    now_12h = now_local.strftime("%I:%M %p").lstrip("0")
    one_min_12h = (now_local - timedelta(minutes=1)).strftime("%I:%M %p").lstrip("0")
    snapshot = "\n".join(
        line.replace("{NOW_12H}", now_12h).replace("{ONE_MIN_AGO_12H}", one_min_12h)
        for line in case["snapshot_lines"]
    )

    adapter = MacMessagesAdapter(backend="ax", fmu_socket=tmp_path / "missing.sock")
    monkeypatch.setattr(adapter, "_query_fmu_cli", lambda _req: snapshot)

    broker = _make_broker()
    broker.register_message_adapter("messages", adapter, "Mac Messages")

    if case["expected_outcome"] == "code":
        result = broker.request_messages_auth_code(
            "service:local",
            target_origin=case["target_origin"],
            purpose="login",
            origin_policy=case["origin_policy"],
        )
        assert result["code"] == case["expected_code"], case["description"]
        assert result["provenance"]["message_id"].startswith("ax:")
        assert case["expected_code"] not in str(broker.store.audits())
    else:
        with pytest.raises(AuthCodeError) as exc:
            broker.request_messages_auth_code(
                "service:local",
                target_origin=case["target_origin"],
                purpose="login",
                origin_policy=case["origin_policy"],
            )
        assert exc.value.reason == case["expected_error_reason"], (
            f"{case['id']}: {case['description']}"
        )
        assert broker.store.count("otp-consumed") == 0
