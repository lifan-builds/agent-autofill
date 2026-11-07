# Real-site form reconnaissance — 2026-10-10

This was a metadata-only reconnaissance pass for the real-data, no-submit
lane. No saved Agent Autofill value was retrieved or typed. No form was
advanced, submitted, or used to create an account.

| Category | Site | Visible form surface | Result | Gap observed |
| --- | --- | --- | --- | --- |
| Account sign-up | [Notion](https://app.notion.com/signup) | Work email, Continue, social sign-in, terms/privacy | READY | Work-email wording differs from ordinary email labels; terms are binding if continued |
| Account sign-up | [Spotify](https://www.spotify.com/us/signup) | Email address, Next, Google/Apple paths, reCAPTCHA notice | READY | Next advances the flow; CAPTCHA/provider terms are outside the no-submit lane |
| Account sign-up | [Figma](https://www.figma.com/signup) | Email, Continue with email, Google path | READY | Email field is visually uppercase; provider-specific path must not be inferred |
| Education registration | [edX](https://authn.edx.org/register) | Full name, Email, Public username, Password, Country/Region, marketing checkbox, Create account | READY | Username, password, country, consent, and CAPTCHA require separate decisions |
| Contact / lead | [Calendly](https://calendly.com/contact) | First Name, Last Name, Work Email, usage select, comments, Submit, marketing consent | READY | Work-email and usage classification are distinct; comments and consent are not saved profile data |
| Sales lead | [Stripe](https://stripe.com/contact/sales) | Work email, Country/Region, Continue | READY | Country and progression are separate decisions; work email is the only safe candidate |
| Enterprise contact | [Cloudflare](https://www.cloudflare.com/plans/enterprise/contact/) | First Name, Last Name, Phone, Work Email, Company, job selects, country, comments, Contact us today | READY | Company, job level/function, country, comments, and marketing consent have no unambiguous saved mapping |
| Identity creation | [Google](https://accounts.google.com/signup) | First name, optional last name, Next | READY | Optional-last-name semantics are site-specific; later email/password steps remain untouched |

## Local metadata findings

- The normal local broker path is available and the Keychain readiness check is
  `ready`.
- The default Chrome profile is the only profile used for the imported metadata.
- The store contains multiple Chrome address groups with overlapping name,
  email, phone, and address field types. A group must be selected by meaning;
  the agent must not choose the first matching row.
- A metadata-only query found no saved email ending in
  a specific institutional domain; no value was printed or persisted.

## Pending real-data lane

Before typing, the acting agent still needs one explicit approval covering the
exact origins and categories in the proposed FF-007 through FF-012 cases. The
approval is for preparation only. The agent will not request another
Agent Autofill confirmation immediately before each field, and it will stop
before every submit, Continue, Next, social-login, consent, CAPTCHA, payment,
or other binding action.
