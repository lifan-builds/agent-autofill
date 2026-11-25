# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Agent Autofill; Section 7 attribution terms in ATTRIBUTION.md.
"""Read-only macOS Messages database adapter."""

import math
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import TextMessage

_APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)
_APPLE_NANO_THRESHOLD = 1_000_000_000_000
_APPLE_SECOND_MIN = 100_000_000  # ~2004 in Apple epoch seconds
_FDA_NEXT_ACTION = (
    "Grant Full Disk Access to the terminal or host app running Agent Autofill in "
    "System Settings > Privacy & Security > Full Disk Access, restart it, then run "
    "'agent-autofill messages-test'"
)
_NOT_FOUND_NEXT_ACTION = (
    "Open Messages on this Mac (and enable iPhone Settings > Apps > Messages > "
    "Text Message Forwarding for SMS 2FA), or pass --database to 'agent-autofill messages-add'"
)


class MessagesUnavailable(RuntimeError):
    """Value-free Messages database access failure with a concrete recovery step."""

    def __init__(
        self,
        message: str,
        *,
        reason: str = "messages_unavailable",
        next_action: str = _FDA_NEXT_ACTION,
    ):
        self.reason = reason
        self.next_action = next_action
        super().__init__(message)


def _normalize_sender_digits(value: str) -> str:
    return re.sub(r"\D+", "", value or "")


def _sender_matches_filter(candidate: str, wanted: str) -> bool:
    cand_clean = (candidate or "").strip()
    want_clean = (wanted or "").strip()
    if not want_clean:
        return True
    if cand_clean.casefold() == want_clean.casefold():
        return True
    cand_digits = _normalize_sender_digits(cand_clean)
    want_digits = _normalize_sender_digits(want_clean)
    if not want_digits or not cand_digits:
        return False
    if cand_digits == want_digits:
        return True
    # Match E.164 vs 10-digit US/CA numbers (e.g. +18005551234 vs 8005551234).
    if len(want_digits) == 10 and len(cand_digits) == 11 and cand_digits.startswith("1"):
        return cand_digits[1:] == want_digits
    if len(cand_digits) == 10 and len(want_digits) == 11 and want_digits.startswith("1"):
        return want_digits[1:] == cand_digits
    return False


def _sender_from_chat(chat_identifier: object, guid: object) -> str:
    if isinstance(chat_identifier, str) and chat_identifier.strip():
        return chat_identifier.strip()
    if isinstance(guid, str) and ";-;" in guid:
        tail = guid.rsplit(";-;", 1)[1].strip()
        if tail:
            return tail
    return ""


class MacMessagesAdapter:
    def __init__(self, database: str | Path | None = None):
        self.database = Path(database or Path.home() / "Library/Messages/chat.db")

    def _open_readonly(self) -> sqlite3.Connection:
        try:
            exists = self.database.exists()
        except PermissionError as exc:
            raise MessagesUnavailable(
                "macOS denied read access to the Messages database (Full Disk Access required)",
                reason="messages_permission_denied",
                next_action=_FDA_NEXT_ACTION,
            ) from exc
        if not exists:
            raise MessagesUnavailable(
                f"Messages database not found: {self.database}",
                reason="messages_not_found",
                next_action=_NOT_FOUND_NEXT_ACTION,
            )
        try:
            db = sqlite3.connect(
                f"file:{self.database}?mode=ro", uri=True, timeout=5.0
            )
            db.execute("PRAGMA query_only = ON")
            return db
        except PermissionError as exc:
            raise MessagesUnavailable(
                "macOS denied read access to the Messages database (Full Disk Access required)",
                reason="messages_permission_denied",
                next_action=_FDA_NEXT_ACTION,
            ) from exc
        except sqlite3.OperationalError as exc:
            detail = str(exc).lower()
            if any(
                token in detail
                for token in ("unable to open", "authorization denied", "permission", "not permitted")
            ):
                raise MessagesUnavailable(
                    "macOS denied read access to the Messages database (Full Disk Access required)",
                    reason="messages_permission_denied",
                    next_action=_FDA_NEXT_ACTION,
                ) from exc
            raise MessagesUnavailable(
                "Messages database could not be opened read-only",
                reason="messages_unavailable",
                next_action="Verify Messages is synced on this Mac and rerun 'agent-autofill messages-test'",
            ) from exc

    def _db(self) -> sqlite3.Connection:
        return self._open_readonly()

    def check_connection(self) -> dict[str, str]:
        """Verify read-only access to the local Messages database without reading content."""

        db = self._open_readonly()
        try:
            db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            return {"status": "ready"}
        except PermissionError as exc:
            raise MessagesUnavailable(
                "macOS denied read access to the Messages database (Full Disk Access required)",
                reason="messages_permission_denied",
                next_action=_FDA_NEXT_ACTION,
            ) from exc
        except sqlite3.Error as exc:
            raise MessagesUnavailable(
                "Messages database could not be read",
                reason="messages_unavailable",
                next_action="Verify Messages is synced on this Mac and rerun 'agent-autofill messages-test'",
            ) from exc
        finally:
            db.close()

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
        if isinstance(raw, bool):
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        try:
            if _APPLE_SECOND_MIN <= abs(value) < _APPLE_NANO_THRESHOLD:
                seconds = value
            else:
                seconds = value / 1_000_000_000
            return (_APPLE_EPOCH + timedelta(seconds=seconds)).isoformat()
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _apple_timestamp_cutoffs(cutoff: datetime) -> tuple[int, int]:
        seconds = int((cutoff - _APPLE_EPOCH).total_seconds())
        return seconds * 1_000_000_000, seconds

    @classmethod
    def _extract_attributed_text(cls, raw: object) -> str:
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw.replace("\ufffc", "").strip()
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
                                .replace("\ufffc", "")
                                .strip()
                            )
                            if candidate:
                                return candidate
        decoded = data.decode("utf-8", errors="ignore").replace("\ufffc", " ")
        for token in cls._TYPEDSTREAM_TOKENS:
            decoded = decoded.replace(token, " ")
        cleaned = " ".join(
            "".join(ch if ch.isprintable() else " " for ch in decoded).split()
        )
        return cleaned.strip()

    @staticmethod
    def _table_columns(db: sqlite3.Connection, table: str) -> set[str]:
        try:
            return {row[1] for row in db.execute(f"pragma table_info({table})")}
        except sqlite3.Error:
            return set()

    @classmethod
    def _has_attributed_body(cls, db: sqlite3.Connection) -> bool:
        return "attributedBody" in cls._table_columns(db, "message")

    @classmethod
    def _row(cls, row) -> TextMessage:
        chat_identifier = None
        is_from_me = None
        if len(row) == 9:
            (
                rowid,
                text,
                attributed_body,
                sender,
                date,
                guid,
                chat_id,
                chat_identifier,
                is_from_me,
            ) = row
        elif len(row) == 7:
            rowid, text, attributed_body, sender, date, guid, chat_id = row
        else:
            rowid, text, sender, date, guid, chat_id = row
            attributed_body = None
        body = (
            text.replace("\ufffc", "").strip()
            if (isinstance(text, str) and text.replace("\ufffc", "").strip())
            else cls._extract_attributed_text(attributed_body)
        )
        if is_from_me == 1:
            resolved_sender = "me"
        else:
            resolved_sender = (
                (sender.strip() if isinstance(sender, str) and sender.strip() else "")
                or _sender_from_chat(chat_identifier, guid)
                or "me"
            )
        return TextMessage(
            str(rowid),
            guid or f"chat:{chat_id}",
            resolved_sender,
            body or "",
            cls._date(date),
        )

    @classmethod
    def _select_clause(cls, db: sqlite3.Connection) -> tuple[str, set[str], set[str]]:
        msg_cols = cls._table_columns(db, "message")
        chat_cols = cls._table_columns(db, "chat")
        attr_expr = "m.attributedBody" if "attributedBody" in msg_cols else "NULL"
        ident_expr = "c.chat_identifier" if "chat_identifier" in chat_cols else "NULL"
        from_me_expr = "m.is_from_me" if "is_from_me" in msg_cols else "NULL"
        cols = (
            f"m.ROWID,m.text,{attr_expr},h.id,m.date,c.guid,cmj.chat_id,"
            f"{ident_expr},{from_me_expr}"
        )
        return cols, msg_cols, chat_cols

    def recent(
        self,
        max_age_seconds: int = 300,
        conversation_scope: str = "*",
        sender: str | None = None,
        limit: int = 100,
    ) -> list[TextMessage]:
        """Return recent incoming text messages within the requested age window."""

        db = self._db()
        try:
            cols, msg_cols, _ = self._select_clause(db)
            cutoff = datetime.now(UTC) - timedelta(seconds=max(1, int(max_age_seconds)))
            cutoff_ns, cutoff_sec = self._apple_timestamp_cutoffs(cutoff)
            clauses = [
                f"(m.date >= ? OR (m.date >= ? AND m.date < {_APPLE_NANO_THRESHOLD}))"
            ]
            args: list[str | int] = [cutoff_ns, cutoff_sec]
            if "is_from_me" in msg_cols:
                clauses.append("(m.is_from_me = 0 OR m.is_from_me IS NULL)")
            if "associated_message_type" in msg_cols:
                clauses.append(
                    "(m.associated_message_type = 0 OR m.associated_message_type IS NULL)"
                )
            if conversation_scope != "*":
                clauses.append("(c.guid=? or ('chat:' || cmj.chat_id)=?)")
                args.extend([conversation_scope, conversation_scope])
            sql = (
                f"select {cols} "
                "from message m left join handle h on h.ROWID=m.handle_id "
                "left join chat_message_join cmj on cmj.message_id=m.ROWID "
                "left join chat c on c.ROWID=cmj.chat_id "
                f"where {' and '.join(clauses)} "
                "order by m.date desc"
            )
            bounded = max(1, min(int(limit), 200))
            if not sender:
                sql += " limit ?"
                args.append(bounded)
                return [
                    msg
                    for msg in (self._row(r) for r in db.execute(sql, args))
                    if msg.body
                ]
            matches: list[TextMessage] = []
            for raw_row in db.execute(sql, args):
                msg = self._row(raw_row)
                if not msg.body:
                    continue
                if _sender_matches_filter(msg.sender, sender):
                    matches.append(msg)
                    if len(matches) >= bounded:
                        break
            return matches
        finally:
            db.close()

    def search(
        self, query: str, conversation_scope: str = "*", limit: int = 20
    ) -> list[TextMessage]:
        db = self._db()
        try:
            cols, msg_cols, _ = self._select_clause(db)
            has_attr = "attributedBody" in msg_cols
            if not has_attr:
                sql = f"""select {cols}
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

            sql = f"""select {cols}
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
            cols, _, _ = self._select_clause(db)
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
