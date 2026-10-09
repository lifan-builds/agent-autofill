"""Command-line setup and administration for the local broker."""

import argparse
import getpass
import json
import re
from pathlib import Path
from typing import Any
from . import cli_connectors as _connector_flows
from .chrome import (
    cold_start,
    discover_profiles,
    preview_chrome_password_manager,
    sync_chrome_password_manager,
)
from .cli_connectors import (
    _configure_gmail as _configure_gmail_impl,
    _configure_imap as _configure_imap_impl,
    _import_thunderbird as _import_thunderbird_impl,
)
from .cli_connectors import _oauth_keychain_secret as _oauth_keychain_secret  # noqa: F401
from .cli_connectors import IMAPEmailAdapter
from .thunderbird import (
    ThunderbirdError,
    discover_profile,
    test_profile,
)
from .service import Broker
from .status import (
    _print_status,
    _safe_email_match_metadata,
    _startup_failure_snapshot,
    _status_snapshot,
)
from .storage import KeyringProvider, Store
from .local import default_database_path


_SETUP_GRANT_EXPIRES_IN = 600
_SITE_DELIVERY_OPERATIONS = {
    "direct": "credential.read",
    "extension": "credential.use",
}
_SITE_OPERATION_DELIVERIES = {
    operation: delivery
    for delivery, operation in _SITE_DELIVERY_OPERATIONS.items()
}
_UNSET = object()


def _print_box(title: str, *lines: str) -> None:
    """Render a small dependency-free panel for interactive CLI guidance."""

    content = [title, *lines]
    width = max(len(line) for line in content) + 2
    print(f"╭{'─' * width}╮")
    for line in content:
        print(f"│ {line.ljust(width - 2)} │")
    print(f"╰{'─' * width}╯")


def _print_next(message: str) -> None:
    """Print the one next action users can take after a CLI operation."""

    print(f"Next: {message}")


def _print_fix(message: str) -> None:
    """Print a concrete recovery action without exposing exception details."""

    print(f"Fix: {message}")


def _print_onboarding_banner() -> None:
    _print_box(
        "✦ Agent Autofill · first run",
        "Private memory for capable agents.",
        "1. Check the encrypted local vault",
        "2. Choose what to remember",
        "3. Seed ordinary profile data from a selected Chrome profile",
        "4. Review every access grant",
        "Use the agent-led Chrome seed guide for ambiguous fields.",
        "Press Ctrl-C at any prompt to cancel safely.",
    )


def _select_setup_profile(explicit_profile: str | None) -> str:
    """Choose a Chrome profile without silently selecting the first one."""

    if explicit_profile:
        return explicit_profile
    profiles = discover_profiles()
    if profiles:
        print("Detected Chrome profiles (choose one explicitly; values are not read yet):")
        for index, profile in enumerate(profiles, start=1):
            print(f"  {index}. {profile}")
        choice = input("Chrome profile number or path (blank to skip): ").strip()
        if not choice:
            return ""
        if choice.isdigit():
            selected = int(choice)
            if 1 <= selected <= len(profiles):
                return str(profiles[selected - 1])
            raise SystemExit("Choose one of the listed Chrome profile numbers")
        return choice
    return input("Chrome profile directory (blank to skip): ").strip()


def _demo_snapshot() -> dict[str, object]:
    """Build a synthetic, memory-only status projection for first-run tours.

    The demo deliberately uses the real broker and metadata projections while
    keeping its store in SQLite memory.  It never opens the user's database,
    Keychain, browser profile, mailbox, or Messages database.
    """

    demo_store = Store(":memory:", key=b"d" * 32)
    try:
        demo_broker = Broker(demo_store)
        demo_broker.put_profile(
            "display_name",
            "identity",
            "Display name",
            "Synthetic Demo User",
            provenance={"source": "synthetic-demo"},
        )
        demo_broker.put_credential(
            "example-login",
            "Example account",
            "https://example.test",
            "demo@example.test",
            "synthetic-password",
            provenance={"source": "synthetic-demo"},
        )
        demo_broker.create_agent("demo-agent")
        demo_broker.grant(
            "demo-agent",
            "profile.read",
            "display_name",
            "https://example.test",
            "demo walkthrough",
            600,
        )
        grants = demo_broker.list_grants()
        agents = demo_broker.list_agents()
        snapshot: dict[str, object] = {
            "status": "ready",
            "next_action": "Exit demo and run `agent-autofill start` for your local vault",
            "broker": {
                "status": "ready",
                "summary": "Synthetic encrypted broker opened in memory",
            },
            "keychain": {
                "status": "demo",
                "summary": "Skipped in demo; no operating-system credential store was opened",
            },
            "stored": {
                "sources": 0,
                "profile_fields": len(demo_broker.list_profile_fields()),
                "profile_groups": len(demo_broker.list_profile_groups()),
                "credentials": len(demo_broker.list_credentials()),
                "financial_records": len(demo_broker.list_financial_records()),
                "agents": len(agents),
                "active_grants": sum(bool(item.get("active")) for item in grants),
                "recent_activity": 0,
            },
            "sources": {"configured": 0, "ready": 0, "needs_attention": 0, "items": []},
            "agents": {
                "configured": len(agents),
                "revoked": sum(bool(item.get("revoked")) for item in agents),
            },
            "grants": {
                "active": sum(bool(item.get("active")) for item in grants),
                "expired": 0,
                "expiring_24h": 0,
                "invalid_expiry": 0,
                "revoked": 0,
            },
            "attention": [],
            "local_sources": {
                "chrome_profiles": 0,
                "chrome_status": "skipped",
                "messages_status": "skipped",
            },
        }
        snapshot["demo"] = {
            "synthetic": True,
            "database": "memory-only",
            "next_action": "Exit demo and run `agent-autofill start` for your local vault",
        }
        return snapshot
    finally:
        demo_store.db.close()


def _sync_connector_test_seams() -> None:
    """Keep legacy CLI monkeypatch/import seams working after extraction."""

    setattr(_connector_flows, "IMAPEmailAdapter", IMAPEmailAdapter)


def _configure_imap(*args: Any, **kwargs: Any) -> dict:
    _sync_connector_test_seams()
    return _configure_imap_impl(*args, **kwargs)


def _configure_gmail(*args: Any, **kwargs: Any) -> dict:
    _sync_connector_test_seams()
    return _configure_gmail_impl(*args, **kwargs)


def _test_saved_imap(*args: Any, **kwargs: Any) -> dict:
    _sync_connector_test_seams()
    return _connector_flows._test_saved_imap(*args, **kwargs)


def _import_thunderbird(*args: Any, **kwargs: Any) -> list[dict]:
    return _import_thunderbird_impl(*args, **kwargs)


class _MarkExplicitAction(argparse.Action):
    """Keep a legacy default while remembering whether an option was supplied."""

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        setattr(namespace, f"_{self.dest}_explicit", True)


def _store(path: str, keyring: KeyringProvider | None = None) -> Store:
    if keyring is None:
        keyring = KeyringProvider()
    return Store(
        path,
        key_provider=keyring,
        key_name=Path(path).expanduser().absolute().as_posix(),
    )


def _broker(path: str) -> Broker:
    effective_path = default_database_path(
        None if path == "agent-autofill.sqlite3" else path
    )
    keyring = KeyringProvider()
    broker = Broker(_store(str(effective_path), keyring))
    connector_names = []
    for row in broker.store.db.execute(
        "select key from kv where namespace='connection'"
    ):
        config = broker.store.get("connection", row["key"])
        if config and config.get("secret_name"):
            connector_names.append(config["secret_name"])
    migrate = getattr(keyring, "migrate_legacy", None)
    setattr(broker, "_keyring_migrated_count", migrate(connector_names) if callable(migrate) else 0)
    setattr(broker, "_keyring_provider", keyring)
    return broker


def _keyring_for(broker: Broker) -> KeyringProvider:
    provider = getattr(broker, "_keyring_provider", None)
    return provider if provider is not None else KeyringProvider()




def _setup_parse_selection(
    raw: str, label: str, available: tuple[str, ...]
) -> list[str]:
    """Parse a comma-separated exact-id selection before any grant is written."""

    value = raw.strip()
    if not value:
        return []
    selected = [part.strip() for part in value.split(",")]
    if any(not part for part in selected):
        raise SystemExit(f"{label} contains an empty selection")
    if len(set(selected)) != len(selected):
        raise SystemExit(f"{label} cannot contain duplicate selections")
    if any("*" in part for part in selected):
        raise SystemExit(f"{label} does not permit wildcard selections")
    unknown = [part for part in selected if part not in available]
    if unknown:
        raise SystemExit(f"Unknown {label}: {', '.join(unknown)}")
    return selected


def _setup_validate_agent(broker: Broker, agent_id: str) -> None:
    """Refuse missing or revoked identities without changing their token state."""

    identity = broker.store.get("agent", agent_id)
    if identity is None:
        raise SystemExit(f"Unknown agent identity: {agent_id}")
    if identity.get("revoked", False):
        raise SystemExit(
            f"Agent identity is revoked; refusing to create grants for {agent_id}"
        )


def _configure_site_grants(
    broker: Broker,
    agent_id: str,
    target_origin: str,
    purpose: str,
    operation: str | None | object = _UNSET,
    username_hint: str | None = None,
    expires_in: int = _SETUP_GRANT_EXPIRES_IN,
    *,
    delivery: str | None = None,
) -> list:
    """Preview exact site credential grants, then persist only after approval.

    Existing callers that omit ``operation`` retain the historical extension
    grant (``credential.use``).  The CLI passes ``operation=None`` when both
    delivery options were omitted so that this function can ask for an
    explicit direct/extension choice instead of silently selecting one.
    """

    if operation is _UNSET:
        # Preserve the helper's historical default for setup code and tests.
        operation = "credential.use" if delivery is None else None

    if delivery is not None:
        if delivery not in _SITE_DELIVERY_OPERATIONS:
            raise SystemExit("Credential delivery must be direct or extension")
        if operation is not None:
            raise SystemExit("Credential delivery cannot be combined with operation")
        operation = _SITE_DELIVERY_OPERATIONS[delivery]
    elif operation is None:
        print("Choose credential delivery mode:")
        print(
            "1) direct: selected credential fields are returned in the agent response."
        )
        print(
            "2) extension: the agent receives a one-time handle; the password stays "
            "outside the agent response."
        )
        try:
            selected = input("Delivery mode [1/2] (blank cancels): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            selected = ""
        if selected in {"1", "direct"}:
            delivery = "direct"
        elif selected in {"2", "extension"}:
            delivery = "extension"
        else:
            print(
                "Site credential grant setup cancelled; choose direct or extension "
                "before creating grants."
            )
            return []
        operation = _SITE_DELIVERY_OPERATIONS[delivery]

    if operation not in _SITE_OPERATION_DELIVERIES:
        raise SystemExit("Credential grant operation must be credential.use or credential.read")
    if delivery is None:
        delivery = _SITE_OPERATION_DELIVERIES[operation]

    _setup_validate_agent(broker, agent_id)
    if not isinstance(target_origin, str) or not target_origin.strip():
        raise SystemExit("A nonempty target origin is required")
    if not isinstance(purpose, str) or not purpose.strip():
        raise SystemExit("A nonempty purpose is required")
    if "*" in target_origin or "*" in purpose:
        raise SystemExit("Grant origin and purpose do not permit wildcards")
    if expires_in < 1 or expires_in > 86400:
        raise SystemExit("Grant lifetime must be between 1 and 86400 seconds")
    matches = broker.match_credentials(target_origin, username_hint, limit=100)
    if not matches:
        print("No matching credentials found; no grants created.")
        return []
    if delivery == "direct":
        print(
            'Direct mode selected: after approval, use '
            'request_credential(delivery="direct") with the approved field IDs; '
            "only those fields are returned to the agent."
        )
    else:
        print(
            'Extension mode selected: after approval, use '
            'request_credential(delivery="extension"); the agent receives only a '
            "one-time handle and the password stays outside the agent response."
        )
    proposal = [
        {
            "operation": operation,
            "credential_id": item["credential_id"],
            "name": item["name"],
            "username": item["username"],
            "origin": item["origin"],
            "target_origin": target_origin,
            "purpose": purpose,
            "expires_in_seconds": expires_in,
        }
        for item in matches
    ]
    print("Proposed site credential grants (passwords omitted):")
    print(json.dumps({"grants": proposal}, indent=2))
    if input("Persist these grants? [y/N] ").strip().lower() != "y":
        print("Site credential grant setup cancelled; existing grants remain unchanged.")
        return []
    _setup_validate_agent(broker, agent_id)
    grants = [
        broker.grant(
            agent_id,
            item["operation"],
            item["credential_id"],
            item["target_origin"],
            item["purpose"],
            item["expires_in_seconds"],
        )
        for item in proposal
    ]
    print(f"Created {len(grants)} site credential grants.")
    if delivery == "direct":
        print('Next step: request_credential(delivery="direct") with approved field IDs.')
    else:
        print(
            'Next step: request_credential(delivery="extension"); consume the '
            "one-time handle in the local extension."
        )
    return grants


def _setup_grant_proposal(
    broker: Broker,
    agent_id: str,
    profile_field_ids: list[str] | tuple[str, ...],
    email_account_id: str | None,
    *,
    include_email_search: bool = False,
    include_email_otp: bool = False,
    target_origin: str | None = None,
    purpose: str | None = None,
) -> list[dict]:
    """Validate and build the value-free grant proposal for setup.

    All selection checks happen before callers invoke ``Broker.grant``.  The
    only operations this wizard can produce are the three explicitly scoped
    read operations below; in particular it cannot create a wildcard grant.
    """

    _setup_validate_agent(broker, agent_id)
    available_fields = tuple(
        item["field_id"]
        for item in broker.list_profile_fields()
        if isinstance(item.get("field_id"), str) and item["field_id"]
    )
    selected_fields = list(profile_field_ids)
    if any(not isinstance(field_id, str) or not field_id for field_id in selected_fields):
        raise SystemExit("Profile field IDs must be nonempty exact IDs")
    if len(set(selected_fields)) != len(selected_fields):
        raise SystemExit("Profile field IDs cannot contain duplicates")
    if any("*" in field_id for field_id in selected_fields):
        raise SystemExit("Profile field IDs do not permit wildcard selections")
    unknown_fields = [
        field_id for field_id in selected_fields if field_id not in available_fields
    ]
    if unknown_fields:
        raise SystemExit(f"Unknown profile field ID: {', '.join(unknown_fields)}")

    available_accounts = tuple(
        source["source_id"]
        for source in broker.list_sources()
        if source.get("kind") == "email"
        and isinstance(source.get("source_id"), str)
        and source["source_id"]
    )
    if email_account_id is not None:
        if not isinstance(email_account_id, str):
            raise SystemExit("Email account ID must be a nonempty exact ID")
        email_account_id = email_account_id.strip()
    if include_email_search or include_email_otp:
        if not email_account_id:
            raise SystemExit(
                "An exact configured email account is required for email grants"
            )
        if "*" in email_account_id or email_account_id not in available_accounts:
            raise SystemExit(f"Unknown email account ID: {email_account_id}")
    elif email_account_id and (
        "*" in email_account_id or email_account_id not in available_accounts
    ):
        raise SystemExit(f"Unknown email account ID: {email_account_id}")

    if selected_fields or include_email_otp:
        if not isinstance(target_origin, str) or not target_origin.strip():
            raise SystemExit(
                "A nonempty origin is required for profile and OTP grants"
            )
        if not isinstance(purpose, str) or not purpose.strip():
            raise SystemExit(
                "A nonempty purpose is required for profile and OTP grants"
            )
        target_origin = target_origin.strip()
        purpose = purpose.strip()
        if "*" in target_origin or "*" in purpose:
            raise SystemExit("Grant origin and purpose do not permit wildcards")

    proposal = [
        {
            "operation": "profile.read",
            "resource_scope": field_id,
            "target_origin": target_origin,
            "purpose": purpose,
            "expires_in_seconds": _SETUP_GRANT_EXPIRES_IN,
        }
        for field_id in selected_fields
    ]
    if include_email_search:
        proposal.append(
            {
                "operation": "email.search",
                "resource_scope": email_account_id,
                "target_origin": None,
                "purpose": None,
                "expires_in_seconds": _SETUP_GRANT_EXPIRES_IN,
            }
        )
    if include_email_otp:
        proposal.append(
            {
                "operation": "email.otp",
                "resource_scope": email_account_id,
                "target_origin": target_origin,
                "purpose": purpose,
                "expires_in_seconds": _SETUP_GRANT_EXPIRES_IN,
            }
        )
    return proposal


def _configure_setup_grants(broker: Broker, agent_id: str) -> list:
    """Offer optional, exact, value-free grants at the end of setup."""

    _setup_validate_agent(broker, agent_id)
    if input("Configure scoped access grants now? [y/N] ").strip().lower() != "y":
        return []

    profile_ids = tuple(
        item["field_id"]
        for item in broker.list_profile_fields()
        if isinstance(item.get("field_id"), str) and item["field_id"]
    )
    if profile_ids:
        print("Available profile field IDs: " + ", ".join(profile_ids))
        selected_fields = _setup_parse_selection(
            input("Profile field IDs to authorize (comma-separated, blank to skip): "),
            "profile field IDs",
            profile_ids,
        )
    else:
        print("No saved profile fields are available for setup grants.")
        selected_fields = []

    email_accounts = tuple(
        source["source_id"]
        for source in broker.list_sources()
        if source.get("kind") == "email"
        and isinstance(source.get("source_id"), str)
        and source["source_id"]
    )
    email_account_id = None
    include_email_search = False
    include_email_otp = False
    if email_accounts:
        print("Available email account IDs: " + ", ".join(email_accounts))
        raw_account = input(
            "Email account ID for optional email grants (blank to skip): "
        )
        if raw_account.strip():
            account_selection = _setup_parse_selection(
                raw_account, "email account ID", email_accounts
            )
            if len(account_selection) != 1:
                raise SystemExit("Select exactly one email account ID")
            email_account_id = account_selection[0]
            include_email_search = (
                input("Authorize account-wide email search? [y/N] ").strip().lower()
                == "y"
            )
            include_email_otp = (
                input("Authorize origin/purpose-bound email OTP? [y/N] ").strip().lower()
                == "y"
            )

    target_origin = None
    purpose = None
    if selected_fields or include_email_otp:
        target_origin = input("Origin for profile/OTP grants (required): ").strip()
        purpose = input("Purpose for profile/OTP grants (required): ").strip()

    # Build the complete proposal before printing a confirmation or calling
    # Broker.grant, so a bad id/scope cannot leave a partial grant set behind.
    proposal = _setup_grant_proposal(
        broker,
        agent_id,
        selected_fields,
        email_account_id,
        include_email_search=include_email_search,
        include_email_otp=include_email_otp,
        target_origin=target_origin,
        purpose=purpose,
    )
    if not proposal:
        print("No grants selected; existing grants remain unchanged.")
        return []

    print("Proposed setup grants (no stored values or connector secrets):")
    print(json.dumps({"grants": proposal}, indent=2))
    if any(item["operation"] == "email.search" for item in proposal):
        print(
            "Email search is account-wide for the selected account; it is not "
            "limited to the target origin."
        )
    if input("Persist these grants? [y/N] ").strip().lower() != "y":
        print("Grant setup cancelled; existing grants remain unchanged.")
        return []
    _setup_validate_agent(broker, agent_id)
    grants = [
        broker.grant(
            agent_id,
            item["operation"],
            item["resource_scope"],
            item["target_origin"],
            item["purpose"],
            _SETUP_GRANT_EXPIRES_IN,
        )
        for item in proposal
    ]
    print(f"Created {len(grants)} setup grants (lifetime: {_SETUP_GRANT_EXPIRES_IN}s).")
    return grants


def _payment_card_digits(value: str) -> str:
    """Validate the local CLI card-number input without echoing it."""

    if not isinstance(value, str):
        raise SystemExit("Card number must contain 12 to 19 digits")
    digits = value.strip().replace(" ", "").replace("-", "")
    if not re.fullmatch(r"\d{12,19}", digits):
        raise SystemExit("Card number must contain 12 to 19 digits")
    return digits


def _payment_card_expiration(value: str, label: str, pattern: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(pattern, value.strip()):
        raise SystemExit(
            f"{label} is invalid; no payment-card record was written"
        )
    value = value.strip()
    return str(int(value)).zfill(2) if label == "Expiration month" else value


def _reject_raw_card_metadata(label: str, value: str) -> None:
    """Avoid echoing an accidental full card number in the review preview."""

    if not isinstance(value, str) or re.search(r"\d{8,}", value):
        raise SystemExit(f"{label} cannot contain a raw financial number")


def _configure_payment_card(
    broker: Broker,
    record_id: str,
    display_name: str,
    *,
    network: str | None = None,
    expiration_month: str | None = None,
    expiration_year: str | None = None,
    cardholder_name: str | None = None,
    billing_postal_code: str | None = None,
    allowed_origins: list[str] | None = None,
    allowed_purposes: list[str] | None = None,
) -> bool:
    """Review and store one payment card using hidden prompts for secrets."""

    _reject_raw_card_metadata("record ID", record_id)
    _reject_raw_card_metadata("display name", display_name)
    if network:
        _reject_raw_card_metadata("card network", network)
    card_number = _payment_card_digits(
        getpass.getpass("Payment card number (not echoed): ")
    )
    expiration_month = expiration_month or input("Expiration month (MM): ").strip()
    expiration_month = _payment_card_expiration(
        expiration_month, "Expiration month", r"(?:0?[1-9]|1[0-2])"
    )
    expiration_year = expiration_year or input("Expiration year (YYYY): ").strip()
    expiration_year = _payment_card_expiration(
        expiration_year, "Expiration year", r"\d{4}"
    )
    security_code = getpass.getpass(
        "Security code (optional; press Enter to skip, not echoed): "
    )
    if security_code and not re.fullmatch(r"\d{3,4}", security_code):
        raise SystemExit("Security code must contain 3 or 4 digits")
    additional_security_code = getpass.getpass(
        "Additional 3-digit Amex code (optional; press Enter to skip, not echoed): "
    )
    if additional_security_code and not re.fullmatch(
        r"\d{3}", additional_security_code
    ):
        raise SystemExit("Additional security code must contain 3 digits")
    network = (network if network is not None else input(
        "Card network (optional, e.g. Visa): "
    )).strip()

    existing = broker.store.get("financial", record_id)
    action = "replace" if existing else "store"
    matching_hints = {
        key: value
        for key, value in {
            "network": network or None,
            "last4": card_number[-4:],
            "expiration_month": expiration_month,
            "expiration_year": expiration_year,
        }.items()
        if value is not None
    }
    matching_records = [
        {
            "record_id": item["record_id"],
            "display_name": item["display_name"],
            "field_ids": item["field_ids"],
            "matching_hints": item["matching_hints"],
        }
        for item in broker.match_financial_records(
            kind="payment_card", matching_hints=matching_hints
        )
        if item["record_id"] != record_id
    ]
    proposal: dict[str, Any] = {
        "action": action,
        "record_id": record_id,
        "kind": "payment_card",
        "display_name": display_name,
        "network": network or None,
        "last4": card_number[-4:],
        "expiration_month": expiration_month,
        "expiration_year": expiration_year,
        "fields": [
            "card_number",
            "expiration_month",
            "expiration_year",
            *(["security_code"] if security_code else []),
            *(["additional_security_code"] if additional_security_code else []),
            *(["cardholder_name"] if cardholder_name else []),
            *(["billing_postal_code"] if billing_postal_code else []),
        ],
        "allowed_origins": allowed_origins or [],
        "allowed_purposes": allowed_purposes or [],
    }
    if matching_records:
        proposal["matching_records"] = matching_records
        print(
            "Matching existing card metadata found; use one of those record IDs "
            "to replace a record instead of creating a duplicate."
        )
    print("Proposed encrypted local payment-card record (values masked):")
    print(json.dumps(proposal, indent=2))
    if input("Store this payment card locally? [y/N] ").strip().lower() != "y":
        print("Payment-card storage cancelled; no record was written.")
        return False

    broker.put_payment_card(
        record_id=record_id,
        display_name=display_name,
        card_number=card_number,
        expiration_month=expiration_month,
        expiration_year=expiration_year,
        security_code=security_code or None,
        additional_security_code=additional_security_code or None,
        cardholder_name=cardholder_name,
        billing_postal_code=billing_postal_code,
        network=network or None,
        allowed_origins=allowed_origins,
        allowed_purposes=allowed_purposes,
        provenance={"source": "local-cli"},
    )
    print(
        f"Stored payment-card record {record_id} (last four {card_number[-4:]}; "
        "full values remain encrypted and are omitted from listings)."
    )
    return True




def _interactive_profile(broker: Broker, args: argparse.Namespace) -> bool:
    """Collect one profile value without placing it in shell arguments."""

    existing = broker.store.get("profile", args.field_id)
    metadata = next(
        (
            item
            for item in broker.list_profile_fields()
            if item.get("field_id") == args.field_id
        ),
        None,
    )
    if args.profile_command == "edit" and existing is None:
        raise SystemExit(f"No saved profile field named {args.field_id}")
    if args.profile_command == "add" and existing is not None:
        raise SystemExit(
            f"Profile field {args.field_id} already exists; use profile edit to replace it"
        )
    current = metadata or {}
    category = (args.category if args.category is not None else "") or str(
        current.get("category", "custom")
    )
    supplied_display_name = args.display_name
    if supplied_display_name is None:
        supplied_display_name = getattr(args, "display_name_arg", None)
    display_name = (supplied_display_name if supplied_display_name is not None else "") or str(
        current.get("display_name", args.field_id)
    )
    sensitivity = (args.sensitivity if args.sensitivity is not None else "") or str(
        current.get("sensitivity", "normal")
    )
    if args.profile_command == "edit":
        category = input(f"Category [{category}]: ").strip() or category
        display_name = input(f"Display name [{display_name}]: ").strip() or display_name
        sensitivity = input(f"Sensitivity [{sensitivity}]: ").strip() or sensitivity
    else:
        category = input(f"Category [{category}]: ").strip() or category
        display_name = input(f"Display name [{display_name}]: ").strip() or display_name
        sensitivity = input(f"Sensitivity [{sensitivity}]: ").strip() or sensitivity
    origins = list(args.origin or current.get("allowed_origins", []))
    purposes = list(args.purpose or current.get("allowed_purposes", []))
    if not args.origin:
        raw_origins = input(
            "Allowed origins (comma-separated, blank for none) "
            f"[{', '.join(origins)}]: "
        ).strip()
        if raw_origins:
            origins = [item.strip() for item in raw_origins.split(",") if item.strip()]
    if not args.purpose:
        raw_purposes = input(
            "Allowed purposes (comma-separated, blank for none) "
            f"[{', '.join(purposes)}]: "
        ).strip()
        if raw_purposes:
            purposes = [item.strip() for item in raw_purposes.split(",") if item.strip()]

    if args.profile_command == "edit":
        value = getpass.getpass(
            "New profile value (leave blank to keep the encrypted value; not echoed): "
        )
        has_new_value = bool(value)
    else:
        value = getpass.getpass("Profile value (not echoed): ")
        has_new_value = True
    proposal = {
        "action": "replace" if existing is not None else "store",
        "field_id": args.field_id,
        "category": category,
        "display_name": display_name,
        "sensitivity": sensitivity,
        "allowed_origins": origins,
        "allowed_purposes": purposes,
        "value": "new value supplied" if has_new_value else "existing value retained",
    }
    print("Proposed encrypted local profile field (value omitted):")
    print(json.dumps(proposal, indent=2))
    if input("Store this profile field locally? [y/N] ").strip().lower() != "y":
        print("Profile storage cancelled; no record was written.")
        return False
    if not has_new_value:
        assert isinstance(existing, dict)
        value = str(existing.get("value") or "")
    broker.put_profile(
        args.field_id,
        category,
        display_name,
        value,
        sensitivity,
        origins,
        purposes,
        provenance={"source": "local-cli"},
    )
    print(f"Stored profile field {args.field_id} (value remains encrypted and omitted from listings).")
    return True


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent-autofill",
        description=(
            "Local-first personal information broker for agents.\n"
            "Values stay encrypted locally; every delivery path is reviewed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Quick paths:\n"
            "  %(prog)s start                 guided first-run setup\n"
            "  %(prog)s status                value-free readiness summary\n"
            "  %(prog)s demo                  try the UI with synthetic data\n"
            "  %(prog)s data                  see what is stored (metadata only)\n"
            "  %(prog)s access                review agent access and grants\n\n"
            "Chrome seed:\n"
            "  %(prog)s chrome profiles       list profiles for explicit selection\n"
            "  Then ask your local agent to use the Chrome intake guide.\n\n"
            "Use `%(prog)s <command> --help` for examples and options."
        ),
    )
    p.add_argument(
        "--db", default="agent-autofill.sqlite3", help="encrypted SQLite database path"
    )
    sub = p.add_subparsers(
        dest="command",
        required=True,
        title="commands",
        metavar="{start,status,demo,data,access,...}",
    )
    sub.add_parser("init", help="create/open the encrypted local database")
    profile = sub.add_parser("profile", help="manage local profile fields")
    ps = profile.add_subparsers(dest="profile_command", required=True)
    setp = ps.add_parser("set")
    setp.add_argument("field_id")
    setp.add_argument("display_name")
    setp.add_argument("value")
    setp.add_argument("--category", default="custom")
    setp.add_argument("--sensitivity", default="normal")
    setp.add_argument("--origin", action="append", default=[])
    setp.add_argument("--purpose", action="append", default=[])
    for profile_action, profile_help in (
        ("add", "add one profile field through hidden value input"),
        ("edit", "edit one profile field through hidden value input"),
    ):
        interactive = ps.add_parser(profile_action, help=profile_help)
        interactive.add_argument("field_id")
        interactive.add_argument(
            "display_name_arg", nargs="?", help="optional nonsecret display name"
        )
        interactive.add_argument("--display-name")
        interactive.add_argument("--category")
        interactive.add_argument("--sensitivity")
        interactive.add_argument("--origin", action="append", default=[])
        interactive.add_argument("--purpose", action="append", default=[])
    profile_list = ps.add_parser("list")
    profile_list.add_argument("--category")
    profile_list.add_argument("--query")
    profile_list.add_argument("--limit", type=int, default=100)
    grant = sub.add_parser("grant", help="create an agent capability grant")
    grant.add_argument("agent_id")
    grant.add_argument("operation")
    grant.add_argument("resource_scope")
    grant.add_argument("--origin")
    grant.add_argument("--purpose")
    grant.add_argument("--expires-in", type=int, default=600)
    email_grant = sub.add_parser(
        "grant-email", help="create an exact or all-mailbox email grant"
    )
    email_grant.add_argument("agent_id")
    email_grant.add_argument(
        "operation",
        choices=[
            "email.search",
            "email.read",
            "email.otp",
            "email.verification_link",
        ],
    )
    email_grant.add_argument("recipient", nargs="?")
    email_grant.add_argument(
        "--all",
        action="store_true",
        help="grant this operation for every configured email source (same as RECIPIENT=all)",
    )
    email_grant.add_argument("--origin")
    email_grant.add_argument("--purpose")
    email_grant.add_argument("--expires-in", type=int, default=600)
    email_match = sub.add_parser(
        "email-match",
        help="show safe metadata for the configured mailbox matching one recipient",
    )
    email_match.add_argument(
        "recipient",
        help="exact recipient address to match; values and message content are never returned",
    )
    site_grant = sub.add_parser(
        "grant-site", help="preview and grant matching credentials for one site"
    )
    site_grant.add_argument("agent_id")
    site_grant.add_argument("target_origin")
    site_grant.add_argument("purpose")
    site_modes = site_grant.add_mutually_exclusive_group()
    site_modes.add_argument(
        "--delivery", choices=["direct", "extension"],
        help="credential delivery: direct fields or an extension-only one-time handle",
    )
    site_modes.add_argument(
        "--operation",
        choices=["credential.use", "credential.read"],
        default="credential.use",
        action=_MarkExplicitAction,
        help="legacy grant operation (explicit selection skips the delivery prompt)",
    )
    site_grant.add_argument("--username")
    site_grant.add_argument("--expires-in", type=int, default=600)
    revoke = sub.add_parser("revoke-agent")
    revoke.add_argument("agent_id")
    agent = sub.add_parser(
        "agent-create", help="create an authenticated MCP agent identity"
    )
    agent.add_argument("agent_id")
    sub.add_parser("agents", help="list agent identities without tokens")
    creds = sub.add_parser("credentials")
    creds.add_argument("action", choices=["list"])
    creds.add_argument("--query")
    creds.add_argument("--target-origin")
    creds.add_argument("--username-hint")
    creds.add_argument("--limit", type=int, default=100)
    financial = sub.add_parser(
        "financial-records",
        aliases=["financial"],
        help="list approved payment and financial record metadata",
    )
    financial.add_argument("action", choices=["list"], nargs="?", default="list")
    financial.add_argument("--kind")
    financial.add_argument("--network")
    financial.add_argument("--last4")
    financial.add_argument("--expiration-month")
    financial.add_argument("--expiration-year")
    financial.add_argument("--query")
    financial.add_argument("--limit", type=int, default=100)
    card = sub.add_parser(
        "payment-card-add",
        help="review and store or replace one encrypted local payment card",
    )
    card.add_argument("record_id")
    card.add_argument("display_name")
    card.add_argument("--network")
    card.add_argument("--expiration-month")
    card.add_argument("--expiration-year")
    card.add_argument("--cardholder-name")
    card.add_argument("--billing-postal-code")
    card.add_argument("--origin", action="append", default=[])
    card.add_argument("--purpose", action="append", default=[])
    record_delete = sub.add_parser(
        "record-delete", help="delete one local profile, credential, or financial record"
    )
    record_delete.add_argument(
        "record_type", choices=["profile", "credential", "financial"]
    )
    record_delete.add_argument("record_id")
    email = sub.add_parser("email-add", help="configure a read-only IMAP account")
    email.add_argument("account_id")
    email.add_argument("host")
    email.add_argument("username")
    email.add_argument("--label")
    email.add_argument("--port", type=int, default=993)
    email.add_argument("--mailbox", default="INBOX")
    email.add_argument(
        "--oauth2",
        action="store_true",
        help="store an OAuth2 bearer token instead of an IMAP password",
    )
    gmail = sub.add_parser(
        "email-add-gmail", help="configure Gmail through a Google Desktop OAuth client"
    )
    gmail.add_argument("account_id")
    gmail.add_argument("username")
    gmail.add_argument(
        "--client-secrets",
        required=True,
        help="path to a user-supplied Google Cloud Desktop client JSON",
    )
    gmail.add_argument("--label")
    gmail.add_argument("--mailbox", default="INBOX")
    email_test = sub.add_parser(
        "email-test", help="test a saved read-only IMAP account"
    )
    email_test.add_argument("account_id")
    thunderbird_test = sub.add_parser(
        "email-test-thunderbird",
        help="test all selected Thunderbird OAuth IMAP accounts read-only",
    )
    thunderbird_test.add_argument(
        "--profile",
        help="Thunderbird profile directory (defaults to the active macOS profile)",
    )
    thunderbird_test.add_argument(
        "--account-id",
        action="append",
        help="test one exact Thunderbird account ID; repeat for multiple accounts",
    )
    thunderbird_import = sub.add_parser(
        "email-import-thunderbird",
        help="import verified Thunderbird OAuth connectors into Keychain",
    )
    thunderbird_import.add_argument(
        "--profile",
        help="Thunderbird profile directory (defaults to the active macOS profile)",
    )
    thunderbird_import.add_argument(
        "--account-id",
        action="append",
        help="import one exact Thunderbird account ID; repeat for multiple accounts",
    )
    thunderbird_import.add_argument(
        "--confirm-import",
        action="store_true",
        help="required acknowledgement after reviewing the metadata-only proposal",
    )
    messages = sub.add_parser(
        "messages-add", help="configure the local macOS Messages database"
    )
    messages.add_argument("--source-id", default="messages")
    messages.add_argument("--database")
    messages.add_argument("--label", default="Mac Messages")
    sources = sub.add_parser("sources", help="list configured sources")
    sources.add_argument("--kind")
    sources.add_argument("--query")
    sources.add_argument("--limit", type=int, default=100)
    keychain = sub.add_parser(
        "keychain", help="migrate legacy per-secret Keychain entries"
    )
    keychain.add_argument("action", choices=["migrate"])
    source_remove = sub.add_parser(
        "source-remove", help="disconnect a source and remove its local secret"
    )
    source_remove.add_argument("source_id")
    chrome = sub.add_parser("chrome", help="Chrome cold-start import")
    cs = chrome.add_subparsers(dest="chrome_command", required=True)
    cp = cs.add_parser("profiles")
    cp.add_argument("--root")
    ci = cs.add_parser("import")
    ci.add_argument("--profile-dir", required=True)
    ci.add_argument("--password-csv")
    ci.add_argument(
        "--allow-password-import",
        action="store_true",
        help="required acknowledgement for importing plaintext CSV secrets",
    )
    cpm = cs.add_parser(
        "password-manager",
        help="import Chrome Password Manager entries through macOS Keychain",
    )
    cpm.add_argument("--profile-dir", required=True)
    cpm.add_argument(
        "--confirm-import",
        action="store_true",
        help="required acknowledgement for importing encrypted Chrome credentials",
    )
    cpm.add_argument(
        "--preview",
        action="store_true",
        help="show a value-free reconciliation preview without writing",
    )
    cpm.add_argument(
        "--remove-stale",
        action="store_true",
        help="remove credentials deleted from Chrome after approval",
    )
    setup = sub.add_parser(
        "setup",
        aliases=["start", "onboard"],
        help="guided first-run wizard (aliases: start, onboard)",
        description=(
            "Set up a local vault in small, reviewable steps.\n"
            "The wizard never prints stored values or connector secrets.\n"
            "For ambiguity-aware Chrome seeding, use the agent-led intake guide."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  agent-autofill --db ~/.agent-autofill.sqlite3 start\n"
            "  agent-autofill --db ~/.agent-autofill.sqlite3 onboard --profile-dir "
            "~/Library/Application\\ Support/Google/Chrome/Default"
        ),
    )
    setup.add_argument("--profile-dir")
    setup.add_argument("--password-csv")
    sub.add_parser("audit", help="show redacted audit metadata")
    sub.add_parser("grants", help="show active and expired grants")
    sub.add_parser("inbox", help="show the local access review summary")
    quickstart = sub.add_parser(
        "quickstart",
        aliases=["status"],
        help="show the safe first-run summary and next action",
    )
    quickstart.add_argument(
        "--json", action="store_true", help="print the value-free status projection as JSON"
    )
    demo = sub.add_parser(
        "demo",
        help="tour the CLI with synthetic data in a memory-only vault",
        description=(
            "Preview the first-run experience without opening your database, "
            "Keychain, browser, mailbox, or Messages data."
        ),
    )
    demo.add_argument(
        "--json", action="store_true", help="print the synthetic projection as JSON"
    )
    data = sub.add_parser(
        "data",
        aliases=["inventory"],
        help="show a value-free inventory of stored data",
    )
    data.add_argument(
        "--json", action="store_true", help="print the metadata projection as JSON"
    )
    access = sub.add_parser(
        "access",
        aliases=["permissions"],
        help="show value-free agent and grant access metadata",
    )
    access.add_argument(
        "--json", action="store_true", help="print access metadata as JSON"
    )
    doctor = sub.add_parser(
        "doctor", help="check broker, Keychain, source, agent, and grant readiness"
    )
    doctor.add_argument(
        "--json", action="store_true", help="print the value-free diagnostics as JSON"
    )
    web = sub.add_parser("inbox-web", help="serve the local access review page")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    serve = sub.add_parser("serve", help="run the local JSON-lines MCP transport")
    serve.add_argument(
        "--service",
        help="bind the server to a stable local service identity recovered from Keychain",
    )
    connect = sub.add_parser(
        "connect", help="pair a local service with the installed Agent Autofill broker"
    )
    connect.add_argument("--service", required=True)
    fill = sub.add_parser(
        "fill-server", help="run the loopback browser credential-fill bridge"
    )
    fill.add_argument("--host", default="127.0.0.1")
    fill.add_argument("--port", type=int, default=8766)
    return p


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "serve":
        from .server import main as serve_main

        server_args = []
        if args.service:
            server_args.extend(["--service", args.service])
            if args.db != "agent-autofill.sqlite3":
                server_args.extend(["--db", args.db])
        else:
            server_args.extend(
                [
                    "--db",
                    args.db,
                    "--key-name",
                    Path(args.db).expanduser().absolute().as_posix(),
                ]
            )
        try:
            serve_main(server_args)
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
        return 0
    if args.command == "connect":
        from .local import connect_service

        path = None if args.db == "agent-autofill.sqlite3" else args.db
        try:
            connection = connect_service(args.service, path)
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
        try:
            print(
                json.dumps(
                    {
                        "service": connection.service_name,
                        "agent_id": connection.agent_id,
                        "database": str(connection.database_path),
                        "paired": True,
                    },
                    indent=2,
                )
            )
        finally:
            connection.close()
        return 0
    if args.command == "fill-server":
        from .fill_server import serve as fill_serve

        try:
            fill_serve(_broker(args.db), args.host, args.port)
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
        return 0
    if args.command == "demo":
        snapshot = _demo_snapshot()
        if args.json:
            print(json.dumps(snapshot, indent=2, sort_keys=True))
        else:
            _print_box(
                "✦ Agent Autofill · safe demo",
                "Synthetic values only; this run is memory-only.",
                "No user database, Keychain, browser, mailbox, or Messages data was opened.",
            )
            _print_status(snapshot, "Agent Autofill demo")
            _print_next("agent-autofill start")
        return 0
    if args.command == "email-test-thunderbird":
        try:
            profile = Path(args.profile).expanduser() if args.profile else discover_profile()
            run = test_profile(profile, account_ids=args.account_id)
        except ThunderbirdError as exc:
            raise SystemExit(str(exc)) from exc
        print(json.dumps(run.public_dict(), indent=2))
        return 0
    if args.command == "chrome" and args.chrome_command == "profiles":
        for path in discover_profiles(args.root):
            print(path)
        return 0
    try:
        broker = _broker(args.db)
    except RuntimeError as exc:
        if args.command in {"quickstart", "status", "doctor", "start", "onboard"}:
            snapshot = _startup_failure_snapshot(exc)
            _print_status(
                snapshot,
                "Agent Autofill doctor"
                if args.command == "doctor"
                else "Agent Autofill start"
                if args.command in {"start", "onboard"}
                else "Agent Autofill quickstart",
                as_json=getattr(args, "json", False),
            )
            if args.command in {"quickstart", "status"} and not getattr(args, "json", False):
                print(
                    "The summary is read-only. Resolve the credential-store "
                    "block before continuing with setup or data access."
                )
            elif args.command in {"start", "onboard"} and not getattr(args, "json", False):
                _print_fix("Unlock the login Keychain, then rerun `agent-autofill start`.")
            return 0
        raise SystemExit(str(exc)) from exc
    if args.command == "init":
        print(f"Initialized encrypted broker database at {Path(args.db).absolute()}")
    elif args.command == "profile":
        if args.profile_command == "set":
            broker.put_profile(
                args.field_id,
                args.category,
                args.display_name,
                args.value,
                args.sensitivity,
                args.origin,
                args.purpose,
            )
            print(f"Stored profile field {args.field_id}")
        elif args.profile_command in {"add", "edit"}:
            _interactive_profile(broker, args)
        else:
            print(
                json.dumps(
                    broker.list_profile_fields(
                        category=args.category,
                        query=args.query,
                        limit=args.limit,
                    ),
                    indent=2,
                )
            )
    elif args.command == "grant":
        g = broker.grant(
            args.agent_id,
            args.operation,
            args.resource_scope,
            args.origin,
            args.purpose,
            args.expires_in,
        )
        print(json.dumps(g.__dict__, indent=2))
    elif args.command == "grant-email":
        positional_all = args.recipient == "all"
        if args.all and positional_all:
            raise SystemExit("grant-email cannot combine RECIPIENT=all with --all")
        if not args.all and not positional_all and not args.recipient:
            raise SystemExit("grant-email requires exactly one of RECIPIENT or --all")
        if args.all or positional_all:
            g = broker.grant_email_all(
                args.agent_id,
                args.operation,
                args.origin,
                args.purpose,
                args.expires_in,
            )
        else:
            g = broker.grant_email_recipient(
                args.agent_id,
                args.operation,
                args.recipient,
                args.origin,
                args.purpose,
                args.expires_in,
            )
        print(json.dumps(g.__dict__, indent=2))
        if args.operation in {"email.otp", "email.verification_link"}:
            response_name = (
                "an authentication code"
                if args.operation == "email.otp"
                else "a verification link"
            )
            prompt = f"Consent required before requesting {response_name}: confirm "
            print(prompt + "the exact recipient, target origin, purpose, and freshness window.")
            if args.operation == "email.verification_link":
                print(
                    "Next step (authenticated MCP): "
                    "request_verification_link_for_recipient(recipient, target_origin, "
                    "purpose, max_age_seconds=300)."
                )
            else:
                print(
                    "Next step (authenticated MCP): request_auth_code_for_recipient("
                    "recipient, target_origin, purpose, max_age_seconds=300)."
                )
                print(
                    "Verification-link flows use the separate "
                    "request_verification_link_for_recipient operation and "
                    "email.verification_link grant."
                )
            print(
                "The scoped response is transient; this CLI does not print or store "
                + ("the authentication code." if args.operation == "email.otp" else "the verification URL.")
            )
    elif args.command == "email-match":
        matches = _safe_email_match_metadata(
            broker.match_email_sources(args.recipient)
        )
        print(json.dumps(matches, indent=2))
        if matches:
            print(
                "Matched mailbox metadata only. Continue with explicit recipient, "
                "origin, and purpose consent before requesting a verification link."
            )
        else:
            print("No configured mailbox metadata matched; no verification link requested.")
    elif args.command == "grant-site":
        # ``operation`` keeps its historical parser default for compatibility,
        # but only an explicitly supplied legacy flag should bypass the new
        # delivery choice.  ``--delivery`` is mutually exclusive with it at
        # the argparse boundary.
        legacy_operation = (
            args.operation if getattr(args, "_operation_explicit", False) else None
        )
        _configure_site_grants(
            broker,
            args.agent_id,
            args.target_origin,
            args.purpose,
            legacy_operation,
            args.username,
            args.expires_in,
            delivery=args.delivery,
        )
    elif args.command == "revoke-agent":
        broker.revoke_agent(args.agent_id)
        print(f"Revoked grants for {args.agent_id}")
    elif args.command == "agent-create":
        token = broker.create_agent(args.agent_id)
        print(
            f"Agent {args.agent_id} created. Save this token; it will not be shown again:\n{token}"
        )
    elif args.command == "agents":
        print(json.dumps(broker.list_agents(), indent=2))
    elif args.command == "credentials":
        print(
            json.dumps(
                broker.list_credentials(
                    query=args.query,
                    target_origin=args.target_origin,
                    username_hint=args.username_hint,
                    limit=args.limit,
                ),
                indent=2,
            )
        )
    elif args.command in {"financial-records", "financial"}:
        hints = {
            key: value
            for key, value in {
                "network": args.network,
                "last4": args.last4,
                "expiration_month": args.expiration_month,
                "expiration_year": args.expiration_year,
            }.items()
            if value is not None
        }
        records = (
            broker.match_financial_records(kind=args.kind, matching_hints=hints)
            if args.kind or hints
            else broker.list_financial_records(
                query=args.query,
                limit=args.limit,
            )
        )
        if args.kind or hints:
            records = records[: max(1, min(args.limit, 100))]
        print(json.dumps(records, indent=2))
    elif args.command == "payment-card-add":
        _configure_payment_card(
            broker,
            args.record_id,
            args.display_name,
            network=args.network,
            expiration_month=args.expiration_month,
            expiration_year=args.expiration_year,
            cardholder_name=args.cardholder_name,
            billing_postal_code=args.billing_postal_code,
            allowed_origins=args.origin,
            allowed_purposes=args.purpose,
        )
    elif args.command == "record-delete":
        broker.delete_record(args.record_type, args.record_id)
        print(f"Deleted {args.record_type} record {args.record_id}")
    elif args.command == "keychain":
        migrated = getattr(broker, "_keyring_migrated_count", 0)
        print(f"Consolidated {migrated} legacy connector secrets into the Agent Autofill vault.")
    elif args.command == "sources":
        print(
            json.dumps(
                broker.list_sources(
                    kind=args.kind,
                    query=args.query,
                    limit=args.limit,
                ),
                indent=2,
            )
        )
    elif args.command == "source-remove":
        config = broker.store.get("connection", args.source_id)
        if config and config.get("secret_name"):
            try:
                _keyring_for(broker).delete_secret(config["secret_name"])
            except Exception as exc:
                raise SystemExit(
                    "Could not remove Keychain secret; source remains configured"
                ) from exc
        broker.remove_source(args.source_id)
        print(f"Removed source {args.source_id}")
    elif args.command == "email-add":
        keyring = _keyring_for(broker)
        secret = getpass.getpass(
            f"IMAP {'OAuth2 token' if args.oauth2 else 'password'} for {args.username}@{args.host} (stored in Keychain): "
        )
        config = _configure_imap(
            broker,
            args.db,
            args.account_id,
            args.host,
            args.username,
            args.port,
            args.mailbox,
            secret,
            args.oauth2,
            args.label,
            keyring,
        )
        print(
            "Configured read-only IMAP source "
            f"{config['source_id']} ({config['host']}:{config['port']}/"
            f"{config['mailbox']}, {config['username']}, {config['auth_kind']})"
        )
    elif args.command == "email-add-gmail":
        config = _configure_gmail(
            broker,
            args.db,
            args.account_id,
            args.username,
            args.client_secrets,
            args.mailbox,
            args.label,
            _keyring_for(broker),
        )
        print(
            "Configured read-only Gmail source "
            f"{config['source_id']} ({config['host']}:{config['port']}/"
            f"{config['mailbox']}, {config['username']}, {config['auth_kind']})"
        )
    elif args.command == "email-test":
        config = _test_saved_imap(broker, args.account_id, _keyring_for(broker))
        print(
            "Read-only IMAP source is reachable "
            f"{config['source_id']} ({config['host']}:{config.get('port', 993)}/"
            f"{config.get('mailbox', 'INBOX')}, {config['username']}, "
            f"{config.get('auth_kind', 'password')})"
        )
    elif args.command == "email-import-thunderbird":
        try:
            profile = Path(args.profile).expanduser() if args.profile else discover_profile()
            imported = _import_thunderbird(
                broker,
                args.db,
                profile,
                args.account_id,
                _keyring_for(broker),
                confirm_import=args.confirm_import,
            )
        except ThunderbirdError as exc:
            raise SystemExit(str(exc)) from exc
        print(json.dumps({"accounts": imported}, indent=2))
    elif args.command == "messages-add":
        broker.store.put(
            "connection",
            args.source_id,
            {
                "source_id": args.source_id,
                "kind": "messages",
                "label": args.label,
                "database": args.database,
            },
        )
        broker.add_source(args.source_id, "messages", args.label)
        print(f"Configured read-only Messages source {args.source_id}")
    elif args.command == "audit":
        print(json.dumps(broker.store.audits(), indent=2))
    elif args.command == "grants":
        print(json.dumps(broker.list_grants(), indent=2))
    elif args.command in {"quickstart", "status"}:
        _print_status(_status_snapshot(broker), "Agent Autofill quickstart", as_json=args.json)
        if not args.json:
            _print_next("agent-autofill start (or `profile add` for one field)")
    elif args.command == "doctor":
        _print_status(_status_snapshot(broker), "Agent Autofill doctor", as_json=args.json)
    elif args.command == "inbox":
        print(
            json.dumps(
                {
                    "sources": broker.list_sources(),
                    "profile_fields": broker.list_profile_fields(),
                    "profile_groups": broker.list_profile_groups(),
                    "credentials": broker.list_credentials(),
                    "financial_records": broker.list_financial_records(),
                    "grants": broker.list_grants(),
                    "recent_audit": broker.store.audits()[-20:],
                },
                indent=2,
            )
        )
    elif args.command in {"data", "inventory"}:
        snapshot = _status_snapshot(broker)
        if args.json:
            print(
                json.dumps(
                    {
                        "stored": snapshot["stored"],
                        "sources": snapshot["sources"],
                        "profile_fields": broker.list_profile_fields(),
                        "credentials": broker.list_credentials(),
                        "financial_records": broker.list_financial_records(),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            _print_status(snapshot, "Agent Autofill data")
            _print_next("agent-autofill inbox-web for the local review dashboard")
    elif args.command in {"access", "permissions"}:
        grants = broker.list_grants()
        agents = broker.list_agents()
        if args.json:
            print(
                json.dumps(
                    {"agents": agents, "grants": grants},
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            _print_box(
                "✦ Access review",
                f"{len(agents)} agent identities · "
                f"{sum(bool(item.get('active')) for item in grants)} active grants",
                "Values, tokens, and secrets are omitted.",
            )
            print(json.dumps({"agents": agents, "grants": grants}, indent=2))
            _print_next("agent-autofill inbox-web to review or revoke access")
    elif args.command == "inbox-web":
        from .inbox import serve

        if args.host not in {"127.0.0.1", "localhost", "::1"}:
            raise SystemExit("inbox-web only permits loopback hosts")
        print(f"Agent Inbox available at http://{args.host}:{args.port}/")
        serve(broker, args.host, args.port)
    elif args.command == "chrome":
        if args.chrome_command == "password-manager":
            if args.preview:
                print(
                    json.dumps(
                        preview_chrome_password_manager(args.profile_dir, broker),
                        indent=2,
                    )
                )
                return 0
            if not args.confirm_import:
                raise SystemExit(
                    "Refusing Chrome Password Manager sync: re-run with --confirm-import"
                )
            print(
                json.dumps(
                    sync_chrome_password_manager(
                        args.profile_dir,
                        broker,
                        approved=True,
                        remove_stale=args.remove_stale,
                    ),
                    indent=2,
                )
            )
            return 0
        if args.password_csv and not args.allow_password_import:
            raise SystemExit(
                "Refusing password import: re-run with --allow-password-import after confirming the CSV is user-exported"
            )
        result = cold_start(args.profile_dir, broker, args.password_csv)
        print(json.dumps(result, indent=2))
    elif args.command in {"setup", "start", "onboard"}:
        if args.command in {"start", "onboard"}:
            _print_onboarding_banner()
        agent_id = input(
            "Agent identity to authorize (blank to configure later): "
        ).strip()
        if agent_id and broker.store.get("agent", agent_id) is None:
            token = broker.create_agent(agent_id)
            print(
                f"Agent {agent_id} token (save once; it will not be shown again):\n{token}"
            )
        profile_dir = _select_setup_profile(args.profile_dir)
        result = {"autofill_fields": 0, "login_metadata": 0, "passwords": 0}
        if profile_dir:
            csv_path = (
                args.password_csv
                or input("Path to Chrome password CSV (blank to skip): ").strip()
                or None
            )
            if csv_path:
                answer = input(
                    "Import passwords into encrypted local vault? type IMPORT to continue: "
                )
                if answer != "IMPORT":
                    raise SystemExit("Password import cancelled")
            result = cold_start(profile_dir, broker, csv_path)
        if (
            input("Configure a read-only IMAP mailbox now? [y/N] ").strip().lower()
            == "y"
        ):
            account_id = input("Account id: ").strip()
            host = input("IMAP host: ").strip()
            username = input("Username: ").strip()
            password = getpass.getpass("IMAP password (stored in Keychain): ")
            _configure_imap(
                broker,
                args.db,
                account_id,
                host,
                username,
                993,
                "INBOX",
                password,
                keyring=_keyring_for(broker),
            )
        if input("Configure local Mac Messages access? [y/N] ").strip().lower() == "y":
            database = (
                input("Messages chat.db path (blank for default): ").strip() or None
            )
            broker.store.put(
                "connection",
                "messages",
                {
                    "source_id": "messages",
                    "kind": "messages",
                    "label": "Mac Messages",
                    "database": database,
                },
            )
            broker.add_source("messages", "messages", "Mac Messages")
        if agent_id:
            _configure_setup_grants(broker, agent_id)
        print(json.dumps(result, indent=2))
        if args.command in {"start", "onboard"}:
            _print_next(
                f"agent-autofill --db {args.db} status; open `inbox-web` for a visual inventory"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
