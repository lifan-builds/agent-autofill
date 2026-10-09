# Agent Autofill form-filling evaluation set

This is a small live smoke set for checking the user path around Agent
Autofill: metadata review, field preparation, validation, and optional
credential delivery. It is intentionally separate from the deterministic
broker tests under `tests/agent_autofill/`.

The live cases use public practice sites and synthetic values. They are useful
for checking that a browser can receive ordinary fields, handle a credential
boundary, and report success or validation errors. They are not evidence that
an arbitrary production site, CAPTCHA, MFA flow, or provider-specific form is
supported.

## Safety rules

- Use synthetic names, usernames, addresses, and passwords generated for one
  run. Never use a real account, mailbox, card, OTP, or user profile value.
- Stop before a CAPTCHA, a browser permission prompt, a terms-of-service or
  other binding agreement, a payment, or an unexpected upload.
- The Selenium Web Form case deliberately detects unsafe reflection: its
  password field is echoed into the GET query string after submission. Use a
  nonsecret sentinel there only; never test a real credential on that page.
- The Expand Testing registration case creates a disposable synthetic test
  account. Record only the outcome and the site URL; do not store its password
  or session data. Log out after a successful login when the site supports it.
- The optional extension case requires the local loopback bridge and a user
  chosen extension installation. The extension must fill the active page only;
  the user reviews and submits any real form.

## Cases

The canonical case metadata is in [`cases.json`](cases.json). Each case has an
expected result and a safety boundary so a future run can be compared without
capturing values.

| ID | Scenario | Site | Expected result |
| --- | --- | --- | --- |
| FF-001 | Basic field matrix | Selenium Web Form | Ordinary fields and controls submit; credential reflection is flagged |
| FF-002 | Public demo login | The Internet | Login reaches `/secure`, then logout succeeds |
| FF-003 | Registration and login | Expand Testing | Synthetic account registers, logs in, reaches `/secure`, then logs out |
| FF-004 | Invalid login | Expand Testing | Wrong password stays on login and shows a validation error |
| FF-005 | Direct delivery | Local Agent Autofill broker | `request_credential(delivery="direct")` returns only approved fields |
| FF-006 | Extension delivery | Local bridge plus optional extension | One-time handle fills locally; password never enters agent input |

FF-001 through FF-004 are browser smoke cases. FF-005 and FF-006 are the
product-boundary cases already covered by the local service and bridge tests;
keep them in this set so a live run always checks both delivery choices.

## Run records

Append one Markdown file under `runs/` per run. Include the date, case IDs,
site URLs, pass/fail/blocked status, visible result, and safety findings. Do
not include passwords, handles, cookies, screenshots containing sensitive
values, or full submitted URLs when they contain form data. Keep a failed case
for diagnosis rather than silently rewriting its expected behavior.

The first live run is recorded in
[`runs/2026-10-07.md`](runs/2026-10-07.md). Future improvements should update
`cases.json` and add a new run record so changes remain comparable over time.
