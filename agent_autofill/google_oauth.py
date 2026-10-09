"""Google installed-app OAuth for the read-only Gmail IMAP adapter.

The module deliberately owns only the OAuth boundary.  It opens a short-lived
loopback callback listener, validates the authorization response, exchanges a
PKCE authorization code, and keeps the resulting access token in memory.  A
Keychain payload contains only the client material and refresh token needed by
the next process; access tokens are never serialised.

No Google client is bundled with Agent Autofill.  Callers must supply a Desktop
application client JSON downloaded from their own Google Cloud project.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import http.server
import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
import webbrowser


AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
MICROSOFT_TOKEN_ENDPOINT = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
GMAIL_SCOPE = "https://mail.google.com/"
LOOPBACK_HOST = "127.0.0.1"
# Desktop clients use the loopback redirect URI at the root path.  Keeping the
# path fixed (rather than accepting arbitrary local URLs) also avoids a
# redirect_uri mismatch with Google's installed-app registration.
CALLBACK_PATH = "/"
KEYCHAIN_PAYLOAD_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 300.0
TOKEN_HTTP_TIMEOUT_SECONDS = 30.0
TOKEN_REFRESH_SKEW_SECONDS = 60.0


class GoogleOAuthError(RuntimeError):
    """Base class for safe, operator-facing Google OAuth failures."""


class GoogleOAuthConfigurationError(GoogleOAuthError):
    """The supplied client JSON or saved OAuth state is unusable."""


class GoogleOAuthAuthorizationError(GoogleOAuthError):
    """The browser authorization or loopback callback failed safely."""


class GoogleOAuthTokenError(GoogleOAuthError):
    """The authorization-code or refresh-token exchange failed safely."""


@dataclass(frozen=True)
class GoogleOAuthClient:
    """The small subset of a Desktop client JSON needed by Google OAuth."""

    client_id: str
    client_secret: str | None = field(default=None, repr=False)

    @classmethod
    def from_json(cls, value: Mapping[str, Any] | str | bytes) -> "GoogleOAuthClient":
        """Parse a Google Desktop client payload without retaining extra fields."""

        if isinstance(value, (str, bytes)):
            try:
                value = json.loads(value)
            except (TypeError, ValueError) as exc:
                raise GoogleOAuthConfigurationError(
                    "Google OAuth client JSON is invalid"
                ) from exc
        if not isinstance(value, Mapping):
            raise GoogleOAuthConfigurationError(
                "Google OAuth client JSON must be an object"
            )
        installed = value.get("installed")
        if not isinstance(installed, Mapping):
            raise GoogleOAuthConfigurationError(
                "Google OAuth client JSON must contain an installed Desktop client"
            )
        client_id = installed.get("client_id")
        if not isinstance(client_id, str) or not client_id.strip():
            raise GoogleOAuthConfigurationError(
                "Google OAuth Desktop client id is missing"
            )
        client_secret = installed.get("client_secret")
        if client_secret is not None and not isinstance(client_secret, str):
            raise GoogleOAuthConfigurationError(
                "Google OAuth Desktop client secret is invalid"
            )
        return cls(client_id.strip(), client_secret or None)

    @classmethod
    def from_file(cls, path: str | Path) -> "GoogleOAuthClient":
        """Read and validate a user-supplied Desktop client JSON file."""

        try:
            raw = Path(path).expanduser().read_bytes()
        except (OSError, TypeError, ValueError) as exc:
            raise GoogleOAuthConfigurationError(
                "Could not read Google OAuth client JSON"
            ) from exc
        return cls.from_json(raw)

    # Friendly aliases make the boundary easy to use from CLI tests and small
    # integrations without exposing the original Google JSON object.
    load = from_file
    load_file = from_file


@dataclass(frozen=True)
class GoogleOAuthToken:
    """An in-memory token response; secret fields are hidden from repr output."""

    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    expires_in: float = field(default=3600.0, repr=False)
    token_type: str = "Bearer"


HttpPost = Callable[[str, Mapping[str, str], float], Mapping[str, Any] | str | bytes]


def _safe_response_json(raw: Mapping[str, Any] | str | bytes) -> Mapping[str, Any]:
    if isinstance(raw, Mapping):
        return raw
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise GoogleOAuthTokenError("Google OAuth token response was invalid") from exc
    if not isinstance(decoded, Mapping):
        raise GoogleOAuthTokenError("Google OAuth token response was invalid")
    return decoded


def _post_form(
    endpoint: str, fields: Mapping[str, str], timeout: float = TOKEN_HTTP_TIMEOUT_SECONDS
) -> Mapping[str, Any]:
    """POST an OAuth form and translate dependency details to safe errors."""

    body = urlencode(dict(fields)).encode("utf-8")
    request = Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise GoogleOAuthTokenError("Google OAuth token exchange failed") from exc
    return _safe_response_json(raw)


def _safe_token_exchange_error(exc: BaseException) -> GoogleOAuthTokenError:
    """Return a fixed error while retaining provider details only as a cause."""

    return GoogleOAuthTokenError("Google OAuth token exchange failed")


def _safe_token_refresh_error(exc: BaseException) -> GoogleOAuthTokenError:
    """Return a fixed refresh error while retaining details only as a cause."""

    return GoogleOAuthTokenError("Google OAuth token refresh failed")


def _expires_in(response: Mapping[str, Any]) -> float:
    value = response.get("expires_in", 3600)
    if isinstance(value, bool):
        raise GoogleOAuthTokenError("Google OAuth token response was invalid")
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise GoogleOAuthTokenError("Google OAuth token response was invalid") from exc
    if seconds <= 0:
        raise GoogleOAuthTokenError("Google OAuth token response was invalid")
    return seconds


def _access_token(response: Mapping[str, Any]) -> str:
    value = response.get("access_token")
    if not isinstance(value, str) or not value:
        raise GoogleOAuthTokenError("Google OAuth token response did not contain an access token")
    return value


class GoogleOAuthTokenProvider:
    """Refresh a Gmail access token while retaining it only in process memory.

    ``http_post`` is intentionally injectable for deterministic tests.  It has
    the signature ``(endpoint, form_fields, timeout)`` and returns a decoded
    mapping or a JSON string/bytes response.
    """

    # The implementation is also used for other OAuth2 IMAP providers.  Keep
    # the original Google class and payload shape for compatibility, while
    # allowing provider-specific subclasses to use their own discriminator.
    KEYCHAIN_PROVIDER = "google"
    DEFAULT_TOKEN_ENDPOINT = TOKEN_ENDPOINT

    def __init__(
        self,
        client_id: str,
        client_secret: str | None,
        refresh_token: str,
        *,
        token_endpoint: str | None = None,
        http_post: HttpPost | None = None,
        clock: Callable[[], float] | None = None,
        access_token: str | None = None,
        expires_at: float = 0.0,
        on_refresh: Callable[[bytes], None] | None = None,
    ):
        if not isinstance(client_id, str) or not client_id.strip():
            raise GoogleOAuthConfigurationError("Google OAuth client id is missing")
        if client_secret is not None and not isinstance(client_secret, str):
            raise GoogleOAuthConfigurationError("Google OAuth client secret is invalid")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise GoogleOAuthConfigurationError("Google OAuth refresh token is missing")
        self.client_id = client_id
        self.client_secret = client_secret or None
        self._refresh_token = refresh_token
        self.token_endpoint = token_endpoint or self.DEFAULT_TOKEN_ENDPOINT
        self._http_post = http_post or _post_form
        self._clock = clock or time.time
        self._access_token = access_token
        self._expires_at = float(expires_at)
        self._on_refresh = on_refresh

    @classmethod
    def from_token_response(
        cls,
        client: GoogleOAuthClient,
        response: Mapping[str, Any] | str | bytes,
        *,
        http_post: HttpPost | None = None,
        token_endpoint: str = TOKEN_ENDPOINT,
        clock: Callable[[], float] | None = None,
    ) -> "GoogleOAuthTokenProvider":
        decoded = _safe_response_json(response)
        access_token = _access_token(decoded)
        refresh_token = decoded.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise GoogleOAuthTokenError(
                "Google OAuth authorization did not return a refresh token"
            )
        now = (clock or time.time)()
        return cls(
            client.client_id,
            client.client_secret,
            refresh_token,
            token_endpoint=token_endpoint,
            http_post=http_post,
            clock=clock,
            access_token=access_token,
            expires_at=now + _expires_in(decoded),
        )

    @classmethod
    def from_keychain_secret(
        cls,
        value: bytes | str | Mapping[str, Any],
        *,
        http_post: HttpPost | None = None,
        token_endpoint: str | None = None,
        clock: Callable[[], float] | None = None,
        on_refresh: Callable[[bytes], None] | None = None,
    ) -> "GoogleOAuthTokenProvider":
        """Reconstruct a provider from the versioned, secret-store payload."""

        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise GoogleOAuthConfigurationError(
                    "Saved Google OAuth state is invalid"
                ) from exc
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError) as exc:
                raise GoogleOAuthConfigurationError(
                    "Saved Google OAuth state is invalid"
                ) from exc
        if not isinstance(value, Mapping):
            raise GoogleOAuthConfigurationError("Saved Google OAuth state is invalid")
        allowed = {
            "version",
            "provider",
            "client_id",
            "client_secret",
            "refresh_token",
        }
        if set(value) != allowed - {"client_secret"} and set(value) != allowed:
            raise GoogleOAuthConfigurationError("Saved Google OAuth state is unsupported")
        if (
            value.get("version") != KEYCHAIN_PAYLOAD_VERSION
            or value.get("provider") != cls.KEYCHAIN_PROVIDER
        ):
            raise GoogleOAuthConfigurationError("Saved Google OAuth state is unsupported")
        client_id = value.get("client_id")
        client_secret = value.get("client_secret")
        refresh_token = value.get("refresh_token")
        if not isinstance(client_id, str) or not client_id:
            raise GoogleOAuthConfigurationError("Saved Google OAuth client id is invalid")
        if client_secret is not None and not isinstance(client_secret, str):
            raise GoogleOAuthConfigurationError("Saved Google OAuth client secret is invalid")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise GoogleOAuthConfigurationError("Saved Google OAuth refresh token is invalid")
        return cls(
            client_id,
            client_secret,
            refresh_token,
            http_post=http_post,
            token_endpoint=token_endpoint,
            clock=clock,
            on_refresh=on_refresh,
        )

    # ``from_secret`` is useful at the server/CLI boundary and preserves the
    # fact that the value is a Keychain payload rather than an access token.
    from_secret = from_keychain_secret
    from_keychain = from_keychain_secret

    def keychain_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "version": KEYCHAIN_PAYLOAD_VERSION,
            "provider": self.KEYCHAIN_PROVIDER,
            "client_id": self.client_id,
            "refresh_token": self._refresh_token,
        }
        if self.client_secret is not None:
            payload["client_secret"] = self.client_secret
        return payload

    def to_keychain_secret(self) -> bytes:
        """Encode only refresh/client state for operating-system storage."""

        return json.dumps(
            self.keychain_payload(), separators=(",", ":"), sort_keys=True
        ).encode("utf-8")

    # Common serialization aliases used by callers that do not need to know
    # whether Keychain stores text or bytes.
    to_secret = to_keychain_secret
    serialize = to_keychain_secret

    def get_access_token(self) -> str:
        """Return a cached token or refresh it before the next IMAP login."""

        now = self._clock()
        if (
            self._access_token
            and now < self._expires_at - TOKEN_REFRESH_SKEW_SECONDS
        ):
            return self._access_token
        fields = {
            "client_id": self.client_id,
            "refresh_token": self._refresh_token,
            "grant_type": "refresh_token",
        }
        if self.client_secret is not None:
            fields["client_secret"] = self.client_secret
        try:
            response = self._http_post(
                self.token_endpoint, fields, TOKEN_HTTP_TIMEOUT_SECONDS
            )
        except GoogleOAuthError as exc:
            raise _safe_token_refresh_error(exc) from exc
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise GoogleOAuthTokenError("Google OAuth token refresh failed") from exc
        except Exception as exc:
            # An injected provider must not be able to leak a response body or
            # token through an exception at the adapter/CLI boundary.
            raise GoogleOAuthTokenError("Google OAuth token refresh failed") from exc
        decoded = _safe_response_json(response)
        token = _access_token(decoded)
        expires = _expires_in(decoded)
        rotated_refresh = decoded.get("refresh_token")
        if isinstance(rotated_refresh, str) and rotated_refresh:
            changed = rotated_refresh != self._refresh_token
            self._refresh_token = rotated_refresh
            if changed and self._on_refresh is not None:
                try:
                    self._on_refresh(self.to_keychain_secret())
                except Exception as exc:
                    raise GoogleOAuthTokenError(
                        "Could not save refreshed OAuth state in Keychain"
                    ) from exc
        self._access_token = token
        self._expires_at = now + expires
        return token

    # Adapter-facing and test-friendly aliases.
    access_token = get_access_token
    refresh = get_access_token

    @property
    def expires_at(self) -> float:
        """Expose expiry timing without exposing the token itself."""

        return self._expires_at

    def __repr__(self) -> str:
        return (
            "<GoogleOAuthTokenProvider client_id="
            f"{self.client_id!r} access_token=<redacted> refresh_token=<redacted>>"
        )


class MicrosoftOAuthTokenProvider(GoogleOAuthTokenProvider):
    """OAuth2 refresh provider for Thunderbird's Microsoft IMAP accounts.

    Microsoft and Google use the same RFC 6749 refresh-token exchange shape
    for this use case.  The separate class keeps Keychain payloads explicit so
    a saved Microsoft token can never be reconstructed as a Gmail connector.
    """

    KEYCHAIN_PROVIDER = "microsoft"
    DEFAULT_TOKEN_ENDPOINT = MICROSOFT_TOKEN_ENDPOINT


def oauth_provider_from_keychain_secret(
    value: bytes | str | Mapping[str, Any],
    *,
    http_post: HttpPost | None = None,
    token_endpoint: str | None = None,
    clock: Callable[[], float] | None = None,
    on_refresh: Callable[[bytes], None] | None = None,
) -> GoogleOAuthTokenProvider:
    """Reconstruct a supported OAuth provider from a Keychain payload.

    Only the non-secret provider discriminator is inspected before delegating
    validation to the concrete provider class.  Unknown providers fail closed.
    """

    raw: Any = value
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GoogleOAuthConfigurationError("Saved OAuth state is invalid") from exc
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise GoogleOAuthConfigurationError("Saved OAuth state is invalid") from exc
    if not isinstance(raw, Mapping):
        raise GoogleOAuthConfigurationError("Saved OAuth state is invalid")
    provider_name = raw.get("provider")
    if not isinstance(provider_name, str):
        raise GoogleOAuthConfigurationError("Saved OAuth provider is unsupported")
    provider_type = {
        "google": GoogleOAuthTokenProvider,
        "microsoft": MicrosoftOAuthTokenProvider,
    }.get(provider_name)
    if provider_type is None:
        raise GoogleOAuthConfigurationError("Saved OAuth provider is unsupported")
    kwargs: dict[str, Any] = {
        "http_post": http_post, "clock": clock, "on_refresh": on_refresh
    }
    if token_endpoint is not None:
        kwargs["token_endpoint"] = token_endpoint
    return provider_type.from_keychain_secret(value, **kwargs)


def generate_pkce() -> tuple[str, str]:
    """Return a high-entropy verifier and its S256 challenge."""

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorization_url(
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    *,
    scope: str = GMAIL_SCOPE,
    authorization_endpoint: str = AUTHORIZATION_ENDPOINT,
) -> str:
    """Build the installed-app authorization URL without client secrets."""

    return authorization_endpoint + "?" + urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scope,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )


@dataclass
class _CallbackState:
    event: threading.Event = field(default_factory=threading.Event)
    code: str | None = None
    error: str | None = None


class _CallbackServer(http.server.HTTPServer):
    allow_reuse_address = True

    def __init__(self, address, expected_state: str, callback_path: str, state: _CallbackState):
        self.expected_state = expected_state
        self.callback_path = callback_path
        self.callback_state = state
        super().__init__(address, _CallbackHandler)


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server: _CallbackServer

    def log_message(self, _format: str, *args: Any) -> None:
        # OAuth callback details, including query parameters, do not belong in
        # stderr or test diagnostics.
        return

    def _reply(self, status: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler hook
        parsed = urlsplit(self.path)
        if parsed.path != self.server.callback_path:
            self._reply(404, "<html><body>Not found.</body></html>")
            return
        query = parse_qs(parsed.query, keep_blank_values=True)
        state_values = query.get("state", [])
        if len(state_values) != 1 or state_values[0] != self.server.expected_state:
            self.server.callback_state.error = "OAuth callback state validation failed"
            self._reply(400, "<html><body>Authorization failed.</body></html>")
            self.server.callback_state.event.set()
            return
        errors = query.get("error", [])
        if errors:
            self.server.callback_state.error = "Google authorization was denied"
            self._reply(400, "<html><body>Authorization was not completed.</body></html>")
            self.server.callback_state.event.set()
            return
        codes = query.get("code", [])
        if len(codes) != 1 or not codes[0]:
            self.server.callback_state.error = "Google authorization callback was invalid"
            self._reply(400, "<html><body>Authorization failed.</body></html>")
            self.server.callback_state.event.set()
            return
        self.server.callback_state.code = codes[0]
        self._reply(200, "<html><body>Authorization complete. You may close this window.</body></html>")
        self.server.callback_state.event.set()


def _callback_url(server: _CallbackServer) -> str:
    return f"http://{LOOPBACK_HOST}:{server.server_port}{server.callback_path}"


def authorize_google_desktop(
    client: GoogleOAuthClient,
    *,
    open_browser: Callable[[str], Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    http_post: HttpPost | None = None,
    token_endpoint: str = TOKEN_ENDPOINT,
    authorization_endpoint: str = AUTHORIZATION_ENDPOINT,
    callback_path: str = CALLBACK_PATH,
    clock: Callable[[], float] | None = None,
) -> GoogleOAuthTokenProvider:
    """Run the Google Desktop authorization-code + PKCE flow.

    The callback binds to IPv4 loopback on an operating-system-selected port.
    Only one exact callback path and matching state are accepted.  ``timeout``
    bounds the temporary listener; it is intentionally not a general local web
    server.
    """

    if not isinstance(client, GoogleOAuthClient):
        try:
            client = GoogleOAuthClient.from_json(client)  # type: ignore[arg-type]
        except GoogleOAuthError:
            raise
        except Exception as exc:
            raise GoogleOAuthConfigurationError("Google OAuth client is invalid") from exc
    try:
        timeout_value = float(timeout)
    except (TypeError, ValueError) as exc:
        raise GoogleOAuthConfigurationError("Google OAuth callback timeout is invalid") from exc
    if timeout_value <= 0:
        raise GoogleOAuthConfigurationError("Google OAuth callback timeout is invalid")
    if not callback_path.startswith("/") or "?" in callback_path or "#" in callback_path:
        raise GoogleOAuthConfigurationError("Google OAuth callback path is invalid")

    state = secrets.token_urlsafe(32)
    verifier, challenge = generate_pkce()
    callback_state = _CallbackState()
    try:
        server = _CallbackServer(
            (LOOPBACK_HOST, 0), state, callback_path, callback_state
        )
    except OSError as exc:
        raise GoogleOAuthAuthorizationError(
            "Could not start the local Google OAuth callback"
        ) from exc
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    redirect_uri = _callback_url(server)
    url = build_authorization_url(
        client.client_id,
        redirect_uri,
        state,
        challenge,
        authorization_endpoint=authorization_endpoint,
    )
    browser = open_browser or webbrowser.open
    try:
        try:
            opened = browser(url)
        except Exception as exc:
            raise GoogleOAuthAuthorizationError(
                "Could not open the Google authorization page"
            ) from exc
        if opened is False:
            raise GoogleOAuthAuthorizationError(
                "Could not open the Google authorization page"
            )
        if not callback_state.event.wait(timeout_value):
            raise GoogleOAuthAuthorizationError("Google authorization timed out")
        if callback_state.error:
            raise GoogleOAuthAuthorizationError(callback_state.error)
        if not callback_state.code:
            raise GoogleOAuthAuthorizationError("Google authorization callback was invalid")
        fields = {
            "code": callback_state.code,
            "client_id": client.client_id,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        }
        if client.client_secret is not None:
            fields["client_secret"] = client.client_secret
        try:
            response = (http_post or _post_form)(
                token_endpoint, fields, TOKEN_HTTP_TIMEOUT_SECONDS
            )
        except GoogleOAuthError as exc:
            raise _safe_token_exchange_error(exc) from exc
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise GoogleOAuthTokenError("Google OAuth token exchange failed") from exc
        except Exception as exc:
            raise GoogleOAuthTokenError("Google OAuth token exchange failed") from exc
        return GoogleOAuthTokenProvider.from_token_response(
            client,
            response,
            http_post=http_post,
            token_endpoint=token_endpoint,
            clock=clock,
        )
    finally:
        try:
            server.shutdown()
        finally:
            server.server_close()
            thread.join(timeout=1.0)


# Explicit aliases keep the public operation discoverable while allowing tests
# and future callers to use the shorter installed-app terminology.
authorize = authorize_google_desktop
authorize_installed_app = authorize_google_desktop
load_client_secrets = GoogleOAuthClient.from_file


__all__ = [
    "AUTHORIZATION_ENDPOINT",
    "CALLBACK_PATH",
    "GMAIL_SCOPE",
    "GoogleOAuthAuthorizationError",
    "GoogleOAuthClient",
    "GoogleOAuthConfigurationError",
    "GoogleOAuthError",
    "GoogleOAuthToken",
    "GoogleOAuthTokenError",
    "GoogleOAuthTokenProvider",
    "MicrosoftOAuthTokenProvider",
    "TOKEN_ENDPOINT",
    "authorize",
    "authorize_google_desktop",
    "authorize_installed_app",
    "build_authorization_url",
    "generate_pkce",
    "load_client_secrets",
    "oauth_provider_from_keychain_secret",
]
