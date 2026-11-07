# First-task usability check

This is a study protocol, not evidence that a user study has been run. Recruit
five people who already use a coding agent, after agreeing on participants and
invitations. Use synthetic accounts and data throughout.

Give each person a clean installation and the getting-started guide. Ask them
to work aloud without explaining which command to run. Record elapsed time,
where they hesitate, errors, and whether they complete each task unaided.
Do not record secrets, screens containing real accounts, or private form data.

| Task | Observable success |
| --- | --- |
| Choose a first task and run the synthetic demo | They can explain what ran locally and what still needs setup |
| Save one synthetic address and check profile readiness | No optional mailbox or password setup blocks them |
| Explain the access granted to an agent | They distinguish an expiring grant from ongoing trusted local read access |
| Pair the extension and fill a synthetic login | They choose the right account and purpose without copying a handoff; the form stays unsubmitted |
| Complete a username-first login | Username fill preserves the handoff for the password step |
| Recover from the wrong site, missing fields, and expired access | They know whether to retry the same request or obtain a new one |
| Correct an address choice | They distinguish use once, remember for this site and purpose, and replace the saved value |
| Revoke an agent | They verify a subsequent read is denied and understand that website sessions remain logged in |

Run browser tasks on a local synthetic fixture. Include separate username and
password pages, an ambiguous two-login page, and a page with hidden decoy fields.
Do not substitute automated test passes for participant observations.

For each participant record only a pseudonymous identifier, task completion,
time, assistance needed, and their explanation of access and revocation. Target
four of five completing the first task in five minutes unaided, and all five
correctly explaining the scope before a fill. Report actual results, including
failures. Prioritize repeated blocking mistakes before visual polish.
