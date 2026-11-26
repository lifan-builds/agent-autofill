# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 lifan-builds. Agent Autofill; Section 7 attribution terms in ATTRIBUTION.md.
from datetime import UTC, datetime

from agent_autofill.imap_adapter import IMAPEmailAdapter


def test_recent_includes_previous_mailbox_day_without_accepting_old_codes(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, _tz=None):
            return cls(2026, 10, 22, 0, 30, tzinfo=UTC)

    fresh_date = b"Thu, 22 Oct 2026 00:29:00 +0000"
    old_date = b"Wed, 21 Oct 2026 23:00:00 +0000"
    fixtures = {
        b"1": (fresh_date, b'INTERNALDATE "21-Oct-2026 16:00:00 -0700" ', b")"),
        b"2": (old_date, b"", b' INTERNALDATE "21-Oct-2026 17:29:30 -0700")'),
        b"3": (fresh_date, b"", b")"),
        b"4": (fresh_date, b'INTERNALDATE "22-Oct-2026 00:29:00 GMT" ', b")"),
        b"5": (old_date, b'INTERNALDATE "22-Oct-2026 00:29:45 -0000" ', b")"),
    }

    class Connection:
        logged_out = False

        def uid(self, command, *args):
            if command == "search":
                assert args == ("SINCE", "21-Oct-2026")
                return "OK", [b"1 2 3 4 5"]
            uid, fields = args
            assert fields == "(INTERNALDATE RFC822)"
            header_date, prefix, tail = fixtures[uid]
            raw = b"Date: " + header_date + b"\r\nSubject: Example message\r\n\r\nContent\r\n"
            metadata = uid + b" (" + prefix + f"RFC822 {{{len(raw)}}}".encode()
            return "OK", [(metadata, raw), tail]

        def logout(self):
            self.logged_out = True

    connection = Connection()
    adapter = IMAPEmailAdapter("synthetic", "imap.example.test", "test@example.test")
    monkeypatch.setattr("agent_autofill.imap_adapter.datetime", Clock)
    monkeypatch.setattr(IMAPEmailAdapter, "_connect", lambda self: connection)

    assert [(message.message_id, message.received_at) for message in adapter.recent(300)] == [
        ("5", "2026-10-22T00:29:45+00:00"),
        ("3", "2026-10-22T00:29:00+00:00"),
        ("2", "2026-10-22T00:29:30+00:00"),
    ]
    assert connection.logged_out
