import pytest

from agent_autofill.form_policy import (
    FormSubmissionPolicyError,
    get_form_submission_policy,
    set_form_submission_policy,
)
from agent_autofill.service import Broker
from agent_autofill.storage import Store


def _broker():
    return Broker(Store(":memory:", key=b"p" * 32))


def test_form_submission_defaults_to_confirmation_for_everything():
    policy = get_form_submission_policy(_broker())
    assert policy.confirmation == "always"
    assert policy.requires_confirmation(financial=False)
    assert policy.requires_confirmation(financial=True)


def test_non_financial_mode_is_configurable_but_financial_is_still_required():
    broker = _broker()
    policy = set_form_submission_policy(broker, "non-financial")
    assert policy.requires_confirmation(financial=False) is False
    assert policy.requires_confirmation(financial=True) is True
    assert get_form_submission_policy(broker).to_dict()["financial_confirmation"] == "always"


def test_invalid_policy_is_rejected_and_corrupt_persisted_value_fails_safe():
    broker = _broker()
    with pytest.raises(FormSubmissionPolicyError):
        set_form_submission_policy(broker, "never")
    broker.store.put("policy", "form-submission", {"confirmation": "never"})
    assert get_form_submission_policy(broker).confirmation == "always"
