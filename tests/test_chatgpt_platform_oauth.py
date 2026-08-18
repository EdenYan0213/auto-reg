import json
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

from platforms.chatgpt.platform_oauth import (
    PLATFORM_OAUTH_CLIENT_ID,
    PLATFORM_OAUTH_REDIRECT_URI,
    PLATFORM_TOKEN_URL,
    PLATFORM_TOKEN_URL_LEGACY,
    exchange_platform_oauth_token,
    exchange_platform_oauth_token_legacy,
    extract_continue_url,
    extract_oauth_callback_params,
    generate_platform_oauth_start,
)
from platforms.chatgpt.http_client import (
    ClearanceBundle,
    apply_clearance_to_session,
    is_cloudflare_challenge,
)
from platforms.chatgpt.refresh_token_registration_engine import (
    RefreshTokenRegistrationEngine,
    RegistrationResult,
)


class _Response:
    status_code = 200
    url = "https://platform.openai.com/auth/callback?code=code-1&state=state-1"
    headers = {}
    text = ""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Session:
    def __init__(self, response):
        self.headers = {"User-Agent": "test-agent"}
        self.response = response
        self.post_calls = []

    def post(self, *args, **kwargs):
        self.post_calls.append((args, kwargs))
        return self.response


class PlatformOAuthTests(unittest.TestCase):
    def test_platform_authorize_url_uses_current_client_and_callback(self):
        start = generate_platform_oauth_start(
            "user@example.com", "device-fixed", screen_hint="login"
        )
        query = parse_qs(urlparse(start.auth_url).query)

        self.assertEqual(query["client_id"][0], PLATFORM_OAUTH_CLIENT_ID)
        self.assertEqual(query["redirect_uri"][0], PLATFORM_OAUTH_REDIRECT_URI)
        self.assertEqual(query["device_id"][0], "device-fixed")
        self.assertEqual(query["login_hint"][0], "user@example.com")
        self.assertEqual(query["screen_hint"][0], "login")
        self.assertEqual(query["state"][0], start.state)
        self.assertTrue(query["code_challenge"][0])

    def test_callback_parser_accepts_query_and_fragment_values(self):
        query = extract_oauth_callback_params(
            "https://platform.openai.com/auth/callback?code=abc&state=xyz"
        )
        fragment = extract_oauth_callback_params(
            "https://platform.openai.com/auth/callback#code=abc&state=xyz"
        )

        self.assertEqual(query["code"], "abc")
        self.assertEqual(query["state"], "xyz")
        self.assertEqual(fragment["code"], "abc")
        self.assertEqual(fragment["state"], "xyz")

    def test_continue_url_parser_handles_reference_response_shapes(self):
        self.assertEqual(
            extract_continue_url({"page": {"payload": {"nextUrl": "/consent"}}}),
            "/consent",
        )
        self.assertEqual(
            extract_continue_url(
                {"oai-client-auth-session": {"continue_url": "/workspace"}}
            ),
            "/workspace",
        )

    def test_current_platform_token_exchange_sends_platform_payload(self):
        session = _Session(
            _Response(
                {
                    "access_token": "access-value",
                    "refresh_token": "refresh-value",
                }
            )
        )

        tokens = exchange_platform_oauth_token(
            session,
            "code-value",
            "verifier-value",
            expected_state="state-value",
            callback_url="https://platform.openai.com/auth/callback?code=code-value&state=state-value",
        )

        self.assertEqual(tokens["access_token"], "access-value")
        self.assertEqual(session.post_calls[0][0][0], PLATFORM_TOKEN_URL)
        payload = session.post_calls[0][1]["json"]
        self.assertEqual(payload["client_id"], PLATFORM_OAUTH_CLIENT_ID)
        self.assertEqual(payload["redirect_uri"], PLATFORM_OAUTH_REDIRECT_URI)
        self.assertEqual(payload["code_verifier"], "verifier-value")

    def test_engine_callback_uses_platform_exchange(self):
        engine = RefreshTokenRegistrationEngine(
            email_service=mock.Mock(), callback_logger=lambda _message: None
        )
        engine.oauth_start = generate_platform_oauth_start("user@example.com", "did")
        engine.session = _Session(
            _Response(
                {
                    "access_token": "access-value",
                    "refresh_token": "refresh-value",
                    "id_token": "",
                }
            )
        )

        callback_url = (
            "https://platform.openai.com/auth/callback?code=code-value&state="
            + engine.oauth_start.state
        )
        result = engine._handle_oauth_callback(callback_url)

        self.assertEqual(result["access_token"], "access-value")
        self.assertEqual(engine.session.post_calls[0][0][0], PLATFORM_TOKEN_URL)

    def test_registration_continuation_can_skip_second_login(self):
        engine = RefreshTokenRegistrationEngine(
            email_service=mock.Mock(), callback_logger=lambda _message: None
        )
        engine.email = "user@example.com"
        engine.password = "password-value"
        engine.oauth_start = generate_platform_oauth_start("user@example.com", "did")
        engine.session = _Session(
            _Response(
                {
                    "access_token": "access-value",
                    "refresh_token": "refresh-value",
                }
            )
        )
        engine._post_registration_continue_url = (
            "https://platform.openai.com/auth/callback?code=code-value&state="
            + engine.oauth_start.state
        )
        result = RegistrationResult(success=False)

        self.assertTrue(engine._try_post_registration_token_exchange(result))
        self.assertEqual(result.access_token, "access-value")
        self.assertEqual(result.refresh_token, "refresh-value")
        self.assertEqual(result.password, "password-value")

    def test_registration_result_redacts_cookie_value_in_summary(self):
        result = RegistrationResult(
            success=True,
            access_token="access-value",
            refresh_token="refresh-value",
            cookies="session-secret=hidden",
        )

        summary = result.to_dict()

        self.assertEqual(summary["cookies"], "***")
        self.assertNotIn("session-secret=hidden", json.dumps(summary))

    def test_is_cloudflare_challenge_detects_interstitial(self):
        challenge = mock.Mock(status_code=403)
        challenge.text = '<html><body>cf-chl-platform?__cf_chl_rt_tk=abc "Just a moment"...</body></html>'
        self.assertTrue(is_cloudflare_challenge(challenge))

        ok = mock.Mock(status_code=200)
        ok.text = "normal response"
        self.assertFalse(is_cloudflare_challenge(ok))

        wrong_status = mock.Mock(status_code=404)
        wrong_status.text = "cf-browser-verification"
        self.assertFalse(is_cloudflare_challenge(wrong_status))

    def test_apply_clearance_injects_cookies_and_user_agent(self):
        session = mock.Mock()
        session.headers = {}
        bundle = ClearanceBundle(
            cookies={"cf_clearance": "abc123", "another": "x"},
            user_agent="Chrome/136 UA",
            target_host="auth.openai.com",
        )

        apply_clearance_to_session(session, bundle)

        self.assertEqual(session.headers["User-Agent"], "Chrome/136 UA")
        session.cookies.set.assert_called()
        set_calls = session.cookies.set.call_args_list
        domains = [c[1].get("domain") for c in set_calls if isinstance(c[1], dict)]
        self.assertTrue(all(".auth.openai.com" in str(d) for d in domains))

    @mock.patch(
        "platforms.chatgpt.platform_oauth._fresh_token_session",
        return_value=mock.Mock(),
    )
    def test_legacy_exchange_uses_fresh_session(self, mock_fresh):
        fresh = mock_fresh.return_value
        fresh.post.return_value = _Response(
            {
                "access_token": "access-value",
                "refresh_token": "refresh-value",
                "id_token": "id-value",
            }
        )

        tokens = exchange_platform_oauth_token_legacy(
            mock.Mock(),
            "code-value",
            "verifier-value",
            fresh_session=True,
            proxy="http://127.0.0.1:7890",
        )

        mock_fresh.assert_called_once_with("http://127.0.0.1:7890")
        self.assertEqual(tokens["access_token"], "access-value")
        called_url = fresh.post.call_args[0][0]
        self.assertEqual(called_url, PLATFORM_TOKEN_URL_LEGACY)
        body = fresh.post.call_args[1]["data"]
        self.assertEqual(body["grant_type"], "authorization_code")
        self.assertEqual(body["client_id"], PLATFORM_OAUTH_CLIENT_ID)


if __name__ == "__main__":
    unittest.main()
