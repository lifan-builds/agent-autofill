from datetime import UTC, datetime

from agent_autofill.imap_adapter import IMAPEmailAdapter
from agent_autofill.models import EmailMessage


def test_recent_includes_previous_mailbox_day_without_accepting_old_codes(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, _tz=None):
            return cls(2026, 10, 22, 0, 30, tzinfo=UTC)

    class Connection:
        logged_out = False

        def uid(self, command, *args):
            if command == "search":
                # A new message has INTERNALDATE Oct 21 in a western timezone.
                return "OK", [b"old fresh" if args[-1] <= "21-Oct-2026" else b""]
            return "OK", [(None, args[0])]

        def logout(self):
            self.logged_out = True

    connection = Connection()
    adapter = IMAPEmailAdapter("synthetic", "imap.example.test", "test@example.test")
    monkeypatch.setattr("agent_autofill.imap_adapter.datetime", Clock)
    monkeypatch.setattr(IMAPEmailAdapter, "_connect", lambda self: connection)
    monkeypatch.setattr(IMAPEmailAdapter, "_parse", lambda self, uid, raw: EmailMessage(
        uid.decode(), "synthetic", "no-reply@auth.openai.com", "Login code",
        "Your code is 123456",
        "2026-10-22T00:29:30+00:00" if uid == b"fresh" else "2026-10-21T23:00:00+00:00"))

    assert [message.message_id for message in adapter.recent(300)] == ["fresh"]
    assert connection.logged_out
