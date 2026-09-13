import sqlite3
from stopaskingme.messages_adapter import MacMessagesAdapter


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
