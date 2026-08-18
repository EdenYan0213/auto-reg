"""OpenAI Platform OAuth helpers used by the ChatGPT registration flow.

The web registration flow and the legacy Codex CLI OAuth flow use different
clients, redirect URIs, and token endpoints.  Keeping the Platform variant in
its own module makes that distinction explicit and keeps the legacy client
available to the other integrations in this repository.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from .oauth import OAuthStart


AUTH_BASE = "https://auth.openai.com"
PLATFORM_BASE = "https://platform.openai.com"
PLATFORM_OAUTH_CLIENT_ID = "app_2SKx67EdpoN0G6j64rFvigXD"
PLATFORM_OAUTH_REDIRECT_URI = f"{PLATFORM_BASE}/auth/callback"
PLATFORM_OAUTH_AUDIENCE = "https://api.openai.com/v1"
PLATFORM_AUTH0_CLIENT = "eyJuYW1lIjoiYXV0aDAtc3BhLWpzIiwidmVyc2lvbiI6IjEuMjEuMCJ9"
PLATFORM_AUTHORIZE_URL = f"{AUTH_BASE}/api/accounts/authorize"
PLATFORM_TOKEN_URL = f"{AUTH_BASE}/api/accounts/oauth/token"
PLATFORM_TOKEN_URL_LEGACY = f"{AUTH_BASE}/oauth/token"


class PlatformOAuthError(RuntimeError):
    """A safe-to-log Platform OAuth error without response credentials."""


def _b64url_no_padding(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _pkce_verifier() -> str:
    return secrets.token_urlsafe(64)


def _pkce_challenge(verifier: str) -> str:
    return _b64url_no_padding(hashlib.sha256(verifier.encode("ascii")).digest())


def generate_platform_oauth_start(
    email: str = "",
    device_id: str = "",
    *,
    screen_hint: str = "login_or_signup",
) -> OAuthStart:
    """Build the current Platform OAuth authorize request.

    This is the same standard PKCE authorization flow used by the reference
    project.  It does not solve or bypass any anti-abuse challenge; the caller
    still has to complete the normal OpenAI authorization state machine.
    """

    state = secrets.token_urlsafe(32)
    code_verifier = _pkce_verifier()
    params = {
        "issuer": AUTH_BASE,
        "client_id": PLATFORM_OAUTH_CLIENT_ID,
        "audience": PLATFORM_OAUTH_AUDIENCE,
        "redirect_uri": PLATFORM_OAUTH_REDIRECT_URI,
        "device_id": str(device_id or "").strip(),
        "screen_hint": str(screen_hint or "login_or_signup").strip(),
        "max_age": "0",
        "login_hint": str(email or "").strip(),
        "scope": "openid profile email offline_access",
        "response_type": "code",
        "response_mode": "query",
        "state": state,
        "nonce": secrets.token_urlsafe(32),
        "code_challenge": _pkce_challenge(code_verifier),
        "code_challenge_method": "S256",
        "auth0Client": PLATFORM_AUTH0_CLIENT,
    }
    return OAuthStart(
        auth_url=f"{PLATFORM_AUTHORIZE_URL}?{urlencode(params)}",
        state=state,
        code_verifier=code_verifier,
        redirect_uri=PLATFORM_OAUTH_REDIRECT_URI,
    )


def extract_oauth_callback_params(url: str) -> dict[str, str]:
    """Extract code/state/error from a callback URL or redirect Location.

    Some OpenAI responses place the values in a fragment while others use the
    query string.  Query values win, and fragment values fill missing fields.
    """

    candidate = str(url or "").strip()
    if not candidate:
        return {"code": "", "state": "", "error": "", "error_description": ""}
    if "://" not in candidate:
        candidate = urljoin(AUTH_BASE, candidate)

    parsed = urlparse(candidate)
    values = parse_qs(parsed.query, keep_blank_values=True)
    fragment_values = parse_qs(parsed.fragment, keep_blank_values=True)
    for key, items in fragment_values.items():
        if not values.get(key) or not str(values[key][0] or "").strip():
            values[key] = items

    def first(name: str) -> str:
        return str((values.get(name) or [""])[0] or "").strip()

    return {
        "code": first("code"),
        "state": first("state"),
        "error": first("error"),
        "error_description": first("error_description"),
    }


def extract_continue_url(data: Any) -> str:
    """Read a continuation URL from known OpenAI response shapes."""

    if not isinstance(data, dict):
        return ""

    for key in ("continue_url", "continueUrl", "next_url", "nextUrl"):
        value = str(data.get(key) or "").strip()
        if value:
            return value

    page = data.get("page")
    if isinstance(page, dict):
        payload = page.get("payload")
        if isinstance(payload, dict):
            for key in ("url", "continue_url", "continueUrl", "next_url", "nextUrl"):
                value = str(payload.get(key) or "").strip()
                if value:
                    return value

    session_info = data.get("oai-client-auth-session")
    if isinstance(session_info, dict):
        return str(
            session_info.get("continue_url")
            or session_info.get("continueUrl")
            or ""
        ).strip()
    return ""


def _response_json(response: Any) -> dict[str, Any]:
    try:
        data = response.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _safe_status(response: Any) -> str:
    status = getattr(response, "status_code", "unknown")
    return str(status)


def _platform_token_headers(session: Any) -> dict[str, str]:
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "auth0-client": PLATFORM_AUTH0_CLIENT,
        "cache-control": "no-cache",
        "content-type": "application/json",
        "origin": PLATFORM_BASE,
        "pragma": "no-cache",
        "priority": "u=1, i",
        "referer": f"{PLATFORM_BASE}/",
        "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
    }
    try:
        user_agent = str(session.headers.get("User-Agent") or "").strip()
    except Exception:
        user_agent = ""
    if user_agent:
        headers["user-agent"] = user_agent
    return headers


def _require_token_response(data: dict[str, Any], *, require_id_token: bool = False) -> dict[str, Any]:
    missing = [key for key in ("access_token", "refresh_token") if not str(data.get(key) or "").strip()]
    if require_id_token and not str(data.get("id_token") or "").strip():
        missing.append("id_token")
    if missing:
        raise PlatformOAuthError(
            "Platform OAuth token response missing: " + ", ".join(missing)
        )
    return data


def exchange_platform_oauth_token(
    session: Any,
    code: str,
    code_verifier: str,
    *,
    expected_state: str = "",
    callback_url: str = "",
) -> dict[str, Any]:
    """Exchange a Platform callback code for access/refresh credentials."""

    callback = extract_oauth_callback_params(callback_url) if callback_url else {}
    callback_state = str(callback.get("state") or "").strip()
    if expected_state and callback_state and callback_state != expected_state:
        raise PlatformOAuthError("OAuth state mismatch")
    if callback and callback.get("error"):
        raise PlatformOAuthError(
            "OAuth authorization failed: "
            + str(callback.get("error") or "unknown error").strip()
        )

    code = str(code or "").strip()
    verifier = str(code_verifier or "").strip()
    if not code or not verifier:
        raise PlatformOAuthError("OAuth callback code or PKCE verifier is missing")

    payload = {
        "client_id": PLATFORM_OAUTH_CLIENT_ID,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": PLATFORM_OAUTH_REDIRECT_URI,
    }
    try:
        response = session.post(
            PLATFORM_TOKEN_URL,
            headers=_platform_token_headers(session),
            json=payload,
            timeout=60,
        )
    except Exception as exc:
        raise PlatformOAuthError(f"Platform OAuth token request failed: {exc}") from exc

    if _safe_status(response) != "200":
        raise PlatformOAuthError(
            f"Platform OAuth token endpoint rejected request: HTTP {_safe_status(response)}"
        )
    return _require_token_response(_response_json(response))


def exchange_platform_oauth_token_legacy(
    session: Any,
    code: str,
    code_verifier: str,
    *,
    expected_state: str = "",
    callback_url: str = "",
    fresh_session: bool = False,
    proxy: str = "",
) -> dict[str, Any]:
    """Compatibility fallback using the legacy endpoint with Platform values.

    `fresh_session` uses a brand-new TLS-impersonated session so the exchange is
    not influenced by the main OAuth session's cookies, mirroring the reference
    project's behavior.
    """

    callback = extract_oauth_callback_params(callback_url) if callback_url else {}
    callback_state = str(callback.get("state") or "").strip()
    if expected_state and callback_state and callback_state != expected_state:
        raise PlatformOAuthError("OAuth state mismatch")
    code = str(code or "").strip()
    verifier = str(code_verifier or "").strip()
    if not code or not verifier:
        raise PlatformOAuthError("OAuth callback code or PKCE verifier is missing")

    token_session = session
    if fresh_session:
        token_session = _fresh_token_session(proxy)

    try:
        response = token_session.post(
            PLATFORM_TOKEN_URL_LEGACY,
            headers={
                "accept": "application/json",
                "accept-language": "en-US,en;q=0.9",
                "content-type": "application/x-www-form-urlencoded",
                "origin": AUTH_BASE,
                "priority": "u=1, i",
                "referer": f"{AUTH_BASE}/",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": PLATFORM_OAUTH_REDIRECT_URI,
                "client_id": PLATFORM_OAUTH_CLIENT_ID,
                "code_verifier": verifier,
            },
            timeout=60,
        )
    except Exception as exc:
        raise PlatformOAuthError(f"Legacy OAuth token request failed: {exc}") from exc
    finally:
        if fresh_session and token_session is not session:
            try:
                token_session.close()
            except Exception:
                pass

    if _safe_status(response) != "200":
        raise PlatformOAuthError(
            f"Legacy OAuth token endpoint rejected request: HTTP {_safe_status(response)}"
        )
    return _require_token_response(_response_json(response))


def _fresh_token_session(proxy: str = "") -> Any:
    """Build a fresh TLS-impersonated session for the legacy token exchange."""
    try:
        from curl_cffi import requests as cffi_requests
    except Exception as exc:  # pragma: no cover - dependency always present
        raise PlatformOAuthError(f"curl_cffi 不可用: {exc}") from exc

    kwargs: dict[str, Any] = {"impersonate": "chrome136", "verify": False}
    proxies: dict[str, str] = {}
    if proxy:
        cleaned = str(proxy or "").strip()
        if cleaned:
            proxies = {"http": cleaned, "https": cleaned}
    if proxies:
        kwargs["proxies"] = proxies
    return cffi_requests.Session(**kwargs)


def extract_callback_from_redirect_chain(
    session: Any,
    start_url: str,
    *,
    max_hops: int = 12,
) -> str:
    """Follow only ordinary HTTP redirects and return the first OAuth callback."""

    current = str(start_url or "").strip()
    if not current:
        return ""

    for _ in range(max(1, int(max_hops))):
        if extract_oauth_callback_params(current).get("code"):
            return current
        try:
            response = session.get(current, allow_redirects=False, timeout=30)
        except Exception:
            return ""
        location = str(getattr(response, "headers", {}).get("Location") or "").strip()
        if not location:
            final_url = str(getattr(response, "url", "") or "").strip()
            return final_url if extract_oauth_callback_params(final_url).get("code") else ""
        current = urljoin(current, location)
    return current if extract_oauth_callback_params(current).get("code") else ""


def serialize_chatgpt_cookie_header(session: Any) -> str:
    """Serialize only ChatGPT-domain cookies for an explicit later action."""

    cookies = getattr(session, "cookies", None)
    pairs: dict[str, str] = {}
    jar = getattr(cookies, "jar", None)
    try:
        iterator = iter(jar) if jar is not None else iter(())
        for cookie in iterator:
            domain = str(getattr(cookie, "domain", "") or "").lstrip(".").lower()
            if domain and domain != "chatgpt.com" and not domain.endswith(".chatgpt.com"):
                continue
            name = str(getattr(cookie, "name", "") or "").strip()
            value = str(getattr(cookie, "value", "") or "").strip()
            if name and value:
                pairs[name] = value
    except Exception:
        pairs = {}

    if not pairs:
        try:
            values = cookies.get_dict() if cookies is not None else {}
        except Exception:
            values = {}
        if isinstance(values, dict):
            for name, value in values.items():
                name = str(name or "").strip()
                value = str(value or "").strip()
                if name and value:
                    pairs[name] = value

    return "; ".join(f"{name}={value}" for name, value in pairs.items())


__all__ = [
    "AUTH_BASE",
    "PLATFORM_BASE",
    "PLATFORM_OAUTH_CLIENT_ID",
    "PLATFORM_OAUTH_REDIRECT_URI",
    "PlatformOAuthError",
    "exchange_platform_oauth_token",
    "exchange_platform_oauth_token_legacy",
    "extract_callback_from_redirect_chain",
    "extract_continue_url",
    "extract_oauth_callback_params",
    "generate_platform_oauth_start",
    "serialize_chatgpt_cookie_header",
]
