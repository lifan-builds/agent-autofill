from datetime import datetime

import pytest

from agent_autofill import cli
from agent_autofill.imap_adapter import IMAPAuthenticationError
from agent_autofill.service import Broker
from agent_autofill.storage import Store


class MemoryKeyring:
    def __init__(self):
        self.values = {}
        self.deleted = []

    def set_secret(self, name, value):
        self.values[name] = value

    def get_secret(self, name):
        return self.values.get(name)

    def delete_secret(self, name):
        self.deleted.append(name)
        self.values.pop(name, None)


class FakeAdapter:
    instances: list["FakeAdapter"] = []
    check_error = None

    def __init__(self, *args):
        self.args = args
        self.__class__.instances.append(self)

    def check_connection(self):
        if self.__class__.check_error:
            raise self.__class__.check_error


class FakeOAuthProvider:
    def __init__(self):
        self.access_token = "access-token-fixture"

    def get_access_token(self):
        return self.access_token

    def to_keychain_secret(self):
        return (
            b'{"version":1,"provider":"google","client_id":"client-id",'
            b'"refresh_token":"refresh-token-fixture"}'
        )


class ExtraFieldOAuthProvider(FakeOAuthProvider):
    def to_keychain_secret(self):
        return (
            b'{"version":1,"provider":"google","client_id":"client-id",'
            b'"refresh_token":"refresh-token-fixture",'
            b'"access_token":"access-token-fixture"}'
        )


def _broker():
    return Broker(Store(":memory:", key=b"c" * 32))


def test_cli_uses_agent_autofill_name_and_database_default():
    parser = cli._parser()

    assert parser.prog == "agent-autofill"
    assert parser.parse_args(["init"]).db == "agent-autofill.sqlite3"


def test_cli_progressive_onboarding_aliases_and_safe_views_are_available():
    parser = cli._parser()

    assert parser.parse_args(["start"]).command == "start"
    assert parser.parse_args(["onboard"]).command == "onboard"
    assert parser.parse_args(["demo", "--json"]).json is True
    assert parser.parse_args(["data", "--json"]).command == "data"
    assert parser.parse_args(["access", "--json"]).command == "access"


def test_setup_profile_selection_requires_an_explicit_choice(monkeypatch, tmp_path):
    profiles = [tmp_path / "Default", tmp_path / "Profile 1"]
    monkeypatch.setattr(cli, "discover_profiles", lambda *_args: profiles)
    monkeypatch.setattr("builtins.input", lambda _prompt: "2")

    assert cli._select_setup_profile(None) == str(profiles[1])


def test_setup_profile_selection_accepts_explicit_path_and_skips_discovery(monkeypatch):
    monkeypatch.setattr(
        cli,
        "discover_profiles",
        lambda *_args: pytest.fail("explicit profile should not discover profiles"),
    )

    assert (
        cli._select_setup_profile("/tmp/selected-chrome-profile")
        == "/tmp/selected-chrome-profile"
    )


def test_demo_is_memory_only_and_does_not_open_the_user_broker(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "_broker",
        lambda _path: pytest.fail("demo must not open the user's broker"),
    )

    assert cli.main(["demo", "--json"]) == 0

    output = capsys.readouterr().out
    assert '"synthetic": true' in output
    assert '"database": "memory-only"' in output
    assert "synthetic-password" not in output
    assert "demo@example.test" not in output


def test_start_keychain_block_has_a_fix_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "_broker",
        lambda _path: (_ for _ in ()).throw(
            RuntimeError("OS credential store unavailable")
        ),
    )

    assert cli.main(["start"]) == 0

    output = capsys.readouterr().out
    assert "Agent Autofill start: needs-attention" in output
    assert "Fix: Unlock the login Keychain" in output


def test_cli_exposes_read_only_quickstart_doctor_and_interactive_profile_paths():
    parser = cli._parser()
    assert parser.parse_args(["quickstart"]).command == "quickstart"
    assert parser.parse_args(["status"]).command == "status"
    assert parser.parse_args(["doctor", "--json"]).command == "doctor"
    parsed = parser.parse_args(["profile", "add", "legal_name"])
    assert parsed.profile_command == "add"
    assert parser.parse_args(["profile", "edit", "legal_name"]).profile_command == "edit"


def test_doctor_reports_keychain_startup_block_without_traceback(monkeypatch, capsys):
    def fail_open(_path):
        raise RuntimeError(
            "OS credential store unavailable: unlock the macOS login Keychain "
            "and run Agent Autofill in a host process with Keychain access"
        )

    monkeypatch.setattr(cli, "_broker", fail_open)

    assert cli.main(["doctor", "--json"]) == 0

    output = capsys.readouterr().out
    assert "needs-attention" in output
    assert "host process with Keychain access" in output
    assert "Traceback" not in output


def test_interactive_profile_add_hides_value_and_writes_after_metadata_confirmation(
    monkeypatch, capsys
):
    broker = _broker()
    monkeypatch.setattr(cli, "_broker", lambda _path: broker)
    answers = iter(["", "", "", "", "", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "private-profile-value")

    assert cli.main(["--db", "unused.sqlite", "profile", "add", "legal_name"]) == 0

    output = capsys.readouterr().out
    assert "private-profile-value" not in output
    assert broker.store.get("profile", "legal_name")["value"] == "private-profile-value"


def test_cli_exposes_service_pairing_and_bound_server_modes():
    parser = cli._parser()
    assert parser.parse_args(["connect", "--service", "cpa-phoenix"]).service == "cpa-phoenix"
    parsed = parser.parse_args(["serve", "--service", "cpa-phoenix"])
    assert parsed.service == "cpa-phoenix"
    parsed = parser.parse_args(["grant-email", "service:cpa-phoenix", "email.otp", "--all"])
    assert parsed.all is True and parsed.recipient is None
    parsed = parser.parse_args(["grant-email", "service:cpa-phoenix", "email.otp", "all"])
    assert parsed.all is False and parsed.recipient == "all"
    parsed = parser.parse_args(
        [
            "grant-email",
            "service:cpa-phoenix",
            "email.verification_link",
            "owner@example.test",
            "--origin",
            "https://auth.example.test",
            "--purpose",
            "verify",
        ]
    )
    assert parsed.operation == "email.verification_link"
    parsed = parser.parse_args(["email-match", "owner@example.test"])
    assert parsed.command == "email-match"
    assert parsed.recipient == "owner@example.test"


def test_email_match_cli_prints_only_bounded_metadata(monkeypatch, capsys):
    broker = _broker()
    broker.match_email_sources = lambda recipient: [
        {
            "source_id": "opaque-mailbox",
            "provider": "imap",
            "label": "Personal mailbox",
            "kind": "email",
        }
    ]
    monkeypatch.setattr(cli, "_broker", lambda _path: broker)

    assert (
        cli.main(
            ["--db", "unused.sqlite", "email-match", "owner@example.test"]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "Personal mailbox" in output
    assert "owner@example.test" not in output
    assert "verification link" in output


def test_email_otp_grant_prints_scoped_code_next_step_without_code(
    monkeypatch, capsys
):
    broker = _broker()
    broker.create_agent("mail-agent")
    broker.add_source("opaque-mailbox", "email", "Personal mailbox")
    broker.store.put(
        "connection",
        "opaque-mailbox",
        {
            "source_id": "opaque-mailbox",
            "kind": "imap",
            "username": "owner@example.test",
        },
    )
    monkeypatch.setattr(cli, "_broker", lambda _path: broker)

    assert (
        cli.main(
            [
                "--db",
                "unused.sqlite",
                "grant-email",
                "mail-agent",
                "email.otp",
                "owner@example.test",
                "--origin",
                "https://auth.example.test",
                "--purpose",
                "account recovery",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "Consent required before requesting an authentication code" in output
    assert "request_auth_code_for_recipient" in output
    assert "request_verification_link_for_recipient" in output
    assert "verification-url-fixture" not in output


def test_cli_accepts_safe_financial_match_hints():
    parsed = cli._parser().parse_args(
        [
            "financial-records",
            "--kind",
            "payment_card",
            "--network",
            "amex",
            "--last4",
            "1007",
            "--expiration-month",
            "05",
            "--expiration-year",
            "2031",
        ]
    )
    assert parsed.kind == "payment_card"
    assert parsed.expiration_year == "2031"


def test_record_and_financial_commands_are_available():
    assert cli._parser().parse_args(["financial-records", "list"]).command == (
        "financial-records"
    )
    parsed = cli._parser().parse_args(["record-delete", "financial", "primary-card"])
    assert parsed.record_type == "financial"
    assert parsed.record_id == "primary-card"


def test_keychain_migration_command_is_available():
    parsed = cli._parser().parse_args(["keychain", "migrate"])
    assert parsed.command == "keychain"
    assert parsed.action == "migrate"


def test_payment_card_add_command_never_accepts_secret_as_an_argument():
    parsed = cli._parser().parse_args(
        [
            "payment-card-add",
            "platinum-card",
            "Travel Platinum",
            "--network",
            "Amex",
            "--expiration-month",
            "05",
            "--expiration-year",
            "2031",
        ]
    )
    assert parsed.record_id == "platinum-card"
    assert parsed.expiration_year == "2031"
    with pytest.raises(SystemExit):
        cli._parser().parse_args(
            [
                "payment-card-add",
                "platinum-card",
                "Travel Platinum",
                "--security-code",
                "123",
            ]
        )


def test_payment_card_add_reviews_masked_metadata_before_encrypted_write(
    monkeypatch, capsys
):
    broker = _broker()
    monkeypatch.setattr(cli, "_broker", lambda _path: broker)
    monkeypatch.setattr(
        cli.getpass,
        "getpass",
        lambda prompt: (
            "4111 1111 1111 1111"
            if prompt.startswith("Payment card")
            else "123"
            if prompt.startswith("Security code")
            else "719"
        ),
    )
    answers = iter(["y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert (
        cli.main(
            [
                "--db",
                "unused.sqlite",
                "payment-card-add",
                "platinum-card",
                "Travel Platinum",
                "--network",
                "Visa",
                "--expiration-month",
                "05",
                "--expiration-year",
                "2031",
                "--origin",
                "https://www.example.test",
                "--purpose",
                "hotel booking",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "4111111111111111" not in output
    assert '"last4": "1111"' in output
    assert '"security_code"' in output
    assert '"additional_security_code"' in output
    assert broker.list_financial_records()[0]["matching_hints"]["last4"] == "1111"
    stored = broker.store.get("financial", "platinum-card")
    assert stored["values"]["card_number"] == "4111111111111111"
    assert stored["values"]["security_code"] == "123"


def test_payment_card_add_surfaces_matching_existing_metadata(monkeypatch, capsys):
    broker = _broker()
    broker.put_payment_card(
        "existing-card",
        "Existing Amex",
        "371378000000007",
        "05",
        "2031",
        security_code="1234",
        network="amex",
    )
    monkeypatch.setattr(cli, "_broker", lambda _path: broker)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: (
        "371378000000007"
        if prompt.startswith("Payment card")
        else "1234"
        if prompt.startswith("Security code")
        else ""
    ))
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    assert (
        cli.main(
            [
                "--db",
                "unused.sqlite",
                "payment-card-add",
                "new-card",
                "Another Amex",
                "--network",
                "amex",
                "--expiration-month",
                "05",
                "--expiration-year",
                "2031",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "Matching existing card metadata found" in output
    assert '"record_id": "existing-card"' in output
    assert "371378000000007" not in output


def test_payment_card_add_decline_does_not_write_or_echo_values(monkeypatch, capsys):
    broker = _broker()
    monkeypatch.setattr(cli, "_broker", lambda _path: broker)
    monkeypatch.setattr(
        cli.getpass,
        "getpass",
        lambda prompt: (
            "4111111111111111"
            if prompt.startswith("Payment card")
            else "123"
            if prompt.startswith("Security code")
            else "719"
        ),
    )
    answers = iter(["n"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cli.main(
        [
            "--db",
            "unused.sqlite",
            "payment-card-add",
            "platinum-card",
            "Travel Platinum",
            "--network",
            "Visa",
            "--expiration-month",
            "05",
            "--expiration-year",
            "2031",
        ]
    )
    output = capsys.readouterr().out
    assert "4111111111111111" not in output
    assert broker.list_financial_records() == []


def test_payment_card_add_rejects_secret_in_review_metadata_before_prompt(
    monkeypatch, capsys
):
    broker = _broker()
    monkeypatch.setattr(cli, "_broker", lambda _path: broker)

    with pytest.raises(SystemExit, match="raw financial number"):
        cli.main(
            [
                "--db",
                "unused.sqlite",
                "payment-card-add",
                "platinum-card",
                "4111111111111111",
            ]
        )
    assert "4111111111111111" not in capsys.readouterr().out
    assert broker.list_financial_records() == []


def test_record_delete_command_deletes_the_exact_record(monkeypatch, capsys):
    broker = _broker()
    broker.put_financial_record(
        "primary-card",
        "payment_card",
        "Primary card",
        {"card_number": "4111111111111111"},
    )
    monkeypatch.setattr(cli, "_broker", lambda _path: broker)

    assert (
        cli.main(
            ["--db", "unused.sqlite", "record-delete", "financial", "primary-card"]
        )
        == 0
    )
    assert broker.store.get("financial", "primary-card") is None
    assert "Deleted financial record primary-card" in capsys.readouterr().out


def test_configure_imap_checks_before_persisting_and_uses_versioned_key(monkeypatch):
    monkeypatch.setattr(cli, "IMAPEmailAdapter", FakeAdapter)
    FakeAdapter.instances = []
    FakeAdapter.check_error = None
    broker = _broker()
    keyring = MemoryKeyring()

    config = cli._configure_imap(
        broker,
        "/tmp/agent-autofill.sqlite",
        "personal",
        "imap.example.test",
        "user@example.test",
        993,
        "INBOX",
        "fixture-password",
        keyring=keyring,
    )

    assert len(FakeAdapter.instances) == 1
    assert config["auth_kind"] == "password"
    assert config["secret_name"].startswith("imap:v1:")
    assert keyring.values[config["secret_name"]] == b"fixture-password"
    assert broker.store.get("connection", "personal") == config
    assert broker.store.get("source", "personal")["kind"] == "email"
    raw = broker.store.db.execute("select value from kv").fetchall()
    assert "fixture-password" not in str(raw)


def test_configure_imap_replacement_cleans_old_secret_after_commit(monkeypatch):
    monkeypatch.setattr(cli, "IMAPEmailAdapter", FakeAdapter)
    FakeAdapter.check_error = None
    broker = _broker()
    keyring = MemoryKeyring()
    first = cli._configure_imap(
        broker,
        "/tmp/agent-autofill.sqlite",
        "personal",
        "imap.example.test",
        "user@example.test",
        993,
        "INBOX",
        "first-fixture-password",
        keyring=keyring,
    )
    second = cli._configure_imap(
        broker,
        "/tmp/agent-autofill.sqlite",
        "personal",
        "imap.other.example.test",
        "user@example.test",
        993,
        "INBOX",
        "second-fixture-password",
        keyring=keyring,
    )

    assert first["secret_name"] != second["secret_name"]
    assert first["secret_name"] in keyring.deleted
    assert first["secret_name"] not in keyring.values
    assert keyring.values[second["secret_name"]] == b"second-fixture-password"
    assert (
        broker.store.get("connection", "personal")["host"] == "imap.other.example.test"
    )


def test_configure_imap_failed_check_does_not_touch_saved_source(monkeypatch):
    monkeypatch.setattr(cli, "IMAPEmailAdapter", FakeAdapter)
    FakeAdapter.check_error = IMAPAuthenticationError("IMAP authentication failed")
    broker = _broker()
    broker.store.put(
        "connection",
        "personal",
        {"source_id": "personal", "kind": "imap", "secret_name": "old-name"},
    )
    keyring = MemoryKeyring()
    keyring.values["old-name"] = b"old-fixture-password"

    with pytest.raises(SystemExit, match="IMAP authentication failed"):
        cli._configure_imap(
            broker,
            "/tmp/agent-autofill.sqlite",
            "personal",
            "imap.example.test",
            "user@example.test",
            993,
            "INBOX",
            "new-fixture-password",
            keyring=keyring,
        )

    assert broker.store.get("connection", "personal")["secret_name"] == "old-name"
    assert keyring.values == {"old-name": b"old-fixture-password"}


def test_email_test_reconnects_saved_secret_without_returning_it(monkeypatch):
    monkeypatch.setattr(cli, "IMAPEmailAdapter", FakeAdapter)
    FakeAdapter.check_error = None
    broker = _broker()
    keyring = MemoryKeyring()
    config = cli._configure_imap(
        broker,
        "/tmp/agent-autofill.sqlite",
        "personal",
        "imap.example.test",
        "user@example.test",
        993,
        "INBOX",
        "fixture-password",
        keyring=keyring,
    )

    tested = cli._test_saved_imap(broker, "personal", keyring)

    assert tested == config
    assert "fixture-password" not in str(tested)


def test_configure_gmail_verifies_before_saving_refresh_state(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "IMAPEmailAdapter", FakeAdapter)
    FakeAdapter.instances = []
    FakeAdapter.check_error = None
    client_path = tmp_path / "desktop-client.json"
    client_path.write_text(
        '{"installed":{"client_id":"client-id","client_secret":"client-secret"}}'
    )
    broker = _broker()
    keyring = MemoryKeyring()
    provider = FakeOAuthProvider()

    config = cli._configure_gmail(
        broker,
        str(tmp_path / "broker.sqlite"),
        "personal",
        "user@gmail.test",
        client_path,
        keyring=keyring,
        oauth_provider=provider,
    )

    assert config["auth_kind"] == "google_oauth"
    assert config["host"] == "imap.gmail.com"
    assert len(FakeAdapter.instances) == 1
    assert FakeAdapter.instances[0].args[-1] is provider
    saved = keyring.values[config["secret_name"]]
    assert b"refresh-token-fixture" in saved
    assert b"access-token-fixture" not in saved
    assert broker.store.get("connection", "personal") == config


def test_configure_gmail_authorization_failure_does_not_save(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "IMAPEmailAdapter", FakeAdapter)
    FakeAdapter.instances = []
    FakeAdapter.check_error = None
    client_path = tmp_path / "desktop-client.json"
    client_path.write_text('{"installed":{"client_id":"client-id"}}')
    broker = _broker()
    keyring = MemoryKeyring()

    def fail(_client):
        from agent_autofill.google_oauth import GoogleOAuthError

        raise GoogleOAuthError("access-token-fixture leaked by provider")

    with pytest.raises(SystemExit, match="Google OAuth setup failed") as caught:
        cli._configure_gmail(
            broker,
            str(tmp_path / "broker.sqlite"),
            "personal",
            "user@gmail.test",
            client_path,
            keyring=keyring,
            authorize_fn=fail,
        )

    assert "access-token-fixture" not in str(caught.value)
    assert broker.store.get("connection", "personal") is None
    assert keyring.values == {}


def test_oauth_keychain_serializer_is_validated_and_canonicalized():
    provider = FakeOAuthProvider()
    saved = cli._oauth_keychain_secret(provider)
    assert saved == (
        b'{"client_id":"client-id","provider":"google",'
        b'"refresh_token":"refresh-token-fixture","version":1}'
    )

    with pytest.raises(SystemExit, match="invalid refresh state"):
        cli._oauth_keychain_secret(ExtraFieldOAuthProvider())


def _setup_grant_broker():
    broker = _broker()
    broker.create_agent("setup-agent")
    broker.put_profile("first_name", "personal", "First name", "value-secret")
    broker.add_source("personal", "email", "Personal mailbox")
    return broker


def test_setup_grant_proposal_is_exact_and_value_free():
    broker = _setup_grant_broker()

    proposal = cli._setup_grant_proposal(
        broker,
        "setup-agent",
        ["first_name"],
        "personal",
        include_email_search=True,
        include_email_otp=True,
        target_origin="https://example.test",
        purpose="checkout",
    )

    assert proposal == [
        {
            "operation": "profile.read",
            "resource_scope": "first_name",
            "target_origin": "https://example.test",
            "purpose": "checkout",
            "expires_in_seconds": 600,
        },
        {
            "operation": "email.search",
            "resource_scope": "personal",
            "target_origin": None,
            "purpose": None,
            "expires_in_seconds": 600,
        },
        {
            "operation": "email.otp",
            "resource_scope": "personal",
            "target_origin": "https://example.test",
            "purpose": "checkout",
            "expires_in_seconds": 600,
        },
    ]
    assert "value-secret" not in str(proposal)
    assert all(item["resource_scope"] != "*" for item in proposal)


def test_setup_grants_preview_and_confirmation_persist_only_selected_scopes(
    monkeypatch, capsys
):
    broker = _setup_grant_broker()
    answers = iter(
        [
            "y",
            "first_name",
            "personal",
            "y",
            "y",
            "https://example.test",
            "checkout",
            "y",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    grants = cli._configure_setup_grants(broker, "setup-agent")

    assert len(grants) == 3
    persisted = broker.list_grants()
    assert sorted(item["operation"] for item in persisted) == sorted(
        ["profile.read", "email.search", "email.otp"]
    )
    for item in persisted:
        lifetime = (
            datetime.fromisoformat(item["expires_at"])
            - datetime.fromisoformat(item["issued_at"])
        ).total_seconds()
        assert 599 < lifetime <= 600
    output = capsys.readouterr().out
    assert "value-secret" not in output
    assert "Personal mailbox" not in output
    assert "account-wide" in output
    assert '"resource_scope": "first_name"' in output
    assert '"resource_scope": "personal"' in output


@pytest.mark.parametrize(
    "answers, expected",
    [
        (["n"], "unchanged"),
        (
            [
                "y",
                "first_name",
                "personal",
                "y",
                "y",
                "https://example.test",
                "checkout",
                "n",
            ],
            "cancelled",
        ),
    ],
)
def test_setup_grants_skip_or_cancel_leave_existing_grants_untouched(
    monkeypatch, capsys, answers, expected
):
    broker = _setup_grant_broker()
    broker.grant("setup-agent", "profile.read", "first_name", "https://old.test", "old")
    before = broker.list_grants()

    if expected == "cancelled":
        answers_iter = iter(answers)
        monkeypatch.setattr("builtins.input", lambda _prompt: next(answers_iter))
    else:
        monkeypatch.setattr("builtins.input", lambda _prompt: answers[0])
    cli._configure_setup_grants(broker, "setup-agent")

    assert broker.list_grants() == before
    assert "value-secret" not in capsys.readouterr().out


def test_setup_grants_validate_every_selection_before_writing(monkeypatch):
    broker = _setup_grant_broker()
    answers = iter(["y", "unknown-field"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    with pytest.raises(SystemExit, match="Unknown profile field ID"):
        cli._configure_setup_grants(broker, "setup-agent")

    assert broker.list_grants() == []


def test_setup_grants_reject_revoked_identity_without_reactivation():
    broker = _setup_grant_broker()
    broker.revoke_agent("setup-agent")

    with pytest.raises(SystemExit, match="identity is revoked"):
        cli._setup_grant_proposal(
            broker,
            "setup-agent",
            ["first_name"],
            "personal",
            include_email_search=True,
            target_origin="https://example.test",
            purpose="checkout",
        )

    assert broker.list_grants() == []
    assert broker.store.get("agent", "setup-agent")["revoked"] is True


def test_setup_blank_identity_does_not_offer_or_create_grants(monkeypatch):
    broker = _setup_grant_broker()
    monkeypatch.setattr(cli, "_broker", lambda _path: broker)
    monkeypatch.setattr(cli, "discover_profiles", lambda *_args: [])
    monkeypatch.setattr(
        cli,
        "_configure_setup_grants",
        lambda *_args: pytest.fail("blank identity must skip grant setup"),
    )
    answers = iter(["", "", "n", "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert cli.main(["--db", "unused.sqlite", "setup"]) == 0
    assert broker.list_grants() == []


def test_setup_existing_identity_token_is_not_rotated(monkeypatch, capsys):
    broker = _setup_grant_broker()
    before = broker.store.get("agent", "setup-agent")
    monkeypatch.setattr(cli, "_broker", lambda _path: broker)
    monkeypatch.setattr(cli, "discover_profiles", lambda *_args: [])
    answers = iter(["setup-agent", "", "n", "n", "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert cli.main(["--db", "unused.sqlite", "setup"]) == 0

    assert (
        broker.store.get("agent", "setup-agent")["token_hash"] == before["token_hash"]
    )
    assert broker.list_grants() == []
    assert "token (save once" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "selection, message",
    [
        ("personal,other", "exactly one email account"),
        ("missing-account", "Unknown email account"),
        ("*", "wildcard"),
    ],
)
def test_setup_rejects_multiple_or_unknown_email_accounts_before_writing(
    monkeypatch, selection, message
):
    broker = _setup_grant_broker()
    broker.add_source("other", "email", "Other mailbox")
    answers = iter(["y", "", selection])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    with pytest.raises(SystemExit, match=message):
        cli._configure_setup_grants(broker, "setup-agent")

    assert broker.list_grants() == []


@pytest.mark.parametrize(
    "answers, operation",
    [
        (["y", "", ""], None),
        (["y", "first_name", "", "example.test", "form", "y"], "profile.read"),
        (["y", "", "personal", "y", "n", "y"], "email.search"),
        (["y", "", "personal", "n", "y", "example.test", "login", "y"], "email.otp"),
    ],
)
def test_setup_grants_are_independently_optional(monkeypatch, answers, operation):
    broker = _setup_grant_broker()
    inputs = iter(answers)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))

    cli._configure_setup_grants(broker, "setup-agent")

    assert next(inputs, None) is None
    grants = broker.list_grants()
    assert [grant["operation"] for grant in grants] == (
        [operation] if operation else []
    )
    if operation == "email.search":
        assert grants[0]["target_origin"] is None
        assert grants[0]["purpose"] is None


@pytest.mark.parametrize(
    "proposal_args",
    [
        {
            "profile_field_ids": ["first_name"],
            "email_account_id": "personal",
            "target_origin": "",
            "purpose": "checkout",
        },
        {
            "profile_field_ids": [],
            "email_account_id": "personal",
            "include_email_otp": True,
            "target_origin": "https://example.test",
            "purpose": "",
        },
    ],
)
def test_setup_requires_origin_and_purpose_for_profile_or_otp_scopes(proposal_args):
    broker = _setup_grant_broker()

    with pytest.raises(SystemExit, match="required"):
        cli._setup_grant_proposal(broker, "setup-agent", **proposal_args)

    assert broker.list_grants() == []


def test_site_grant_preview_is_value_free_and_decline_is_non_mutating(
    monkeypatch, capsys
):
    broker = _broker()
    broker.create_agent("site-agent")
    broker.put_credential(
        "hilton-login",
        "Hilton",
        "https://www.hilton.com/login",
        "traveler@example.test",
        "synthetic-password",
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    assert (
        cli._configure_site_grants(
            broker,
            "site-agent",
            "https://www.hilton.com/reservation",
            "hotel booking",
        )
        == []
    )
    assert broker.list_grants() == []
    output = capsys.readouterr().out
    assert "traveler@example.test" in output
    assert "synthetic-password" not in output
    assert "cancelled" in output


def test_site_grant_confirmation_creates_short_lived_exact_scopes(monkeypatch):
    broker = _broker()
    broker.create_agent("site-agent")
    broker.put_credential(
        "hilton-login",
        "Hilton",
        "https://www.hilton.com/login",
        "traveler@example.test",
        "synthetic-password",
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    grants = cli._configure_site_grants(
        broker,
        "site-agent",
        "https://www.hilton.com/reservation",
        "hotel booking",
        expires_in=90,
    )

    assert len(grants) == 1
    assert grants[0].operation == "credential.use"
    assert grants[0].resource_scope == "hilton-login"
    assert grants[0].target_origin == "https://www.hilton.com/reservation"
    assert grants[0].purpose == "hotel booking"
    assert broker.list_grants()[0]["expires_at"]


def _site_grant_broker():
    broker = _broker()
    broker.create_agent("site-agent")
    broker.put_credential(
        "site-login",
        "Example",
        "https://example.test/login",
        "traveler@example.test",
        "synthetic-password",
    )
    return broker


@pytest.mark.parametrize(
    ("delivery", "operation", "next_step"),
    [
        ("direct", "credential.read", 'request_credential(delivery="direct")'),
        ("extension", "credential.use", 'request_credential(delivery="extension")'),
    ],
)
def test_site_grant_delivery_flags_create_only_the_mapped_operation(
    monkeypatch, capsys, delivery, operation, next_step
):
    broker = _site_grant_broker()
    monkeypatch.setattr(cli, "_broker", lambda _path: broker)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    assert (
        cli.main(
            [
                "--db",
                "unused.sqlite",
                "grant-site",
                "site-agent",
                "https://example.test/checkout",
                "checkout",
                "--delivery",
                delivery,
            ]
        )
        == 0
    )

    grants = broker.list_grants()
    assert len(grants) == 1
    assert grants[0]["operation"] == operation
    assert {item["operation"] for item in grants} == {operation}
    output = capsys.readouterr().out
    assert next_step in output
    assert "synthetic-password" not in output
    assert "handle" not in output or delivery == "extension"


@pytest.mark.parametrize(
    ("choice", "operation", "delivery"),
    [("1", "credential.read", "direct"), ("2", "credential.use", "extension")],
)
def test_site_grant_without_mode_prompts_before_metadata_confirmation(
    monkeypatch, capsys, choice, operation, delivery
):
    broker = _site_grant_broker()
    monkeypatch.setattr(cli, "_broker", lambda _path: broker)
    answers = iter([choice, "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert (
        cli.main(
            [
                "--db",
                "unused.sqlite",
                "grant-site",
                "site-agent",
                "https://example.test/checkout",
                "checkout",
            ]
        )
        == 0
    )

    grants = broker.list_grants()
    assert len(grants) == 1
    assert grants[0]["operation"] == operation
    output = capsys.readouterr().out
    assert f'request_credential(delivery="{delivery}")' in output
    if delivery == "direct":
        assert "selected credential fields" in output
    else:
        assert "password stays outside the agent response" in output


@pytest.mark.parametrize("choice", ["", "3", "unknown"])
def test_site_grant_empty_or_invalid_delivery_choice_creates_no_grants(
    monkeypatch, capsys, choice
):
    broker = _site_grant_broker()
    monkeypatch.setattr(cli, "_broker", lambda _path: broker)
    monkeypatch.setattr("builtins.input", lambda _prompt: choice)

    assert (
        cli.main(
            [
                "--db",
                "unused.sqlite",
                "grant-site",
                "site-agent",
                "https://example.test/checkout",
                "checkout",
            ]
        )
        == 0
    )

    assert broker.list_grants() == []
    output = capsys.readouterr().out
    assert "cancelled" in output
    assert "Proposed site credential grants" not in output


def test_site_grant_explicit_legacy_operation_skips_delivery_prompt(monkeypatch):
    broker = _site_grant_broker()
    monkeypatch.setattr(cli, "_broker", lambda _path: broker)
    answers = iter(["y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert (
        cli.main(
            [
                "--db",
                "unused.sqlite",
                "grant-site",
                "site-agent",
                "https://example.test/checkout",
                "checkout",
                "--operation",
                "credential.read",
            ]
        )
        == 0
    )

    assert [item["operation"] for item in broker.list_grants()] == [
        "credential.read"
    ]


def test_site_grant_delivery_and_legacy_operation_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        cli._parser().parse_args(
            [
                "grant-site",
                "site-agent",
                "https://example.test",
                "checkout",
                "--delivery",
                "direct",
                "--operation",
                "credential.read",
            ]
        )


def test_site_grant_parser_exposes_site_and_password_manager_commands():
    parsed = cli._parser().parse_args(
        ["grant-site", "site-agent", "https://example.test", "checkout"]
    )
    assert parsed.operation == "credential.use"
    assert parsed.delivery is None
    password_manager = cli._parser().parse_args(
        [
            "chrome",
            "password-manager",
            "--profile-dir",
            "/tmp/Chrome/Default",
            "--preview",
        ]
    )
    assert password_manager.preview is True
