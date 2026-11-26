# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Agent Autofill; Section 7 attribution terms in ATTRIBUTION.md.
"""Read-only macOS Messages adapter (faster-mac-use AX default, optional chat.db)."""

import hashlib
import json
import math
import os
import re
import shutil
import socket
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import TextMessage

_APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)
_APPLE_NANO_THRESHOLD = 1_000_000_000_000
_APPLE_SECOND_MIN = 100_000_000  # ~2004 in Apple epoch seconds
_DEFAULT_FMU_SOCK = "/tmp/fmu-ax.sock"
_VALID_BACKENDS = {"auto", "ax", "db"}

_AX_APP_NEXT_ACTION = (
    "Open the Messages app on this Mac (it can stay in the background) and enable "
    "iPhone Settings > Apps > Messages > Text Message Forwarding for SMS 2FA, then "
    "run 'agent-autofill messages-test'"
)
_AX_PERMISSION_NEXT_ACTION = (
    "Grant Accessibility permission for faster-mac-use (fmu-ax) in "
    "System Settings > Privacy & Security > Accessibility, then run 'agent-autofill messages-test'"
)
_AX_FMU_NEXT_ACTION = (
    "Ensure faster-mac-use (agy-cua / fmu-ax) is running with Messages.app open, "
    "or pass an explicit readable database to 'agent-autofill messages-add --database ...'"
)
_DB_NOT_FOUND_NEXT_ACTION = (
    "Open Messages on this Mac (and enable iPhone Settings > Apps > Messages > "
    "Text Message Forwarding for SMS 2FA), or pass --database to 'agent-autofill messages-add'"
)
_DB_DENIED_NEXT_ACTION = (
    "Use the default faster-mac-use AX backend (no Full Disk Access required) with Messages.app open, "
    "or grant read access to the configured Messages database"
)

_AX_STATICTEXT_RE = re.compile(r'^@\S+\s+statictext\s+"(.*)"\s*$')
_AX_TIME_12H_RE = re.compile(r"^(\d{1,2}):(\d{2})[\s\u202f\u00a0]*(AM|PM)$", re.IGNORECASE)
_AX_TIME_24H_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_AX_DATE_SLASH_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$")
_AX_DATE_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_AX_WEEKDAYS = {
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "mon",
    "tue",
    "wed",
    "thu",
    "fri",
    "sat",
    "sun",
}
_AX_ROW_FLAGS = {"unread", "pinned", "muted", "alerts off", "hide alerts"}
_AX_TAPBACK_PREFIXES = (
    'liked "',
    "liked \u201c",
    'loved "',
    "loved \u201c",
    'disliked "',
    "disliked \u201c",
    'laughed at "',
    "laughed at \u201c",
    'emphasized "',
    "emphasized \u201c",
    'questioned "',
    "questioned \u201c",
)
_AX_TAPBACK_RE = re.compile(
    r'^(?:reacted\s+[^\s"\u201c]{1,24}\s+to|removed\s+(?:an?\s+)?[^"\u201c]{1,32}\s+from)\s+["\u201c]',
    re.IGNORECASE,
)


class MessagesUnavailable(RuntimeError):
    """Value-free Messages access failure with a concrete recovery step."""

    def __init__(
        self,
        message: str,
        *,
        reason: str = "messages_unavailable",
        next_action: str = _AX_APP_NEXT_ACTION,
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
    if any(char == "@" or char.isalpha() for char in cand_clean + want_clean):
        return False
    cand_digits = _normalize_sender_digits(cand_clean)
    want_digits = _normalize_sender_digits(want_clean)
    if min(len(cand_digits), len(want_digits)) < 5:
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


def _parse_ax_timestamp(token: str, now_local: datetime) -> datetime | None:
    """Parse a macOS Messages sidebar timestamp token into a UTC datetime."""

    cleaned = token.replace("\u202f", " ").replace("\u00a0", " ").strip()
    if not cleaned:
        return None
    lowered = cleaned.lower()
    if lowered in {"now", "just now"}:
        return now_local.astimezone(UTC)
    if lowered == "yesterday":
        return (now_local - timedelta(days=1)).astimezone(UTC)
    if lowered in _AX_WEEKDAYS:
        return (now_local - timedelta(days=2)).astimezone(UTC)

    m12 = _AX_TIME_12H_RE.match(cleaned)
    if m12:
        hour = int(m12.group(1))
        minute = int(m12.group(2))
        meridiem = m12.group(3).upper()
        if not (1 <= hour <= 12 and 0 <= minute <= 59):
            return None
        hour24 = (hour % 12) + (12 if meridiem == "PM" else 0)
        candidate = now_local.replace(hour=hour24, minute=minute, second=0, microsecond=0)
        # Handle midnight rollover (e.g. 11:59 PM displayed right after 12:00 AM).
        if candidate > now_local + timedelta(seconds=60):
            candidate -= timedelta(days=1)
        elif candidate > now_local:
            candidate = now_local
        return candidate.astimezone(UTC)

    m24 = _AX_TIME_24H_RE.match(cleaned)
    if m24:
        hour24 = int(m24.group(1))
        minute = int(m24.group(2))
        if not (0 <= hour24 <= 23 and 0 <= minute <= 59):
            return None
        candidate = now_local.replace(hour=hour24, minute=minute, second=0, microsecond=0)
        if candidate > now_local + timedelta(seconds=60):
            candidate -= timedelta(days=1)
        elif candidate > now_local:
            candidate = now_local
        return candidate.astimezone(UTC)

    mslash = _AX_DATE_SLASH_RE.match(cleaned)
    if mslash:
        month, day, year = int(mslash.group(1)), int(mslash.group(2)), int(mslash.group(3))
        if year < 100:
            year += 2000
        try:
            return now_local.replace(
                year=year, month=month, day=day, hour=0, minute=0, second=0, microsecond=0
            ).astimezone(UTC)
        except ValueError:
            return None

    miso = _AX_DATE_ISO_RE.match(cleaned)
    if miso:
        year, month, day = int(miso.group(1)), int(miso.group(2)), int(miso.group(3))
        try:
            return now_local.replace(
                year=year, month=month, day=day, hour=0, minute=0, second=0, microsecond=0
            ).astimezone(UTC)
        except ValueError:
            return None

    return None


def _parse_ax_sidebar_row(text: str, now_local: datetime) -> TextMessage | None:
    """Parse one macOS Messages AX sidebar statictext into a TextMessage."""

    if ", " not in text:
        return None
    prefix, ts_token = text.rsplit(", ", 1)
    sent_utc = _parse_ax_timestamp(ts_token, now_local)
    if sent_utc is None:
        return None
    if ", " not in prefix:
        return None

    # Split sender and optional VoiceOver status flags (e.g. "Unread", "Pinned").
    parts = prefix.split(", ")
    flag_idx = None
    for idx in range(1, len(parts)):
        if parts[idx].strip().lower() in _AX_ROW_FLAGS:
            flag_idx = idx
            break
    if flag_idx is not None:
        sender = ", ".join(parts[:flag_idx]).strip()
        body_start = flag_idx
        while body_start < len(parts) and parts[body_start].strip().lower() in _AX_ROW_FLAGS:
            body_start += 1
        body = ", ".join(parts[body_start:]).strip()
    else:
        sender = parts[0].strip()
        body = ", ".join(parts[1:]).strip()

    if not sender or not body:
        return None
    digest = hashlib.sha256(f"{sender}\0{ts_token.strip()}\0{body}".encode()).hexdigest()[:20]
    return TextMessage(
        message_id=f"ax:{digest}",
        conversation_id=f"ax:{sender}",
        sender=sender,
        body=body,
        sent_at=sent_utc.isoformat(),
    )


def is_tapback_reaction_body(body: str) -> bool:
    """Return True if a Messages body is a Tapback reaction quoting another message."""

    cleaned = (body or "").strip()
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if any(lowered.startswith(prefix) for prefix in _AX_TAPBACK_PREFIXES):
        return True
    return bool(_AX_TAPBACK_RE.match(cleaned))


def _is_ax_tapback_body(body: str) -> bool:
    return is_tapback_reaction_body(body)


def _unescape_ax_text(raw: str) -> str:
    return (
        raw.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
        .replace("\ufffc", "")
    )


def parse_ax_messages_snapshot(
    snapshot: str, *, now: datetime | None = None
) -> list[TextMessage]:
    """Extract sidebar TextMessage items from a faster-mac-use Messages.app AX snapshot."""

    now_local = (
        now.astimezone() if now is not None and now.tzinfo is not None else datetime.now().astimezone()
    )
    messages: list[TextMessage] = []
    seen_ids: set[str] = set()
    for line in (snapshot or "").splitlines():
        match = _AX_STATICTEXT_RE.match(line.strip())
        if not match:
            continue
        raw_text = _unescape_ax_text(match.group(1))
        msg = _parse_ax_sidebar_row(raw_text, now_local)
        if msg is None or msg.message_id in seen_ids:
            continue
        seen_ids.add(msg.message_id)
        messages.append(msg)
    return messages


def _default_fmu_command() -> str | None:
    env_cmd = os.environ.get("AGENT_AUTOFILL_FMU_COMMAND", "").strip()
    if env_cmd:
        return env_cmd
    for candidate in ("agy-cua", "fmu"):
        found = shutil.which(candidate)
        if found:
            return found
    local_script = Path.home() / "Project" / "agent" / "scripts" / "agy-cua"
    if local_script.is_file() and os.access(local_script, os.X_OK):
        return str(local_script)
    return None


class MacMessagesAdapter:
    def __init__(
        self,
        database: str | Path | None = None,
        *,
        backend: str = "auto",
        fmu_socket: str | Path | None = None,
        fmu_command: str | None = None,
    ):
        normalized_backend = (backend or "auto").strip().lower()
        if normalized_backend not in _VALID_BACKENDS:
            raise ValueError("Messages backend must be auto, ax, or db")
        self._explicit_database = database is not None and str(database).strip() != ""
        self.database = Path(database or Path.home() / "Library/Messages/chat.db")
        self.backend = "db" if (normalized_backend == "auto" and self._explicit_database) else normalized_backend
        self.fmu_socket = Path(
            fmu_socket or os.environ.get("FMU_AX_SOCK", _DEFAULT_FMU_SOCK)
        )
        self.fmu_command = fmu_command

    # -- Read-only faster-mac-use AX backend (zero Full Disk Access) -----------

    def _query_fmu_socket(self, payload: bytes) -> str | None:
        af_unix = getattr(socket, "AF_UNIX", None)
        if af_unix is None or not self.fmu_socket.exists():
            return None
        sock = socket.socket(af_unix, socket.SOCK_STREAM)
        try:
            sock.settimeout(3.0)
            sock.connect(str(self.fmu_socket))
            sock.sendall(payload)
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            chunks: list[bytes] = []
            while True:
                buf = sock.recv(16384)
                if not buf:
                    break
                chunks.append(buf)
            return b"".join(chunks).decode("utf-8", errors="replace").strip()
        except OSError:
            return None
        finally:
            sock.close()

    def _query_fmu_cli(self, request_json: str) -> str | None:
        cmd = self.fmu_command or _default_fmu_command()
        if not cmd:
            return None
        try:
            proc = subprocess.run(
                [cmd, request_json],
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return (proc.stdout or proc.stderr or "").strip()

    def _ax_snapshot(self) -> str:
        req = json.dumps({"app": "Messages", "steps": [{"op": "snapshot"}]})
        raw = self._query_fmu_socket((req + "\n").encode("utf-8"))
        if raw is None:
            raw = self._query_fmu_cli(req)
        if not raw:
            raise MessagesUnavailable(
                "faster-mac-use Accessibility engine is unavailable for reading Messages.app",
                reason="messages_fmu_unavailable",
                next_action=_AX_FMU_NEXT_ACTION,
            )
        lowered = raw.lower()
        if "user_stopped" in lowered:
            raise MessagesUnavailable(
                "faster-mac-use is currently paused by the user",
                reason="messages_paused",
                next_action="Click Resume in the faster-mac-use menu bar item, then retry",
            )
        if "axisprocesstrusted=false" in lowered or "accessibility permission" in lowered:
            raise MessagesUnavailable(
                "macOS Accessibility permission is required for faster-mac-use to read Messages.app",
                reason="messages_accessibility_denied",
                next_action=_AX_PERMISSION_NEXT_ACTION,
            )
        if raw.startswith("Error:") or "not running" in lowered or "no window" in lowered:
            if any(tok in lowered for tok in ("not running", "not found", "no window", "cannot find", "could not find")):
                raise MessagesUnavailable(
                    "Messages.app is not open with an accessible window",
                    reason="messages_app_not_running",
                    next_action=_AX_APP_NEXT_ACTION,
                )
            raise MessagesUnavailable(
                "Messages.app could not be read through faster-mac-use Accessibility",
                reason="messages_unavailable",
                next_action=_AX_APP_NEXT_ACTION,
            )
        return raw

    def _ax_messages(self, *, now: datetime | None = None) -> list[TextMessage]:
        return parse_ax_messages_snapshot(self._ax_snapshot(), now=now)

    def _ax_check_connection(self) -> dict[str, str]:
        self._ax_snapshot()
        return {"status": "ready", "backend": "ax"}

    # -- Optional read-only chat.db SQLite backend -----------------------------

    def _open_readonly(self) -> sqlite3.Connection:
        try:
            exists = self.database.exists()
        except PermissionError as exc:
            raise MessagesUnavailable(
                "Messages database could not be read directly",
                reason="messages_permission_denied",
                next_action=_DB_DENIED_NEXT_ACTION,
            ) from exc
        if not exists:
            raise MessagesUnavailable(
                f"Messages database not found: {self.database}",
                reason="messages_not_found",
                next_action=_DB_NOT_FOUND_NEXT_ACTION,
            )
        try:
            db = sqlite3.connect(
                f"file:{self.database}?mode=ro", uri=True, timeout=5.0
            )
            db.execute("PRAGMA query_only = ON")
            return db
        except PermissionError as exc:
            raise MessagesUnavailable(
                "Messages database could not be read directly",
                reason="messages_permission_denied",
                next_action=_DB_DENIED_NEXT_ACTION,
            ) from exc
        except sqlite3.OperationalError as exc:
            detail = str(exc).lower()
            if any(
                token in detail
                for token in ("unable to open", "authorization denied", "permission", "not permitted")
            ):
                raise MessagesUnavailable(
                    "Messages database could not be read directly",
                    reason="messages_permission_denied",
                    next_action=_DB_DENIED_NEXT_ACTION,
                ) from exc
            raise MessagesUnavailable(
                "Messages database could not be opened read-only",
                reason="messages_unavailable",
                next_action="Verify Messages is synced on this Mac and rerun 'agent-autofill messages-test'",
            ) from exc

    def _db(self) -> sqlite3.Connection:
        return self._open_readonly()

    def _db_check_connection(self) -> dict[str, str]:
        db = self._open_readonly()
        try:
            db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            return {"status": "ready", "backend": "db"}
        except PermissionError as exc:
            raise MessagesUnavailable(
                "Messages database could not be read directly",
                reason="messages_permission_denied",
                next_action=_DB_DENIED_NEXT_ACTION,
            ) from exc
        except sqlite3.Error as exc:
            raise MessagesUnavailable(
                "Messages database could not be read",
                reason="messages_unavailable",
                next_action="Verify Messages is synced on this Mac and rerun 'agent-autofill messages-test'",
            ) from exc
        finally:
            db.close()

    def _dispatch(self, ax_fn, db_fn):
        if self.backend == "ax":
            return ax_fn()
        if self.backend == "db":
            return db_fn()
        # Default "auto": prefer zero-FDA faster-mac-use AX; fall back to chat.db if readable.
        try:
            return ax_fn()
        except MessagesUnavailable as ax_exc:
            try:
                return db_fn()
            except MessagesUnavailable:
                raise ax_exc from None

    def check_connection(self) -> dict[str, str]:
        """Verify read-only Mac Messages access without returning message content."""

        return self._dispatch(self._ax_check_connection, self._db_check_connection)

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

    def _ax_recent(
        self,
        max_age_seconds: int = 300,
        conversation_scope: str = "*",
        sender: str | None = None,
        limit: int = 100,
    ) -> list[TextMessage]:
        now = datetime.now(UTC)
        # Allow a 60s cushion for minute-granularity sidebar timestamps.
        effective_max_age = max(60, int(max_age_seconds) + 60)
        bounded = max(1, min(int(limit), 200))
        results: list[TextMessage] = []
        for msg in self._ax_messages(now=now):
            if _is_ax_tapback_body(msg.body):
                continue
            if conversation_scope != "*" and msg.conversation_id != conversation_scope:
                continue
            if sender and not _sender_matches_filter(msg.sender, sender):
                continue
            if not msg.sent_at:
                continue
            try:
                sent_at = datetime.fromisoformat(msg.sent_at)
            except ValueError:
                continue
            age = (now - sent_at).total_seconds()
            if age < -60 or age > effective_max_age:
                continue
            results.append(msg)
            if len(results) >= bounded:
                break
        return results

    def _db_recent(
        self,
        max_age_seconds: int = 300,
        conversation_scope: str = "*",
        sender: str | None = None,
        limit: int = 100,
    ) -> list[TextMessage]:
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

    def recent(
        self,
        max_age_seconds: int = 300,
        conversation_scope: str = "*",
        sender: str | None = None,
        limit: int = 100,
    ) -> list[TextMessage]:
        """Return recent incoming text messages within the requested age window."""

        return self._dispatch(
            lambda: self._ax_recent(max_age_seconds, conversation_scope, sender, limit),
            lambda: self._db_recent(max_age_seconds, conversation_scope, sender, limit),
        )

    def _ax_search(
        self, query: str, conversation_scope: str = "*", limit: int = 20
    ) -> list[TextMessage]:
        needle = (query or "").lower()
        matches: list[TextMessage] = []
        for msg in self._ax_messages():
            if conversation_scope != "*" and msg.conversation_id != conversation_scope:
                continue
            if needle in (msg.body + " " + msg.sender).lower():
                matches.append(msg)
                if len(matches) >= limit:
                    break
        return matches

    def _db_search(
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

    def search(
        self, query: str, conversation_scope: str = "*", limit: int = 20
    ) -> list[TextMessage]:
        return self._dispatch(
            lambda: self._ax_search(query, conversation_scope, limit),
            lambda: self._db_search(query, conversation_scope, limit),
        )

    def _ax_get(self, message_id: str) -> TextMessage:
        for msg in self._ax_messages():
            if msg.message_id == message_id:
                return msg
        raise KeyError(message_id)

    def _db_get(self, message_id: str) -> TextMessage:
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

    def get(self, message_id: str) -> TextMessage:
        if isinstance(message_id, str) and message_id.startswith("ax:"):
            return self._ax_get(message_id)
        return self._dispatch(
            lambda: self._ax_get(message_id),
            lambda: self._db_get(message_id),
        )

    def _ax_list_conversations(self) -> list[dict]:
        seen: set[str] = set()
        conversations: list[dict] = []
        for msg in self._ax_messages():
            if msg.conversation_id in seen:
                continue
            seen.add(msg.conversation_id)
            conversations.append(
                {
                    "conversation_id": msg.conversation_id,
                    "title": msg.sender,
                    "source": "mac-messages-ax",
                }
            )
        return conversations

    def _db_list_conversations(self) -> list[dict]:
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

    def list_conversations(self) -> list[dict]:
        return self._dispatch(
            self._ax_list_conversations,
            self._db_list_conversations,
        )
