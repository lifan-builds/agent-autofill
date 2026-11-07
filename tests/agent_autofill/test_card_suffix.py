"""Synthetic-only regressions for choosing a saved checkout card."""

import json

import pytest

from agent_autofill import cli
from agent_autofill.server import _call
from agent_autofill.service import Broker, BrokerError
from agent_autofill.storage import Store


@pytest.fixture
def wallet():
    broker = Broker(Store(":memory:", key=b"s" * 32))
    for record_id, label, number, year in (
        ("business-old", "Business Platinum", "370000000041234", "2030"),
        ("business-current", "Business Platinum", "370000000051234", "2031"),
        ("personal-current", "Personal Platinum", "370000000051234", "2031"),
    ):
        broker.put_financial_record(record_id, "payment_card", label, {
            "card_number": number, "expiration_month": "05",
            "expiration_year": year, "security_code": "9876",
        })
    yield broker
    broker.store.db.close()


def test_five_digit_suffix_combines_name_and_expiry_without_exposing_values(wallet):
    records = wallet.match_financial_records(
        card_suffix="51234", query="business",
        matching_hints={"expiration_month": "5", "expiration_year": "2031"},
    )
    assert [r["record_id"] for r in records] == ["business-current"]
    assert records[0]["matching_hints"]["last4"] == "1234"
    output = json.dumps(records) + str(wallet.store.audits())
    for secret in ("370000000051234", "51234", "9876", '"values"'):
        assert secret not in output


def test_short_suffix_remains_ambiguous_and_conflicting_hints_do_not_match(wallet):
    assert len(wallet.match_financial_records(card_suffix="1234")) == 3
    assert wallet.match_financial_records(
        card_suffix="51234", matching_hints={"last4": "9999"}
    ) == []
    assert wallet.match_financial_records(card_suffix="61234") == []


@pytest.mark.parametrize("suffix", ["123", "123456", "370000000051234", "１２３４", 1234, ""])
def test_suffix_validation_does_not_echo_input(wallet, suffix):
    with pytest.raises(BrokerError, match="card_suffix must contain four or five digits") as error:
        wallet.match_financial_records(card_suffix=suffix)
    if suffix:
        assert str(suffix) not in str(error.value)


def test_suffix_only_matches_payment_cards_with_a_full_stored_number(wallet):
    wallet.put_financial_record("masked", "payment_card", "Masked only", {"card_number": "*****51234"})
    assert len(wallet.match_financial_records(card_suffix="51234")) == 2
    with pytest.raises(BrokerError, match="only supported for payment cards"):
        wallet.match_financial_records(kind="custom_financial", card_suffix="1234")


def test_cli_combines_query_with_kind_and_suffix(wallet, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_broker", lambda _path: wallet)
    cli.main(["financial-records", "--kind", "payment_card", "--query", "business",
              "--card-suffix", "51234", "--expiration-year", "2031"])
    records = json.loads(capsys.readouterr().out)
    assert [r["record_id"] for r in records] == ["business-current"]
    cli.main(["financial-records", "--kind", "payment_card", "--query", "personal"])
    assert [r["record_id"] for r in json.loads(capsys.readouterr().out)] == ["personal-current"]


def test_mcp_suffix_match_requires_authentication(wallet, monkeypatch):
    monkeypatch.delenv("AGENT_AUTOFILL_AGENT_TOKEN", raising=False)
    with pytest.raises(BrokerError, match="authentication failed"):
        _call(wallet, "match_financial_records", {"card_suffix": "51234"})
    agent = wallet.create_agent("suffix-test")
    result = _call(wallet, "match_financial_records", {
        "agent_id": "suffix-test", "agent_token": agent,
        "card_suffix": "51234", "query": "business",
    })
    assert [r["record_id"] for r in result] == ["business-current"]
