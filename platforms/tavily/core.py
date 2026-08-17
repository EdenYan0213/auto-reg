"""Tavily 注册协议核心实现 (Auth0 流程)"""
import html
import re, json, secrets, hashlib, base64, urllib.parse
from typing import Optional, Callable

AUTH0_CLIENT_ID   = "RRIAvvXNFxpfTWIozX1mXqLnyUmYSTrQ"
AUTH0_BASE        = "https://auth.tavily.com"
APP_BASE          = "https://app.tavily.com"
REDIRECT_URI      = "https://app.tavily.com/api/auth/callback"
# Auth0 会在注册页动态下发 sitekey，不能长期硬编码。
TURNSTILE_SITEKEY = ""


class TavilyRegister:
    def __init__(self, executor, captcha, log_fn: Callable = print):
        self.ex = executor
        self.captcha = captcha
        self.log = log_fn
        self.signup_url = ""
        self.turnstile_sitekey = ""

    @staticmethod
    def _extract_sitekey(text: str) -> str:
        patterns = (
            r'data-captcha-sitekey=["\']([^"\']+)',
            r'["\']sitekey["\']\s*[:=]\s*["\']([^"\']+)',
            r'0x4[A-Za-z0-9_-]{20,}',
        )
        for pattern in patterns:
            match = re.search(pattern, str(text or ""), re.IGNORECASE)
            if match:
                return match.group(1) if match.lastindex else match.group(0)
        return ""

    def step1_authorize(self) -> str:
        """GET /authorize → 返回 state"""
        nonce = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(43)
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b'=').decode()
        state_val = base64.urlsafe_b64encode(
            json.dumps({"returnTo": f"{APP_BASE}/home"}).encode()
        ).rstrip(b'=').decode()
        params = {
            "client_id": AUTH0_CLIENT_ID, "scope": "openid profile email",
            "response_type": "code", "redirect_uri": REDIRECT_URI,
            "nonce": nonce, "state": state_val,
            "screen_hint": "signup", "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        r = self.ex.get(f"{AUTH0_BASE}/authorize", params=params)
        location = r.headers.get("location", "") or ""
        m = re.search(r'[?&]state=([^&]+)', location)
        state = urllib.parse.unquote(m.group(1)) if m else state_val

        # ProtocolExecutor 会跟随 Auth0 重定向，当前响应通常就是 identifier
        # 页面。用最终页面中的 state/sitekey 建立后续请求契约。
        page_text = str(r.text or "")
        state_input = re.search(
            r'<input[^>]+name=["\']state["\'][^>]+value=["\']([^"\']+)',
            page_text,
            re.IGNORECASE,
        )
        if state_input:
            state = html.unescape(state_input.group(1))
        self.turnstile_sitekey = self._extract_sitekey(page_text)
        self.signup_url = f"{AUTH0_BASE}/u/signup/identifier?state={urllib.parse.quote(state, safe='')}"
        if not self.turnstile_sitekey:
            raise RuntimeError("Tavily 注册页未返回 Turnstile sitekey")
        return state

    def step2_solve_captcha(self) -> str:
        self.log("获取 Turnstile token...")
        if not self.signup_url or not self.turnstile_sitekey:
            raise RuntimeError("Tavily 注册上下文不完整，无法获取 Turnstile 参数")
        token = self.captcha.solve_turnstile(self.signup_url, self.turnstile_sitekey)
        self.log("Turnstile OK")
        return token

    def step3_submit_email(self, email: str, state: str, captcha_token: str) -> str:
        self.log(f"提交邮箱: {email}")
        r = self.ex.post(
            f"{AUTH0_BASE}/u/signup/identifier",
            params={"state": state},
            data={"state": state, "email": email, "captcha": captcha_token},
        )
        loc = r.headers.get("location", "")
        m = re.search(r'[?&]state=([^&]+)', loc)
        return urllib.parse.unquote(m.group(1)) if m else state

    def step4_submit_otp(self, otp: str, challenge_state: str) -> str:
        self.log("提交验证码...")
        r = self.ex.post(
            f"{AUTH0_BASE}/u/email-identifier/challenge",
            params={"state": challenge_state},
            data={"state": challenge_state, "code": otp},
        )
        loc = r.headers.get("location", "")
        m = re.search(r'[?&]state=([^&]+)', loc)
        return urllib.parse.unquote(m.group(1)) if m else challenge_state

    def step5_submit_password(self, email: str, password: str, pw_state: str) -> str:
        self.log("设置密码...")
        r = self.ex.post(
            f"{AUTH0_BASE}/u/signup/password",
            params={"state": pw_state},
            data={"state": pw_state, "email": email, "password": password,
                  "passwordPolicy.isFlexible": "false",
                  "strengthPolicy": "good", "complexityOptions.minLength": "8"},
        )
        loc = r.headers.get("location", "")
        m = re.search(r'[?&]state=([^&]+)', loc)
        return urllib.parse.unquote(m.group(1)) if m else pw_state

    def step6_resume_and_get_key(self, resume_state: str) -> str:
        self.log("完成授权流程...")
        self.ex.get(f"{AUTH0_BASE}/authorize/resume", params={"state": resume_state})
        r = self.ex.get(f"{APP_BASE}/api/keys", headers={"accept": "application/json"})
        try:
            keys = r.json()
            if keys and isinstance(keys, list):
                return keys[0].get("key", "")
        except Exception:
            pass
        return ""

    def register(self, email: str, password: str,
                 otp_callback: Optional[Callable] = None) -> dict:
        state = self.step1_authorize()
        captcha_token = self.step2_solve_captcha()
        challenge_state = self.step3_submit_email(email, state, captcha_token)
        otp = otp_callback() if otp_callback else input("OTP: ")
        if not otp:
            raise RuntimeError("未获取到验证码")
        self.log(f"验证码: {otp}")
        pw_state = self.step4_submit_otp(otp, challenge_state)
        resume_state = self.step5_submit_password(email, password, pw_state)
        api_key = self.step6_resume_and_get_key(resume_state)
        self.log(f"API Key: {api_key[:20]}..." if api_key else "未获取到 API Key")
        return {"email": email, "password": password, "api_key": api_key}
