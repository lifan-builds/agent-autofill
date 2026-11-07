"""Local policy for acting-agent form submission.

Agent Autofill does not drive a browser.  This small policy object gives an
acting agent one consistent decision point immediately before it submits a
form, while keeping the policy itself in the encrypted local store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

POLICY_NAMESPACE = "policy"
POLICY_KEY = "form-submission"
CONFIRMATION_ALWAYS = "always"
CONFIRMATION_NON_FINANCIAL = "non-financial"
CONFIRMATION_MODES = (CONFIRMATION_ALWAYS, CONFIRMATION_NON_FINANCIAL)


class FormSubmissionPolicyError(ValueError):
    """Raised when an unsupported submission policy is requested."""


@dataclass(frozen=True)
class FormSubmissionPolicy:
    """The confirmation policy used by the acting agent.

    ``non-financial`` may allow an agent to skip a second prompt for an
    ordinary form only.  Financial fields always require confirmation.
    """

    confirmation: str = CONFIRMATION_ALWAYS

    def __post_init__(self) -> None:
        if self.confirmation not in CONFIRMATION_MODES:
            raise FormSubmissionPolicyError(
                "form submission confirmation must be always or non-financial"
            )

    def requires_confirmation(self, *, financial: bool) -> bool:
        return financial or self.confirmation == CONFIRMATION_ALWAYS

    def to_dict(self) -> dict[str, Any]:
        return {
            "confirmation": self.confirmation,
            # This is deliberately explicit in the persisted projection so a
            # dashboard cannot imply that financial confirmation is optional.
            "financial_confirmation": "always",
        }


def get_form_submission_policy(broker: Any) -> FormSubmissionPolicy:
    """Read the local policy, defaulting safely to ``always``."""

    value = broker.store.get(POLICY_NAMESPACE, POLICY_KEY)
    if not isinstance(value, dict):
        return FormSubmissionPolicy()
    confirmation = value.get("confirmation")
    if not isinstance(confirmation, str):
        return FormSubmissionPolicy()
    try:
        return FormSubmissionPolicy(confirmation)
    except FormSubmissionPolicyError:
        # A manually edited or old value must never weaken the safety default.
        return FormSubmissionPolicy()


def set_form_submission_policy(broker: Any, confirmation: str) -> FormSubmissionPolicy:
    """Persist a validated policy without storing form values or secrets."""

    policy = FormSubmissionPolicy(confirmation)
    broker.store.put(POLICY_NAMESPACE, POLICY_KEY, policy.to_dict())
    return policy
