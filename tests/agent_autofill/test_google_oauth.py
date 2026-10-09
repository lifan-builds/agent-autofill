import json
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

import pytest

from agent_autofill.google_oauth import (
    CALLBACK_PATH,
    GMAIL_SCOPE,
    GoogleOAuthAuthorizationError,
    GoogleOAuthClient,
    GoogleOAuthConfigurationError,
    GoogleOAuthTokenError,
    GoogleOAuthTokenProvider,
    MicrosoftOAuthTokenProvider,
    MICROSOFT_TOKEN_ENDPOINT,
    authorize_google_desktop,
    build_authorization_url,
    generate_pkce,
    oauth_provider_from_keychain_secret,
)


def test_desktop_client_parser_keeps_only_installed_client_material(tmp_path):
    path = tmp_path / "client.json"
    path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "client-id.apps.googleusercontent.com",
                    "client_secret": "client-secret",
                    "project_id": "ignored-project",
                },
                "web": {"client_id": "ignored-web-client"},
            }
        )
    )

    client = GoogleOAuthClient.from_file(path)

    assert client.client_id == "client-id.apps.googleusercontent.com"
    assert client.client_secret == "client-secret"
    assert "ignored-project" not in repr(client)
    with pytest.raises(GoogleOAuthConfigurationError):
        GoogleOAuthClient.from_json({"web": {"client_id": "wrong"}})


def test_pkce_and_authorization_url_use_gmail_scope_and_offline_access():
    verifier, challenge = generate_pkce()
    url = build_authorization_url(
        "client-id", "http://127.0.0.1:4321/", "state-value", challenge
    )
    query = parse_qs(urlsplit(url).query)

    assert len(verifier) >= 43
    assert query["scope"] == [GMAIL_SCOPE]
    assert query["access_type"] == ["offline"]
    assert query["response_type"] == ["code"]
    assert query["state"] == ["state-value"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [challenge]


def test_authorize_desktop_validates_loopback_callback_and_exchanges_pkce_code():
    browser_urls = []
    requests = []

    def token_request(endpoint, fields, timeout):
        requests.append((endpoint, dict(fields), timeout))
        return {
            "access_token": "access-token-fixture",
            "refresh_token": "refresh-token-fixture",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

    def open_browser(url):
        browser_urls.append(url)
        query = parse_qs(urlsplit(url).query)
        callback = query["redirect_uri"][0]
        callback_query = "?code=authorization-code-fixture&state=" + query["state"][0]
        with urlopen(callback + callback_query, timeout=2) as response:
            assert response.status == 200
        return True

    provider = authorize_google_desktop(
        GoogleOAuthClient("client-id", "client-secret"),
        open_browser=open_browser,
        timeout=2,
        http_post=token_request,
    )

    assert len(browser_urls) == 1
    authorization_query = parse_qs(urlsplit(browser_urls[0]).query)
    redirect_uri = authorization_query["redirect_uri"][0]
    assert urlsplit(redirect_uri).hostname == "127.0.0.1"
    assert urlsplit(redirect_uri).path == CALLBACK_PATH
    endpoint, fields, timeout = requests[0]
    assert endpoint.endswith("/token")
    assert timeout > 0
    assert fields["grant_type"] == "authorization_code"
    assert fields["code"] == "authorization-code-fixture"
    assert fields["client_id"] == "client-id"
    assert fields["client_secret"] == "client-secret"
    assert fields["redirect_uri"] == redirect_uri
    assert fields["code_verifier"]
    assert provider.get_access_token() == "access-token-fixture"
    assert "access-token-fixture" not in provider.to_keychain_secret().decode()
    assert "refresh-token-fixture" in provider.to_keychain_secret().decode()


def test_authorize_desktop_rejects_wrong_state_and_times_out():
    def wrong_state(url):
        query = parse_qs(urlsplit(url).query)
        callback = query["redirect_uri"][0]
        try:
            urlopen(
                callback + "?code=authorization-code-fixture&state=wrong-state",
                timeout=2,
            ).read()
        except Exception:
            pass
        return True

    with pytest.raises(
        GoogleOAuthAuthorizationError, match="state validation failed"
    ):
        authorize_google_desktop(
            GoogleOAuthClient("client-id", None),
            open_browser=wrong_state,
            timeout=2,
        )

    with pytest.raises(GoogleOAuthAuthorizationError, match="timed out"):
        authorize_google_desktop(
            GoogleOAuthClient("client-id", None),
            open_browser=lambda _url: True,
            timeout=0.01,
        )


def test_token_provider_caches_and_refreshes_without_serializing_access_token():
    now = [1000.0]
    calls = []

    def token_request(endpoint, fields, _timeout):
        calls.append((endpoint, dict(fields)))
        return {"access_token": "refreshed-access-token", "expires_in": 3600}

    provider = GoogleOAuthTokenProvider.from_token_response(
        GoogleOAuthClient("client-id", "client-secret"),
        {
            "access_token": "initial-access-token",
            "refresh_token": "refresh-token-fixture",
            "expires_in": 3600,
        },
        http_post=token_request,
        clock=lambda: now[0],
    )
    assert provider.get_access_token() == "initial-access-token"
    assert calls == []
    now[0] = 4550.0
    assert provider.get_access_token() == "refreshed-access-token"
    assert calls[0][1]["grant_type"] == "refresh_token"
    assert calls[0][1]["refresh_token"] == "refresh-token-fixture"
    assert provider.get_access_token() == "refreshed-access-token"
    assert len(calls) == 1
    payload = provider.keychain_payload()
    assert set(payload) == {"version", "provider", "client_id", "client_secret", "refresh_token"}
    assert "initial-access-token" not in json.dumps(payload)
    assert "refreshed-access-token" not in json.dumps(payload)


def test_token_provider_rejects_missing_refresh_and_redacts_failures():
    with pytest.raises(GoogleOAuthTokenError, match="refresh token"):
        GoogleOAuthTokenProvider.from_token_response(
            GoogleOAuthClient("client-id", None),
            {"access_token": "access-token-fixture"},
        )

    def failing_request(_endpoint, _fields, _timeout):
        raise RuntimeError("response contained access-token-fixture")

    provider = GoogleOAuthTokenProvider(
        "client-id",
        None,
        "refresh-token-fixture",
        http_post=failing_request,
    )
    with pytest.raises(GoogleOAuthTokenError) as caught:
        provider.get_access_token()
    assert "access-token-fixture" not in str(caught.value)
    assert "refresh-token-fixture" not in str(caught.value)


def test_saved_oauth_payload_rejects_access_tokens_and_unknown_fields():
    payload = {
        "version": 1,
        "provider": "google",
        "client_id": "client-id",
        "refresh_token": "refresh-token-fixture",
        "access_token": "access-token-fixture",
    }
    with pytest.raises(GoogleOAuthConfigurationError, match="unsupported"):
        GoogleOAuthTokenProvider.from_keychain_secret(payload)

    payload.pop("access_token")
    payload["unexpected"] = "fixture"
    with pytest.raises(GoogleOAuthConfigurationError, match="unsupported"):
        GoogleOAuthTokenProvider.from_keychain_secret(payload)


def test_exchange_and_refresh_normalize_arbitrary_oauth_errors():
    def failing_request(_endpoint, _fields, _timeout):
        from agent_autofill.google_oauth import GoogleOAuthError

        raise GoogleOAuthError("access-token-fixture and refresh-token-fixture")

    def complete_browser(url):
        query = parse_qs(urlsplit(url).query)
        callback = query["redirect_uri"][0]
        try:
            urlopen(
                callback + "?code=authorization-code-fixture&state=" + query["state"][0],
                timeout=2,
            ).read()
        except Exception:
            pass
        return True

    with pytest.raises(GoogleOAuthTokenError, match="token exchange failed") as exchange:
        authorize_google_desktop(
            GoogleOAuthClient("client-id", None),
            open_browser=complete_browser,
            timeout=1,
            http_post=failing_request,
        )
    assert "access-token-fixture" not in str(exchange.value)
    assert "refresh-token-fixture" not in str(exchange.value)

    provider = GoogleOAuthTokenProvider(
        "client-id",
        None,
        "refresh-token-fixture",
        http_post=failing_request,
    )
    with pytest.raises(GoogleOAuthTokenError, match="token refresh failed") as refresh:
        provider.get_access_token()
    assert "access-token-fixture" not in str(refresh.value)
    assert "refresh-token-fixture" not in str(refresh.value)


def test_saved_microsoft_provider_refreshes_only_at_microsoft_and_saves_rotation():
    saved = []
    requests = []

    def refresh(endpoint, fields, timeout):
        requests.append(endpoint)
        return {
            "access_token": "fixture-microsoft-access",
            "refresh_token": "fixture-rotated-refresh",
            "expires_in": 3600,
        }

    initial = MicrosoftOAuthTokenProvider("client-id", None, "fixture-initial-refresh")
    provider = oauth_provider_from_keychain_secret(
        initial.to_keychain_secret(), http_post=refresh, on_refresh=saved.append
    )
    assert provider.get_access_token() == "fixture-microsoft-access"
    assert requests == [MICROSOFT_TOKEN_ENDPOINT]
    assert json.loads(saved[0])["refresh_token"] == "fixture-rotated-refresh"
    assert b"fixture-microsoft-access" not in saved[0]
    fresh = oauth_provider_from_keychain_secret(saved[0], http_post=refresh)
    assert fresh.token_endpoint == MICROSOFT_TOKEN_ENDPOINT
    assert fresh.keychain_payload()["refresh_token"] == "fixture-rotated-refresh"


def test_refresh_save_failure_is_sanitized_and_does_not_cache_access():
    def fail(value):
        raise RuntimeError("fixture-private-refresh")

    provider = MicrosoftOAuthTokenProvider(
        "client-id", None, "fixture-original-refresh",
        http_post=lambda *_: {
            "access_token": "fixture-private-access",
            "refresh_token": "fixture-private-refresh",
        },
        on_refresh=fail,
    )
    with pytest.raises(GoogleOAuthTokenError, match="Keychain") as caught:
        provider.get_access_token()
    assert "fixture-private" not in str(caught.value)
    assert provider.expires_at == 0
