"""Read-only macOS Messages database adapter."""

from datetime import datetime, timezone, timedelta
from pathlib import Path
import sqlite3
from .models import TextMessage


class MessagesUnavailable(RuntimeError):
    pass


class MacMessagesAdapter:
    def __init__(self, database: str | Path | None = None):
        self.database = Path(database or Path.home() / "Library/Messages/chat.db")

    def _db(self):
        if not self.database.exists():
            raise MessagesUnavailable(f"Messages database not found: {self.database}")
        return sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)

    @staticmethod
    def _date(raw) -> str:
        try:
            seconds = float(raw) / 1_000_000_000
            return (
                datetime(2001, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)
            ).isoformat()
        except Exception:
            return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _row(row) -> TextMessage:
        rowid, text, sender, date, guid, chat_id = row
        return TextMessage(
            str(rowid),
            guid or f"chat:{chat_id}",
            sender or "me",
            text or "",
            MacMessagesAdapter._date(date),
        )

    def search(
        self, query: str, conversation_scope: str = "*", limit: int = 20
    ) -> list[TextMessage]:
        db = self._db()
        try:
            sql = """select m.ROWID,m.text,h.id,m.date,c.guid,cmj.chat_id
                     from message m left join handle h on h.ROWID=m.handle_id
                     left join chat_message_join cmj on cmj.message_id=m.ROWID
                     left join chat c on c.ROWID=cmj.chat_id
                     where m.text like ?"""
            args: list[str | int] = [f"%{query}%"]
            if conversation_scope != "*":
                sql += " and (c.guid=? or ('chat:' || cmj.chat_id)=?)"
                args.extend([conversation_scope, conversation_scope])
            sql += " order by m.date desc limit ?"
            args.append(limit)
            return [self._row(r) for r in db.execute(sql, args)]
        finally:
            db.close()

    def get(self, message_id: str) -> TextMessage:
        db = self._db()
        try:
            row = db.execute(
                """select m.ROWID,m.text,h.id,m.date,c.guid,cmj.chat_id
                from message m left join handle h on h.ROWID=m.handle_id
                left join chat_message_join cmj on cmj.message_id=m.ROWID
                left join chat c on c.ROWID=cmj.chat_id where m.ROWID=? limit 1""",
                (int(message_id),),
            ).fetchone()
            if row is None:
                raise KeyError(message_id)
            return self._row(row)
        finally:
            db.close()

    def list_conversations(self) -> list[dict]:
        db = self._db()
        try:
            return [
                {
                    "conversation_id": row[0] or f"chat:{row[1]}",
                    "title": row[2] or "",
                    "source": "mac-messages",
                }
                for row in db.execute(
                    "select c.guid,c.ROWID,c.display_name from chat c order by c.ROWID"
                )
            ]
        finally:
            db.close()
