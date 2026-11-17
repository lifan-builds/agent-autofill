---
name: agent-autofill-form-memory
description: "Suggest and retrieve reusable Agent Autofill records while preparing or reviewing a form, then let the acting agent complete and submit it through its browser after the required confirmation gate. Do not use for unrelated email search, account administration, or generic profile writing."
---

# Agent Autofill form memory

Prepare, fill, and gate external form submissions using Agent Autofill's local metadata and scoped reads. Read [references/record-contracts.md](references/record-contracts.md) before calling `write_records` or `update_record_scope`.

## 1. Form-field preflight & retrieval

1. **Contextual & metadata preflight:** Check `get_task_context` when confirmed bundles exist (mapping explicit components like `street`, `city`, `postcode`, `country`). Otherwise send visible field labels, required flags, stable locator hints (`name`, `autocomplete`, `type`, `role`), `target_origin`, `purpose`, and safe state flags (`obscured`, `blocked`, `validation_error`, `task_specific`, `consent`, `page_default`) to `match_form_fields`—never pass form values.
2. **Act on `match_form_fields` classifications:**
   - `matched` / `ambiguous`: Use the value-free ranked `candidate_rows` (`group_summary`, `group_label`, `is_primary`, `matches_self_profile`, `recipient_initials`, `state`, `country`, `confidence`, `llm_guidance`). Pick the top/clearest candidate from page context without pausing per field; ask one grouped question (e.g., home vs. work) only when genuinely tied. Keep address components within a single `group_id` (`list_profile_groups` -> `request_profile_fields(..., group_id=...)`).
   - `credential`: Route through `match_credentials` / `request_credential` (`delivery="direct"` or `"extension"`).
   - `missing`: Use **Just-in-time capture** below.
   - `consent` / `task_specific` / `page_default`: Keep in current task state; never infer `task_specific` answers from profile records or treat `page_default` placeholders as saved user data.
   - `transient` / `obstructed` / `validation_error`: Never save OTPs/CAPTCHAs; report obstructions or validation errors without auto-accepting consent banners or transforming values blindly.
3. **Locate controls reliably:** Prefer accessible labels and `name`/`autocomplete`/`type`/`role` attributes over generated DOM IDs. Filling browser fields is preparation and needs no extra prompt before typing.

## 2. Just-in-time capture for missing fields

Pause once at the missing ordinary field (or batch multiple missing ordinary fields together) with a single compact check-in offering **use once**, **save to Agent Autofill**, or **skip**:

- Display the full proposal card inline (`field_id`, `category`, `display_name`, exact ordinary `value`, `source_context: "user supplied during this form"`, `sensitivity`, `saved_scope: "any future authorized origin/purpose"`, active `target_origin`/`purpose`, `confidence`, `ambiguity`, plus narrow `profile.write` grant details if needed).
- **Use once** keeps the value transient for the current form only.
- **Save to Agent Autofill** approves both the displayed reusable record and narrow `profile.write` grant in one step (no second confirmation prompt unless details change): acquire the narrow grant if needed and call `write_records(..., confirmed=True)`. Never offer profile-save for passwords, payment fields, OTPs, tokens, or inferred page values.

## 3. Proposals, updates & learned mappings

- **Proposal table contract (for batch saves/updates):** Show every eligible `add`, `update`, `no-op`, and `held` row with explicit columns:
  `# | action | record type/id | display/name | category/kind + fields | value preview | source context | sensitivity | allowed origins/purposes | confidence + ambiguity note`
  Mask credential/financial previews and require explicit row numbers (e.g. `save 1 and 3`) before calling `write_records(..., confirmed=True)` with valid schema fields from `record-contracts.md` only.
- **Full replacement vs. metadata-only update:** `write_records` replaces the entire record (never copy values from a prior scoped read into `write_records`). To change only `allowed_origins`, `allowed_purposes` (`["*"]` -> `[]`), `is_default`, `matching_hints` (e.g. `hint: "C1vx"`), `display_name`, `name`, `group_label`, or `is_primary`, call `update_record_scope(..., confirmed=True)` so encrypted secrets remain untouched.
- **Learned form mappings:** When the user chooses to remember a selection/correction for a site, call `record_form_mapping_feedback` (stores labels, IDs, `group_id`, origin, purpose, and outcome—never raw values).

## 4. External submission gate

- **Routine saved sign-in:** Authorized by the user's task—submit without an extra prompt.
- **All other forms:** Stop immediately before clicking the external submit control and display exact `target_origin`, `purpose`, selected field names, masked credential/financial summary, and expected side effect (payment, signup, application, account change). Require explicit confirmation (`agent-autofill policy form-submission`; `non-financial` may skip only when zero financial fields are used; financial forms always require confirmation). Re-confirm if origin, amount, recipient, or values change, and verify post-submit page state afterward.
