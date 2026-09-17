from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets
from urllib.parse import urlparse
from .models import Grant, EmailMessage, TextMessage, now_iso
from .storage import Store


class BrokerError(Exception):
    pass


class AuthorizationError(BrokerError):
    pass


class CredentialUseError(BrokerError):
    pass


def _bounded_limit(value) -> int:
    try:
        return max(1, min(int(value), 100))
    except (TypeError, ValueError):
        raise BrokerError("limit must be an integer")


def _origin_matches(left: str | None, right: str | None) -> bool:
    if left is None:
        return right is None
    if right is None:
        return False

    def parts(value):
        parsed = urlparse(value if "://" in value else "//" + value)
        try:
            port = parsed.port
        except ValueError:
            port = -1
        return (parsed.scheme.lower(), (parsed.hostname or "").lower(), port)

    a, b = parts(left), parts(right)
    return a == b or (not a[0] or not b[0]) and a[1:] == b[1:]


def _hostname_token_matches(text: str, hostname: str) -> bool:
    """Match a hostname as a complete token in provider text.

    URL and email delimiters such as ``/``, ``?``, ``@``, ``<`` and ``>`` are
    valid boundaries. Hostname characters are not, so ``notexample.com`` and
    ``example.com.evil`` cannot accidentally bind an OTP to ``example.com``.
    """

    if not hostname:
        return False
    boundary = r"A-Za-z0-9_.-"
    pattern = rf"(?<![{boundary}]){re.escape(hostname)}(?![{boundary}])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


class Broker:
    def __init__(self, store: Store | None = None):
        self.store = store or Store()
        self.store.purge_legacy_otp_markers()
        self.email: dict[str, list[EmailMessage]] = {}
        self.texts: list[TextMessage] = []
        self.email_adapters: dict[str, object] = {}
        self.unavailable_email_sources: set[str] = set()
        self.removed_email_sources: set[str] = set()
        self.message_adapters: dict[str, object] = {}
        self.sources: dict[str, dict] = {}
        for row in self.store.db.execute("select key from kv where namespace='source'"):
            item = self.store.get("source", row["key"])
            if item:
                self.sources[row["key"]] = item

    def add_source(self, source_id: str, kind: str, label: str) -> None:
        self.removed_email_sources.discard(source_id)
        self.sources[source_id] = {"source_id": source_id, "kind": kind, "label": label}
        self.store.put("source", source_id, self.sources[source_id])

    def register_email_adapter(
        self, account_id: str, adapter: object, label: str | None = None
    ) -> None:
        self.email_adapters[account_id] = adapter
        self.unavailable_email_sources.discard(account_id)
        self.removed_email_sources.discard(account_id)
        self.add_source(account_id, "email", label or account_id)

    def mark_email_unavailable(self, account_id: str) -> None:
        """Keep a saved source visible while refusing unbacked reads."""

        self.unavailable_email_sources.add(account_id)

    def register_message_adapter(
        self, source_id: str, adapter: object, label: str | None = None
    ) -> None:
        self.message_adapters[source_id] = adapter
        self.add_source(source_id, "messages", label or source_id)

    def remove_source(self, source_id: str) -> None:
        # Keep a tombstone even when the persisted source was unavailable and
        # no adapter was reconstructed. This prevents stale in-memory fallback
        # messages from becoming readable again after removal.
        self.removed_email_sources.add(source_id)
        self.sources.pop(source_id, None)
        self.email_adapters.pop(source_id, None)
        self.unavailable_email_sources.discard(source_id)
        self.message_adapters.pop(source_id, None)
        self.store.db.execute(
            "delete from kv where namespace in ('source','connection') and key=?",
            (source_id,),
        )
        self.store.db.commit()
        for row in self.store.db.execute(
            "select key,value from kv where namespace='grant'"
        ).fetchall():
            grant = self.store._open(row["value"])
            if grant["resource_scope"] == source_id:
                grant["revoked"] = True
                self.store.put("grant", row["key"], grant)

    def put_profile(
        self,
        field_id: str,
        category: str,
        display_name: str,
        value,
        sensitivity: str = "normal",
        allowed_origins: list[str] | None = None,
        allowed_purposes: list[str] | None = None,
    ) -> None:
        self.store.put(
            "profile",
            field_id,
            {
                "field_id": field_id,
                "category": category,
                "display_name": display_name,
                "value": value,
                "sensitivity": sensitivity,
                "allowed_origins": allowed_origins or [],
                "allowed_purposes": allowed_purposes or [],
                "updated_at": now_iso(),
            },
        )

    def put_credential(
        self,
        credential_id: str,
        name: str,
        origin: str,
        username: str,
        password: str,
        note: str = "",
    ) -> None:
        self.store.put(
            "credential",
            credential_id,
            {
                "credential_id": credential_id,
                "name": name,
                "origin": origin,
                "username": username,
                "password": password,
                "note": note,
                "created_at": now_iso(),
            },
        )

    def list_credentials(self) -> list[dict]:
        result = []
        for row in self.store.db.execute(
            "select key from kv where namespace='credential'"
        ):
            item = self.store.get("credential", row["key"])
            if item:
                result.append(
                    {
                        k: item[k]
                        for k in (
                            "credential_id",
                            "name",
                            "origin",
                            "username",
                            "created_at",
                        )
                    }
                )
        return result

    def request_credential_use(
        self,
        agent_id: str,
        credential_id: str,
        target_origin: str,
        purpose: str,
        expires_in: int = 60,
    ) -> dict:
        if not target_origin or not purpose:
            raise CredentialUseError("target_origin and purpose are required")
        if expires_in < 1 or expires_in > 300:
            raise CredentialUseError(
                "credential handle lifetime must be between 1 and 300 seconds"
            )
        self._authorized(
            agent_id, "credential.use", credential_id, target_origin, purpose
        )
        item = self.store.get("credential", credential_id)
        if item is None:
            raise CredentialUseError("credential not found")
        requested_host = (urlparse(target_origin).hostname or target_origin).lower()
        credential_host = (urlparse(item["origin"]).hostname or item["origin"]).lower()
        if requested_host != credential_host:
            raise CredentialUseError("target origin does not match credential origin")
        handle = secrets.token_urlsafe(32)
        self.store.put(
            "credential-handle",
            hashlib.sha256(handle.encode()).hexdigest(),
            {
                "agent_id": agent_id,
                "credential_id": credential_id,
                "purpose": purpose,
                "target_origin": target_origin,
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                ).isoformat(),
                "consumed": False,
            },
        )
        self.store.audit(
            agent_id,
            "credential.use",
            credential_id,
            now_iso(),
            {"target_origin": target_origin, "purpose": purpose},
        )
        return {
            "handle": handle,
            "username": item["username"],
            "origin": item["origin"],
            "expires_in": expires_in,
            "provenance": {
                "source": "local-credential-vault",
                "credential_id": credential_id,
            },
        }

    def consume_credential_handle(self, handle: str, agent_id: str) -> dict:
        key = hashlib.sha256(handle.encode()).hexdigest()
        item = self.store.get("credential-handle", key)
        if (
            not item
            or item["agent_id"] != agent_id
            or item["consumed"]
            or datetime.fromisoformat(item["expires_at"]) <= datetime.now(timezone.utc)
        ):
            raise CredentialUseError(
                "credential handle is invalid, expired, or already consumed"
            )
        credential = self.store.get("credential", item["credential_id"])
        if credential is None:
            raise CredentialUseError("credential not found")
        item["consumed"] = True
        self.store.put("credential-handle", key, item)
        return {
            "username": credential["username"],
            "password": credential["password"],
            "origin": credential["origin"],
        }

    def grant(
        self,
        agent_id: str,
        operation: str,
        resource_scope: str,
        target_origin: str | None,
        purpose: str | None,
        expires_in: int = 600,
    ) -> Grant:
        if expires_in < 1 or expires_in > 86400:
            raise BrokerError("expires_in must be between 1 and 86400 seconds")
        issued = datetime.now(timezone.utc)
        g = Grant(
            agent_id,
            operation,
            resource_scope,
            target_origin,
            purpose,
            issued.isoformat(),
            (issued + timedelta(seconds=expires_in)).isoformat(),
        )
        self.store.put("grant", secrets.token_hex(12), g.__dict__)
        return g

    def create_agent(self, agent_id: str) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        self.store.put(
            "agent",
            agent_id,
            {
                "agent_id": agent_id,
                "token_hash": token_hash,
                "created_at": now_iso(),
                "revoked": False,
            },
        )
        return token

    def authenticate_agent(self, agent_id: str, token: str | None) -> bool:
        if not token:
            return False
        item = self.store.get("agent", agent_id)
        return bool(
            item
            and not item.get("revoked", False)
            and hmac.compare_digest(
                item["token_hash"], hashlib.sha256(token.encode()).hexdigest()
            )
        )

    def list_agents(self) -> list[dict]:
        result = []
        for row in self.store.db.execute("select key from kv where namespace='agent'"):
            item = self.store.get("agent", row["key"])
            if item:
                result.append(
                    {
                        "agent_id": item["agent_id"],
                        "created_at": item["created_at"],
                        "revoked": item.get("revoked", False),
                    }
                )
        return result

    def revoke_agent(self, agent_id: str) -> None:
        for row in self.store.db.execute(
            "select key,value from kv where namespace='grant'"
        ).fetchall():
            d = self.store._open(row["value"])
            if d["agent_id"] == agent_id:
                d["revoked"] = True
                self.store.put("grant", row["key"], d)
        item = self.store.get("agent", agent_id)
        if item:
            item["revoked"] = True
            self.store.put("agent", agent_id, item)

    def list_grants(self) -> list[dict]:
        result = []
        for row in self.store.db.execute(
            "select key,value from kv where namespace='grant'"
        ):
            grant = self.store._open(row["value"])
            result.append(
                {"grant_id": row["key"], **grant, "active": Grant(**grant).active()}
            )
        return result

    def _authorized(
        self, agent_id, operation, resource, origin=None, purpose=None
    ) -> Grant:
        rows = self.store.db.execute(
            "select value from kv where namespace='grant'"
        ).fetchall()
        for row in rows:
            d = self.store._open(row["value"])
            if (
                d["agent_id"] == agent_id
                and d["operation"] == operation
                and d["resource_scope"] in ("*", resource)
                and (
                    d["target_origin"] is None
                    or _origin_matches(d["target_origin"], origin)
                )
                and d["purpose"] in (None, purpose)
                and Grant(**d).active()
            ):
                return Grant(**d)
        raise AuthorizationError(f"no active grant for {operation}:{resource}")

    def list_sources(self):
        return list(self.sources.values())

    def _ensure_email_source(self, account_id: str) -> object | None:
        if account_id in self.removed_email_sources:
            raise BrokerError("email account is not configured")
        if account_id in self.unavailable_email_sources:
            raise BrokerError("email source is unavailable; run email-test")
        if account_id in self.email_adapters:
            return self.email_adapters[account_id]
        if account_id in self.email:
            if self.sources.get(account_id, {}).get("kind") not in (None, "email"):
                raise BrokerError("email account is not configured")
            return None
        if self.sources.get(account_id, {}).get("kind") == "email":
            raise BrokerError("email source is unavailable; run email-test")
        raise BrokerError("email account is not configured")

    def list_profile_fields(self):
        return [
            {
                k: v
                for k, v in (self.store.get("profile", key) or {}).items()
                if k != "value"
            }
            for key in [
                r["key"]
                for r in self.store.db.execute(
                    "select key from kv where namespace='profile'"
                )
            ]
        ]

    def request_profile_fields(self, agent_id, field_ids, target_origin, purpose):
        if not target_origin or not purpose:
            raise BrokerError("target_origin and purpose are required")
        if not field_ids or len(field_ids) > 100:
            raise BrokerError("field_ids must contain 1 to 100 fields")
        out = {}
        for fid in field_ids:
            self._authorized(agent_id, "profile.read", fid, target_origin, purpose)
            item = self.store.get("profile", fid)
            if item is None:
                raise BrokerError(f"unknown profile field: {fid}")
            if item["allowed_origins"] and not any(
                _origin_matches(allowed, target_origin)
                for allowed in item["allowed_origins"]
            ):
                raise AuthorizationError("origin not allowed for profile field")
            if item["allowed_purposes"] and purpose not in item["allowed_purposes"]:
                raise AuthorizationError("purpose not allowed for profile field")
            out[fid] = {
                "value": item["value"],
                "provenance": {"source": "local-profile", "field_id": fid},
            }
        self.store.audit(
            agent_id,
            "profile.read",
            ",".join(field_ids),
            now_iso(),
            {"target_origin": target_origin, "purpose": purpose},
        )
        return out

    def search_email(self, agent_id, account_id, query, limit=20):
        limit = _bounded_limit(limit)
        self._authorized(agent_id, "email.search", account_id)
        adapter = self._ensure_email_source(account_id)
        q = query.lower()
        rows = [
            m
            for m in self.email.get(account_id, [])
            if q in (m.subject + " " + m.sender + " " + m.body).lower()
        ][:limit]
        if adapter is not None:
            rows = adapter.search(query, limit)
        self.store.audit(
            agent_id,
            "email.search",
            account_id,
            now_iso(),
            {
                "query_hash": hashlib.sha256(query.encode()).hexdigest(),
                "count": len(rows),
            },
        )
        return [
            m.__dict__
            | {"provenance": {"account_id": account_id, "message_id": m.message_id}}
            for m in rows
        ]

    def get_email_message(self, agent_id, account_id, message_id):
        self._authorized(agent_id, "email.read", account_id)
        adapter = self._ensure_email_source(account_id)
        if adapter is not None:
            m = adapter.get(message_id)
            self.store.audit(
                agent_id,
                "email.read",
                account_id,
                now_iso(),
                {"message_id": message_id},
            )
            return m.__dict__ | {
                "provenance": {"account_id": account_id, "message_id": message_id}
            }
        for m in self.email.get(account_id, []):
            if m.message_id == message_id:
                self.store.audit(
                    agent_id,
                    "email.read",
                    account_id,
                    now_iso(),
                    {"message_id": message_id},
                )
                return m.__dict__ | {
                    "provenance": {"account_id": account_id, "message_id": message_id}
                }
        raise BrokerError("message not found")

    def request_auth_code(
        self, agent_id, account_id, target_origin, purpose, max_age_seconds=300
    ):
        if not target_origin or not purpose:
            raise BrokerError("target_origin and purpose are required")
        try:
            max_age_seconds = int(max_age_seconds)
        except (TypeError, ValueError):
            raise BrokerError("max_age_seconds must be an integer")
        if max_age_seconds < 1 or max_age_seconds > 3600:
            raise BrokerError("max_age_seconds must be between 1 and 3600 seconds")
        self._authorized(agent_id, "email.otp", account_id, target_origin, purpose)
        self.store.purge_legacy_otp_markers()
        adapter = self._ensure_email_source(account_id)
        now = datetime.now(timezone.utc)
        candidates = []
        messages = (
            adapter.recent(max_age_seconds)
            if adapter is not None and hasattr(adapter, "recent")
            else self.email.get(account_id, [])
        )
        for m in messages:
            received_at = datetime.fromisoformat(m.received_at)
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=timezone.utc)
            age = (now - received_at).total_seconds()
            if 0 <= age <= max_age_seconds:
                codes = re.findall(r"\b\d{4,8}\b", m.body)
                candidates.extend((m, c) for c in codes)
        parsed_origin = urlparse(
            target_origin if "://" in target_origin else "//" + target_origin
        )
        domain = (parsed_origin.hostname or "").lower().rstrip(".")
        if len(candidates) != 1:
            raise BrokerError(
                "no unambiguous recent authentication code associated with target origin"
            )
        bound = [
            item
            for item in candidates
            if _hostname_token_matches(
                item[0].sender + " " + item[0].subject + " " + item[0].body,
                domain,
            )
        ]
        if len(bound) != 1:
            raise BrokerError(
                "recent authentication code is not bound to target origin"
            )
        candidates = bound
        m, code = candidates[0]
        used_key = hashlib.sha256(
            f"{agent_id}\0{account_id}\0{m.message_id}".encode()
        ).hexdigest()
        if not self.store.put_if_absent(
            "otp-consumed", used_key, {"used_at": now_iso()}
        ):
            raise BrokerError("authentication code already consumed")
        self.store.audit(
            agent_id,
            "email.otp",
            account_id,
            now_iso(),
            {
                "message_id": m.message_id,
                "target_origin": target_origin,
                "purpose": purpose,
            },
        )
        return {
            "code": code,
            "provenance": {
                "source": "email",
                "account_id": account_id,
                "message_id": m.message_id,
                "received_at": m.received_at,
            },
        }

    def search_messages(self, agent_id, conversation_scope, query, limit=20):
        limit = _bounded_limit(limit)
        self._authorized(agent_id, "messages.search", conversation_scope)
        q = query.lower()
        rows = []
        for source_id, adapter in self.message_adapters.items():
            if hasattr(adapter, "search"):
                rows.extend(adapter.search(query, conversation_scope, limit))
        rows.extend(
            [
                m
                for m in self.texts
                if (
                    conversation_scope == "*" or m.conversation_id == conversation_scope
                )
                and q in (m.body + m.sender).lower()
            ][:limit]
        )
        rows = rows[:limit]
        self.store.audit(
            agent_id,
            "messages.search",
            conversation_scope,
            now_iso(),
            {
                "query_hash": hashlib.sha256(query.encode()).hexdigest(),
                "count": len(rows),
            },
        )
        return [
            m.__dict__
            | {
                "provenance": {
                    "source": "messages",
                    "conversation_id": m.conversation_id,
                    "message_id": m.message_id,
                }
            }
            for m in rows
        ]

    def list_conversations(self, agent_id, source_id="*"):
        self._authorized(agent_id, "messages.list", source_id)
        result = []
        for adapter in self.message_adapters.values():
            if hasattr(adapter, "list_conversations"):
                result.extend(adapter.list_conversations())
        result.extend(
            {"conversation_id": cid, "source": "memory"}
            for cid in sorted({m.conversation_id for m in self.texts})
            if source_id in ("*", cid)
        )
        self.store.audit(
            agent_id, "messages.list", source_id, now_iso(), {"count": len(result)}
        )
        return result

    def get_message(self, agent_id, message_id):
        for adapter in self.message_adapters.values():
            try:
                m = adapter.get(message_id)
                self._authorized(agent_id, "messages.read", m.conversation_id)
                self.store.audit(
                    agent_id,
                    "messages.read",
                    m.conversation_id,
                    now_iso(),
                    {"message_id": message_id},
                )
                return m.__dict__ | {
                    "provenance": {
                        "source": "messages",
                        "conversation_id": m.conversation_id,
                        "message_id": message_id,
                    }
                }
            except KeyError:
                continue
        for m in self.texts:
            if m.message_id == message_id:
                self._authorized(agent_id, "messages.read", m.conversation_id)
                self.store.audit(
                    agent_id,
                    "messages.read",
                    m.conversation_id,
                    now_iso(),
                    {"message_id": message_id},
                )
                return m.__dict__ | {
                    "provenance": {
                        "source": "messages",
                        "conversation_id": m.conversation_id,
                        "message_id": message_id,
                    }
                }
        raise BrokerError("message not found")
