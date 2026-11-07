import sqlite3

import pytest

from agent_autofill.messages_adapter import MacMessagesAdapter


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
