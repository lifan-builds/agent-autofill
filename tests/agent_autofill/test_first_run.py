"""Fixture-backed setup to persisted JSON-lines server integration."""

import hashlib
import io
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agent_autofill import cli, server
from agent_autofill.models import EmailMessage
from agent_autofill.service import Broker
from agent_autofill.storage import Store


FIELD_ID = "autofill:" + hashlib.sha256(b"First name").hexdigest()[:20]
ORIGIN = "https://example.com"
PROFILE_VALUE = "Synthetic first-run person"
MAILBOX_SECRET = "synthetic-first-run-mailbox-secret"
OTP = "482917"


class FixtureKeyring:
    def __init__(self):
        self.keys = {}
        self.secrets = {}

    def load_or_create(self, name):
        return self.keys.setdefault(name, b"f" * 32)

    def set_secret(self, name, value):
        self.secrets[name] = value

    def get_secret(self, name):
        return self.secrets.get(name)

    def delete_secret(self, name):
        self.secrets.pop(name, None)


@pytest.fixture
def first_run(tmp_path, monkeypatch, capsys):
    profile = tmp_path / "Default"
    profile.mkdir()
    for filename, schema, statement, values in [
        (
            "Web Data",
            "create table autofill (name text, value text, count integer)",
            "insert into autofill values (?, ?, 1)",
            ("First name", PROFILE_VALUE),
        ),
        (
            "Login Data",
            "create table logins (origin_url text, username_value text, password_value blob)",
            "insert into logins values (?, ?, ?)",
            (ORIGIN, "fixture-user", b"synthetic-encrypted-blob"),
        ),
    ]:
        db = sqlite3.connect(profile / filename)
        try:
            db.execute(schema)
            db.execute(statement, values)
            db.commit()
        finally:
            db.close()

    keyring = FixtureKeyring()
    now = datetime.now(timezone.utc).isoformat()
    state = SimpleNamespace(
        path=tmp_path / "broker.sqlite3",
        keyring=keyring,
        instances=[],
        checks=[],
        searches=[],
        recent_calls=[],
        messages=[
            EmailMessage(
                "welcome",
                "personal",
                "hello@example.com",
                "Welcome",
                "Your fixture account is ready.",
                now,
            ),
            EmailMessage(
                "login",
                "personal",
                "login@example.com",
                "Sign in",
                f"Your code is {OTP}",
                now,
            ),
        ],
    )

    class FixtureIMAP:
        def __init__(self, account_id, host, username, password, port, mailbox):
            assert (account_id, host, username, password, port, mailbox) == (
                "personal",
                "imap.example.test",
                "fixture@example.test",
                MAILBOX_SECRET,
                993,
                "INBOX",
            )
            self.instance_id = len(state.instances)
            state.instances.append(self)

        def check_connection(self):
            state.checks.append(self.instance_id)

        def search(self, query, limit):
            state.searches.append((self.instance_id, query, limit))
            return [m for m in state.messages if query.lower() in m.subject.lower()][
                :limit
            ]

        def recent(self, max_age_seconds):
            state.recent_calls.append((self.instance_id, max_age_seconds))
            return list(state.messages)

    for module in (cli, server):
        monkeypatch.setattr(module, "KeyringProvider", lambda: keyring)
        monkeypatch.setattr(module, "IMAPEmailAdapter", FixtureIMAP)
    monkeypatch.delenv("AGENT_AUTOFILL_AGENT_TOKEN", raising=False)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: MAILBOX_SECRET)
    # Exact prompt order is part of the wizard's first-run path.
    answers = iter(
        [
            "fixture-agent",
            "",
            "y",
            "personal",
            "imap.example.test",
            "fixture@example.test",
            "n",
            "y",
            FIELD_ID,
            "personal",
            "y",
            "y",
            ORIGIN,
            "login",
            "y",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    assert not state.path.exists()
    assert (
        cli.main(["--db", str(state.path), "setup", "--profile-dir", str(profile)]) == 0
    )
    assert next(answers, None) is None
    output = capsys.readouterr()
    assert not output.err
    lines = output.out.splitlines()
    state.token = lines[
        lines.index(
            "Agent fixture-agent token (save once; it will not be shown again):"
        )
        + 1
    ]
    assert output.out.count(state.token) == 1
    for secret in (MAILBOX_SECRET, PROFILE_VALUE, OTP):
        assert secret not in output.out
    assert '"autofill_fields": 1' in output.out
    assert '"login_metadata": 1' in output.out
    assert '"passwords": 0' in output.out
    assert len(state.instances) == 1
    assert state.checks == [0]
    assert list(keyring.keys) == [str(state.path)]
    yield state


def _request(state, name, **arguments):
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": {
                "agent_id": "fixture-agent",
                "agent_token": state.token,
                **arguments,
            },
        },
    }


def _serve(state, monkeypatch, capsys, *requests):
    monkeypatch.setattr(
        server.sys,
        "stdin",
        io.StringIO("\n".join(json.dumps(request) for request in requests) + "\n"),
    )
    server.main(["--db", str(state.path)])
    captured = capsys.readouterr()
    assert not captured.err
    assert MAILBOX_SECRET not in captured.out
    assert state.token not in captured.out
    return [json.loads(line) for line in captured.out.splitlines()]


def _result(response):
    assert "error" not in response
    assert response["result"]["isError"] is False
    return json.loads(response["result"]["content"][0]["text"])


def test_setup_to_json_lines_reads_and_persisted_otp_consumption(
    first_run, monkeypatch, capsys
):
    state = first_run
    profile = _request(
        state,
        "request_profile_fields",
        field_ids=[FIELD_ID],
        target_origin=ORIGIN,
        purpose="login",
    )
    search = _request(state, "search_email", account_id="personal", query="Welcome")
    otp = _request(
        state,
        "request_auth_code",
        account_id="personal",
        target_origin=ORIGIN,
        purpose="login",
    )
    responses = _serve(
        state,
        monkeypatch,
        capsys,
        {"jsonrpc": "2.0", "id": 0, "method": "initialize"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        profile,
        search,
        otp,
        otp,
    )
    assert len(responses) == 5
    assert responses[0]["result"]["serverInfo"]["name"] == "agent-autofill"
    assert _result(responses[1])[FIELD_ID]["value"] == PROFILE_VALUE
    assert _result(responses[2])[0]["provenance"] == {
        "account_id": "personal",
        "message_id": "welcome",
    }
    assert _result(responses[3]) == {
        "code": OTP,
        "provenance": {
            "source": "email",
            "account_id": "personal",
            "message_id": "login",
            "received_at": state.messages[1].received_at,
        },
    }
    assert responses[4]["error"]["message"] == "authentication code already consumed"
    assert OTP not in json.dumps(responses[:3] + responses[4:])
    assert state.searches == [(1, "Welcome", 20)]
    assert state.recent_calls == [(1, 300), (1, 300)]
    replay = _serve(state, monkeypatch, capsys, otp)[0]
    assert replay["error"]["message"] == "authentication code already consumed"
    assert state.recent_calls[-1] == (2, 300)

    store = Store(state.path, key_provider=state.keyring, key_name=str(state.path))
    try:
        grants = Broker(store).list_grants()
        assert len(grants) == 3
        assert {
            (
                grant["operation"],
                grant["resource_scope"],
                grant["target_origin"],
                grant["purpose"],
            )
            for grant in grants
        } == {
            ("profile.read", FIELD_ID, ORIGIN, "login"),
            ("email.search", "personal", None, None),
            ("email.otp", "personal", ORIGIN, "login"),
        }
        assert {
            datetime.fromisoformat(grant["expires_at"])
            - datetime.fromisoformat(grant["issued_at"])
            for grant in grants
        } == {timedelta(seconds=600)}
        audits = store.audits()
        assert {row["operation"] for row in audits} == {
            "profile.read",
            "email.search",
            "email.otp",
        }
        metadata = [
            store.get(row["namespace"], row["key"])
            for row in store.db.execute(
                "select namespace, key from kv where namespace != 'profile'"
            )
        ]
        for secret in (OTP, MAILBOX_SECRET, state.token, PROFILE_VALUE):
            assert secret not in json.dumps(audits)
            assert secret not in json.dumps(metadata)
        raw = " ".join(str(tuple(row)) for row in store.db.execute("select * from kv"))
        for secret in (OTP, MAILBOX_SECRET, state.token, PROFILE_VALUE):
            assert secret not in raw
    finally:
        store.db.close()


@pytest.mark.parametrize(
    "name, arguments, error_type",
    [
        (
            "request_profile_fields",
            {
                "field_ids": [FIELD_ID],
                "target_origin": ORIGIN,
                "purpose": "login",
                "agent_token": "wrong",
            },
            "BrokerError",
        ),
        (
            "request_profile_fields",
            {"field_ids": ["ungranted"], "target_origin": ORIGIN, "purpose": "login"},
            "AuthorizationError",
        ),
        (
            "request_profile_fields",
            {
                "field_ids": [FIELD_ID],
                "target_origin": "https://other.test",
                "purpose": "login",
            },
            "AuthorizationError",
        ),
        (
            "request_profile_fields",
            {"field_ids": [FIELD_ID], "target_origin": ORIGIN, "purpose": "other"},
            "AuthorizationError",
        ),
        (
            "search_email",
            {"account_id": "other", "query": "Welcome"},
            "AuthorizationError",
        ),
        (
            "request_auth_code",
            {"account_id": "other", "target_origin": ORIGIN, "purpose": "login"},
            "AuthorizationError",
        ),
        (
            "request_auth_code",
            {
                "account_id": "personal",
                "target_origin": "https://other.test",
                "purpose": "login",
            },
            "AuthorizationError",
        ),
        (
            "request_auth_code",
            {"account_id": "personal", "target_origin": ORIGIN, "purpose": "other"},
            "AuthorizationError",
        ),
    ],
)
def test_first_run_refuses_unapproved_requests(
    first_run, monkeypatch, capsys, name, arguments, error_type
):
    response = _serve(
        first_run, monkeypatch, capsys, _request(first_run, name, **arguments)
    )[0]
    assert response["error"]["type"] == error_type
    assert OTP not in json.dumps(response)
    assert not first_run.searches
    assert not first_run.recent_calls


@pytest.mark.parametrize("mode", ["expired", "revoked", "stale", "ambiguous"])
def test_first_run_otp_refusals(first_run, monkeypatch, capsys, mode):
    state = first_run
    if mode in {"expired", "revoked"}:
        store = Store(state.path, key_provider=state.keyring, key_name=str(state.path))
        try:
            if mode == "revoked":
                Broker(store).revoke_agent("fixture-agent")
            else:
                for grant in Broker(store).list_grants():
                    key = grant.pop("grant_id")
                    grant.pop("active")
                    grant["expires_at"] = (
                        datetime.now(timezone.utc) - timedelta(seconds=1)
                    ).isoformat()
                    store.put("grant", key, grant)
        finally:
            store.db.close()
    elif mode == "stale":
        state.messages[1] = replace(
            state.messages[1],
            received_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        )
    else:
        state.messages.append(replace(state.messages[1], message_id="another-code"))
    request = _request(
        state,
        "request_auth_code",
        account_id="personal",
        target_origin=ORIGIN,
        purpose="login",
    )
    response = _serve(state, monkeypatch, capsys, request)[0]
    expected = "AuthorizationError" if mode == "expired" else "BrokerError"
    assert response["error"]["type"] == expected
    assert OTP not in json.dumps(response)
