"""Read-only macOS Messages database adapter."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

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

    _TYPEDSTREAM_TOKENS = (
        "streamtyped",
        "NSMutableAttributedString",
        "NSAttributedString",
        "NSMutableString",
        "NSString",
        "NSDictionary",
        "NSMutableDictionary",
        "NSNumber",
        "NSValue",
        "NSObject",
        "__kIMMessagePartAttributeName",
        "__kIMFileTransferGUIDAttributeName",
        "__kIMCalendarEventAttributeName",
        "__kIMBaseWritingDirectionAttributeName",
    )

    @staticmethod
    def _date(raw) -> str | None:
        try:
            seconds = float(raw) / 1_000_000_000
            return (
                datetime(2001, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)
            ).isoformat()
        except (TypeError, ValueError, OverflowError):
            return None

    @classmethod
    def _extract_attributed_text(cls, raw: object) -> str:
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw.strip()
        if isinstance(raw, memoryview):
            raw = raw.tobytes()
        if not isinstance(raw, (bytes, bytearray)) or not raw:
            return ""
        data = bytes(raw)
        for marker in (b"NSString", b"NSMutableString"):
            idx = data.find(marker)
            if idx != -1:
                tail = data[idx + len(marker) :]
                plus = tail.find(b"+")
                if 0 <= plus <= 16:
                    pos = plus + 1
                    if pos < len(tail):
                        length_byte = tail[pos]
                        pos += 1
                        if length_byte == 0x81 and pos + 2 <= len(tail):
                            length = int.from_bytes(tail[pos : pos + 2], "little")
                            pos += 2
                        elif length_byte == 0x82 and pos + 4 <= len(tail):
                            length = int.from_bytes(tail[pos : pos + 4], "little")
                            pos += 4
                        else:
                            length = length_byte
                        if 0 < length <= len(tail) - pos:
                            candidate = (
                                tail[pos : pos + length]
                                .decode("utf-8", errors="ignore")
                                .strip()
                            )
                            if candidate:
                                return candidate
        decoded = data.decode("utf-8", errors="ignore")
        for token in cls._TYPEDSTREAM_TOKENS:
            decoded = decoded.replace(token, " ")
        cleaned = " ".join(
            "".join(ch if ch.isprintable() else " " for ch in decoded).split()
        )
        return cleaned.strip()

    @staticmethod
    def _has_attributed_body(db: sqlite3.Connection) -> bool:
        try:
            cols = {row[1] for row in db.execute("pragma table_info(message)")}
            return "attributedBody" in cols
        except sqlite3.Error:
            return False

    @classmethod
    def _row(cls, row) -> TextMessage:
        if len(row) == 7:
            rowid, text, attributed_body, sender, date, guid, chat_id = row
        else:
            rowid, text, sender, date, guid, chat_id = row
            attributed_body = None
        body = (
            text
            if (isinstance(text, str) and text.strip())
            else cls._extract_attributed_text(attributed_body)
        )
        return TextMessage(
            str(rowid),
            guid or f"chat:{chat_id}",
            sender or "me",
            body or "",
            cls._date(date),
        )

    def search(
        self, query: str, conversation_scope: str = "*", limit: int = 20
    ) -> list[TextMessage]:
        db = self._db()
        try:
            has_attr = self._has_attributed_body(db)
            if not has_attr:
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

            sql = """select m.ROWID,m.text,m.attributedBody,h.id,m.date,c.guid,cmj.chat_id
                     from message m left join handle h on h.ROWID=m.handle_id
                     left join chat_message_join cmj on cmj.message_id=m.ROWID
                     left join chat c on c.ROWID=cmj.chat_id
                     where (m.text like ? or ((m.text is null or m.text = '') and m.attributedBody is not null))"""
            args = [f"%{query}%"]
            if conversation_scope != "*":
                sql += " and (c.guid=? or ('chat:' || cmj.chat_id)=?)"
                args.extend([conversation_scope, conversation_scope])
            sql += " order by m.date desc"
            needle = query.lower()
            matches: list[TextMessage] = []
            for raw_row in db.execute(sql, args):
                msg = self._row(raw_row)
                if needle in msg.body.lower():
                    matches.append(msg)
                    if len(matches) >= limit:
                        break
            return matches
        finally:
            db.close()

    def get(self, message_id: str) -> TextMessage:
        db = self._db()
        try:
            cols = (
                "m.ROWID,m.text,m.attributedBody,h.id,m.date,c.guid,cmj.chat_id"
                if self._has_attributed_body(db)
                else "m.ROWID,m.text,h.id,m.date,c.guid,cmj.chat_id"
            )
            row = db.execute(
                f"""select {cols}
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
