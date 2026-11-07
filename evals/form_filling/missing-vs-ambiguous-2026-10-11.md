# Missing versus ambiguous form fields — 2026-10-11

This is a metadata-only review generated from the current Agent Autofill
database seeded from Chrome's default profile. No profile values, usernames,
passwords, OTPs, addresses, or credential contents are included.

The live Chrome snapshot contained **99 structured fields across 13 source
groups**:

| Chrome field type | Count |
| --- | ---: |
| `state` | 13 |
| `city` | 12 |
| `country` | 12 |
| `postcode` | 12 |
| `street` | 11 |
| `first_name` | 9 |
| `email` | 8 |
| `last_name` | 8 |
| `phone` | 7 |
| `name` | 7 |

## Truly missing from the current profile memory

These labels had no compatible ordinary profile metadata in the live snapshot.
They are absent from the current Agent Autofill profile namespace, rather than
being blocked by a Chrome group choice.

| Site / journey | Form label | Why it is classified as missing |
| --- | --- | --- |
| Cloudflare enterprise contact | Company | No company record was present; Chrome intake currently allowlists contact and address types, not company. |
| Lever job application | Company / current company | No company record was present. |
| Lever job application | Resume | No saved resume/file record exists in the profile namespace. |
| Lever job application | LinkedIn | No saved social-profile field exists. |
| Lever job application | GitHub | No saved social-profile field exists. |
| Airbnb post-OTP signup | Date of birth | No date-of-birth field was present in the imported profile metadata. |

## Ambiguous, with Chrome-backed candidates

These fields have compatible metadata, but the agent cannot safely choose one
without a group or meaning decision. The current matcher now uses Chrome's
`field_type` provenance, so `Chrome email` and `Chrome country` are treated as
their semantic types rather than as unrelated display strings.

| Site / journey | Form label | Chrome-backed evidence | Acting-agent resolution | Learning after the run |
| --- | --- | --- | --- | --- |
| Airbnb | Phone number or email | Email and phone candidates exist across Chrome groups | Choose the input type and best group from the page context; continue filling. Confirm only before the next external action. | Record the chosen field/group with `outcome=accepted` or the user's correction with `outcome=corrected`. |
| Notion, Spotify, Figma, edX, Calendly, Stripe, Cloudflare, Microsoft, GitLab | Email / Work email / Company email | Eight Chrome email candidates across multiple groups | Choose the intended email meaning/group; “work” is not encoded reliably by Chrome provenance. | Save the site-and-purpose mapping after the user-confirmed result. |
| Lever, Cloudflare, FedEx | Phone | Seven Chrome phone candidates across multiple groups | Choose the best phone group; let the site validation result determine whether formatting needs remediation. | Save the accepted group only after the user confirms the outcome. |
| edX, Stripe, Cloudflare | Country/Region / Country | Twelve Chrome country candidates across multiple groups | Choose the address group by meaning and keep the choice within one group-bound read. | Save a corrected group when the user identifies the right address. |
| Lever | Current location | Street, city, state, postal-code, and country candidates exist in Chrome groups | Choose one address group using the job form's context; ask the user only if context cannot distinguish it. | Save the group choice for that origin and purpose. |
| Google, Calendly, Cloudflare, Lever, GitLab | First name / Last name / Full name | Multiple Chrome name-component candidates | Choose one source group; do not take the first matching row without considering page context. | Record accepted/corrected field IDs after the confirmed outcome. |
| Airbnb post-OTP signup | Legal name | First name, last name, and combined-name candidates exist | Choose the candidate set that best matches the site's legal-name instruction; confirm before account creation. | Record the corrected name-field mapping if the user changes it. |

## Not missing profile data

These appeared during the run but should not be counted as ingestion gaps:

| Labels | Classification |
| --- | --- |
| Username, Public username, Password | Credential or new-account workflow. They are not ordinary profile fields. |
| Usage, Job Level, Job Function, Account, Origin, Destination, Ship date, Package, Declared value, Comments | Task-specific input; the agent must ask for the current task rather than infer it from an address. |
| Marketing consent, Marketing checkbox, Terms of Service | Consent or legal controls, not saved profile data. |
| CAPTCHA, quote, Continue, Next, Submit, Contact us today | Site controls or blockers, not missing information. |

The run also recorded validation failures for a saved email and phone on two
sites. Those values were present; the sites rejected their format or semantics.
That is a validation/remediation gap, not a Chrome-ingestion gap.

## AWS and USPS reconciliation

The follow-up run added two richer forms without changing the profile snapshot:

| Site | Truly missing/task-specific | Ambiguous Chrome-backed data |
| --- | --- | --- |
| AWS Sales Support | Job role, company, company type, industry, use case, inquiry type, and project description are current sales-task inputs. Marketing consent is a separate user decision. | Name, email, phone, country, state, and postal code have candidates in multiple Chrome groups; the acting agent selected one coherent group by page context. |
| USPS Postage Calculator | Mailing-from ZIP, mailing-to ZIP, mailing date/time, mailpiece type, and ground-transport choice describe the current shipment. The page's default country/date/time are not saved profile values. | No compatible saved shipment record was present, so no Chrome candidate was used. |

This run also found a matcher taxonomy edge: USPS uses “mailing from/to”
language rather than the explicit words “origin” and “destination.” The
acting agent kept those fields empty correctly, but future matching should
recognize the phrasing as task-specific and honor an explicit task-specific
hint supplied by the acting agent.

## Interpretation

The acting agent owns ambiguous-field judgment. The matcher exposes candidate
group IDs and marks the decision as `agent_judgment`; it does not require a
user prompt before typing. The user confirmation gate remains immediately
before submission, and financial information always requires it. A confirmed
result or user correction can be stored as value-free mapping feedback so the
next run prefers the learned choice for that site and purpose.

The real Chrome import is incomplete by design for fields such as company,
date of birth, resumes, social links, job metadata, and free-text answers. The
larger issue was semantic matching: duplicated Chrome groups and labels such as
“Work email,” “Legal name,” and “Phone number or email” previously looked
missing even when compatible Chrome metadata existed. The matcher now reports
those as ambiguous and gives the acting agent a meaning-based choice.
