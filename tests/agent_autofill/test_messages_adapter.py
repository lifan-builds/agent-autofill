# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Agent Autofill; Section 7 attribution terms in ATTRIBUTION.md.
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from agent_autofill.messages_adapter import (
    _APPLE_EPOCH,
    MacMessagesAdapter,
    MessagesUnavailable,
    parse_ax_messages_snapshot,
)


def test_messages_adapter_reads_only_requested_scope(tmp_path):
    path = tmp_path / "chat.db"
    db = sqlite3.connect(path)
    db.executescript("""
      create table message (ROWID integer primary key, text text, handle_id integer, date integer);
      create table handle (ROWID integer primary key, id text);
      create table chat_message_join (chat_id integer, message_id integer);
      create table chat (ROWID integer primary key, guid text, display_name text);
    """)
    db.execute("insert into chat values (1,'iMessage;-;+1555','Test')")
    db.execute("insert into handle values (1,'+1555')")
    db.execute("insert into message values (1,'hello',1,0)")
    db.execute("insert into chat_message_join values (1,1)")
    db.commit()
    db.close()
    adapter = MacMessagesAdapter(path)
    assert adapter.search("hello", "iMessage;-;+1555")[0].body == "hello"
    assert adapter.search("hello", "other") == []


@pytest.mark.parametrize("raw", [None, "invalid", float("nan"), float("inf"), 10**40])
def test_malformed_messages_dates_remain_unknown(raw):
    assert MacMessagesAdapter._date(raw) is None


def test_messages_date_preserves_apple_epoch():
    assert MacMessagesAdapter._date(0) == "2001-01-01T00:00:00+00:00"


def test_messages_adapter_decodes_attributed_body_when_text_is_null(tmp_path):
    path = tmp_path / "chat.db"
    db = sqlite3.connect(path)
    db.executescript("""
      create table message (ROWID integer primary key, text text, attributedBody blob, handle_id integer, date integer);
      create table handle (ROWID integer primary key, id text);
      create table chat_message_join (chat_id integer, message_id integer);
      create table chat (ROWID integer primary key, guid text, display_name text);
    """)
    payload = b"Your verification token is 482910"
    typedstream_blob = (
        b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00"
        b"\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+"
        + bytes([len(payload)])
        + payload
        + b"\x86\x84\x02iI\x01\x1c\x92"
    )
    db.execute("insert into chat values (1,'iMessage;-;+1555','Test')")
    db.execute("insert into handle values (1,'+1555')")
    db.execute("insert into message values (1,null,?,1,0)", (typedstream_blob,))
    db.execute("insert into chat_message_join values (1,1)")
    db.commit()
    db.close()
    adapter = MacMessagesAdapter(path)
    results = adapter.search("verification", "iMessage;-;+1555")
    assert len(results) == 1
    assert results[0].body == "Your verification token is 482910"
    assert adapter.get("1").body == "Your verification token is 482910"


def _apple_ns(dt: datetime) -> int:
    return int((dt - _APPLE_EPOCH).total_seconds() * 1_000_000_000)


def _create_full_messages_db(path):
    db = sqlite3.connect(path)
    db.executescript("""
      create table message (
        ROWID integer primary key,
        text text,
        attributedBody blob,
        handle_id integer,
        date integer,
        is_from_me integer default 0,
        associated_message_type integer default 0
      );
      create table handle (ROWID integer primary key, id text);
      create table chat_message_join (chat_id integer, message_id integer);
      create table chat (ROWID integer primary key, guid text, display_name text, chat_identifier text);
    """)
    return db


def test_messages_adapter_recent_filters_outbound_reactions_and_old_messages(tmp_path):
    path = tmp_path / "chat.db"
    db = _create_full_messages_db(path)
    now = datetime.now(UTC)
    db.execute("insert into chat values (1,'SMS;-;22395','','22395')")
    db.execute("insert into chat values (2,'SMS;-;+18005551234','','+18005551234')")
    db.execute("insert into handle values (1,'22395')")
    # 1: recent incoming short-code SMS with handle_id=0 (sender recovered from chat_identifier)
    db.execute(
        "insert into message values (1,'Your GitHub verification code is 482910',null,0,?,0,0)",
        (_apple_ns(now - timedelta(seconds=20)),),
    )
    # 2: recent outbound message (must be excluded)
    db.execute(
        "insert into message values (2,'My code is 999999',null,1,?,1,0)",
        (_apple_ns(now - timedelta(seconds=10)),),
    )
    # 3: tapback reaction (must be excluded)
    db.execute(
        "insert into message values (3,'Liked \"Your GitHub verification code is 482910\"',null,1,?,0,2000)",
        (_apple_ns(now - timedelta(seconds=5)),),
    )
    # 4: old message outside lookback
    db.execute(
        "insert into message values (4,'Your GitHub verification code is 111111',null,1,?,0,0)",
        (_apple_ns(now - timedelta(seconds=3600)),),
    )
    # 5: recent SMS in second chat with E.164 handle
    db.execute("insert into handle values (2,'+18005551234')")
    db.execute(
        "insert into message values (5,'G-654321 is your Google verification code.',null,2,?,0,0)",
        (_apple_ns(now - timedelta(seconds=15)),),
    )
    for mid, cid in [(1, 1), (2, 1), (3, 1), (4, 1), (5, 2)]:
        db.execute("insert into chat_message_join values (?,?)", (cid, mid))
    db.commit()
    db.close()

    adapter = MacMessagesAdapter(path)
    assert adapter.check_connection() == {"status": "ready", "backend": "db"}
    recent = adapter.recent(max_age_seconds=300)
    assert [m.message_id for m in recent] == ["5", "1"]
    assert recent[1].sender == "22395"
    # Sender filter supports both 10-digit US/CA numbers and short codes.
    filtered = adapter.recent(max_age_seconds=300, sender="800-555-1234")
    assert [m.message_id for m in filtered] == ["5"]
    scoped = adapter.recent(max_age_seconds=300, conversation_scope="SMS;-;22395")
    assert [m.message_id for m in scoped] == ["1"]


def test_messages_adapter_handles_legacy_second_timestamps(tmp_path):
    path = tmp_path / "chat.db"
    db = _create_full_messages_db(path)
    now = datetime.now(UTC)
    sec_ts = int((now - timedelta(seconds=30) - _APPLE_EPOCH).total_seconds())
    db.execute("insert into chat values (1,'SMS;-;22395','','22395')")
    db.execute("insert into handle values (1,'22395')")
    db.execute(
        "insert into message values (1,'Your Example code is 246810',null,1,?,0,0)",
        (sec_ts,),
    )
    db.execute("insert into chat_message_join values (1,1)")
    db.commit()
    db.close()

    adapter = MacMessagesAdapter(path)
    recent = adapter.recent(max_age_seconds=300)
    assert [m.message_id for m in recent] == ["1"]
    assert recent[0].sent_at is not None


def test_messages_adapter_check_connection_reports_missing_and_permission_denied(
    tmp_path, monkeypatch
):
    missing = MacMessagesAdapter(tmp_path / "missing.db")
    with pytest.raises(MessagesUnavailable) as missing_exc:
        missing.check_connection()
    assert missing_exc.value.reason == "messages_not_found"
    assert "Text Message Forwarding" in missing_exc.value.next_action

    existing = tmp_path / "chat.db"
    existing.write_bytes(b"")

    def deny(*_args, **_kwargs):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr("agent_autofill.messages_adapter.sqlite3.connect", deny)
    denied = MacMessagesAdapter(existing)
    with pytest.raises(MessagesUnavailable) as denied_exc:
        denied.check_connection()
    assert denied_exc.value.reason == "messages_permission_denied"
    assert "faster-mac-use" in denied_exc.value.next_action


def test_parse_ax_messages_snapshot_extracts_sender_body_and_timestamps():
    now_local = datetime.now().astimezone().replace(second=30, microsecond=0)
    one_min_ago = now_local - timedelta(minutes=1)
    ts_str = one_min_ago.strftime("%I:%M %p").lstrip("0")
    snapshot = "\n".join(
        [
            '# app="Messages" pid=1488 win="Messages" els=6 ms=15.0',
            '@a1 button "Compose"',
            f'@a2 statictext "22395, Unread, Your GitHub verification code is 482910. Valid for 10 min., {ts_str}"',
            f'@a3 statictext "72166, Chase: Use 123-456 to sign in., {ts_str}"',
            '@a4 statictext "22395, Unread, Liked \\"Your GitHub verification code is 482910\\", Now"',
            '@a5 statictext "55555, Unread, Old code is 111111, Yesterday"',
            '@a6 statictext "Today 3:00 PM"',
        ]
    )
    rows = parse_ax_messages_snapshot(snapshot, now=now_local)
    assert len(rows) == 4
    assert rows[0].sender == "22395"
    assert "482910" in rows[0].body
    assert rows[0].message_id.startswith("ax:")
    assert rows[0].conversation_id == "ax:22395"
    assert rows[1].sender == "72166"
    assert rows[1].body == "Chase: Use 123-456 to sign in."


def test_messages_adapter_ax_backend_works_without_chat_db_and_enforces_one_time_otp(
    tmp_path, monkeypatch
):
    from agent_autofill.service import AuthCodeError, Broker
    from agent_autofill.storage import Store

    now_local = datetime.now().astimezone().replace(second=20, microsecond=0)
    ts_str = now_local.strftime("%I:%M %p").lstrip("0")
    fake_snapshot = "\n".join(
        [
            '# app="Messages" pid=1488 win="Messages" els=4 ms=12.0',
            f'@a2 statictext "22395, Unread, Your GitHub verification code is 482910., {ts_str}"',
            f'@a3 statictext "22395, Unread, Liked \\"Your GitHub verification code is 999999\\", {ts_str}"',
            '@a4 statictext "55555, Unread, Old GitHub code is 111111, Yesterday"',
        ]
    )

    # Point default DB to a nonexistent path to prove AX mode never needs chat.db or FDA.
    adapter = MacMessagesAdapter(backend="auto", fmu_socket=tmp_path / "missing.sock")
    adapter.database = tmp_path / "nonexistent-chat.db"
    monkeypatch.setattr(adapter, "_query_fmu_cli", lambda _req: fake_snapshot)

    assert adapter.check_connection() == {"status": "ready", "backend": "ax"}
    recent = adapter.recent(max_age_seconds=300)
    assert len(recent) == 1
    assert recent[0].sender == "22395"
    assert adapter.list_conversations()[0]["source"] == "mac-messages-ax"

    broker = Broker(Store(":memory:", key=b"a" * 32))
    broker.register_message_adapter("messages", adapter, "Mac Messages")
    broker.create_agent("service:local")
    broker.enable_trusted_local("service:local")

    out = broker.request_messages_auth_code(
        "service:local", target_origin="https://github.com", purpose="login"
    )
    assert out["code"] == "482910"
    assert out["provenance"]["message_id"].startswith("ax:")
    with pytest.raises(AuthCodeError) as replay:
        broker.request_messages_auth_code(
            "service:local", target_origin="https://github.com", purpose="login"
        )
    assert replay.value.reason == "otp_consumed"


def test_messages_adapter_ax_errors_surface_actionable_reasons(tmp_path, monkeypatch):
    adapter = MacMessagesAdapter(backend="ax", fmu_socket=tmp_path / "missing.sock")

    monkeypatch.setattr(
        adapter, "_query_fmu_cli", lambda _req: "Error: AXIsProcessTrusted=false"
    )
    with pytest.raises(MessagesUnavailable) as ax_denied:
        adapter.check_connection()
    assert ax_denied.value.reason == "messages_accessibility_denied"
    assert "Accessibility" in ax_denied.value.next_action

    monkeypatch.setattr(
        adapter, "_query_fmu_cli", lambda _req: "Error: app 'Messages' is not running"
    )
    with pytest.raises(MessagesUnavailable) as not_running:
        adapter.check_connection()
    assert not_running.value.reason == "messages_app_not_running"
    assert "Messages app" in not_running.value.next_action
