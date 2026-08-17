"""Cursor 注册协议核心实现。

Cursor 使用 WorkOS AuthKit 的 Next Server Action。Action id 会随着部署
变化，不能像旧实现一样写死在代码里；本文件会从当前注册页的 RSC
payload 中读取 action id 和隐藏表单字段。
"""

import html
import re, json, urllib.parse, random, string
from typing import Optional, Callable
from core.proxy_utils import build_requests_proxy_config

AUTH = "https://authenticator.cursor.sh"
CURSOR = "https://cursor.com"

# 保留旧名称，避免外部导入报错；实际请求永远使用页面动态值。
ACTION_SUBMIT_EMAIL = ""
ACTION_SUBMIT_PASSWORD = ""
ACTION_MAGIC_CODE = ""

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

TURNSTILE_SITEKEY = ""


def _rand_password(n=16):
    chars = string.ascii_letters + string.digits + "!@#$"
    return "".join(random.choices(chars, k=n))


def _boundary():
    return "----WebKitFormBoundary" + "".join(
        random.choices(string.ascii_letters + string.digits, k=16)
    )


def _multipart(fields: dict, boundary: str) -> bytes:
    parts = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        )
    parts.append(f"--{boundary}--\r\n")
    return "".join(parts).encode()


def _extract_server_action_id(page_text: str) -> str:
    """从 Next RSC 页面中提取绑定到表单 action 的完整 id。

    当前 Cursor 的 id 长度为 42 个十六进制字符，旧代码的 40 位截取会
    直接得到 ``Server action not found``。
    """
    patterns = (
        r'\\?"id\\?":\\?"([0-9a-f]{40,64})\\?"\\?,\\?"bound\\?":',
        r'(?<![0-9a-f])([0-9a-f]{40,64})(?![0-9a-f])',
    )
    for pattern in patterns:
        match = re.search(pattern, str(page_text or ""), re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _extract_hidden_inputs(page_text: str) -> tuple[dict, list[str]]:
    hidden = {}
    names = []
    for input_match in re.finditer(r"<input\b([^>]*)>", page_text, re.IGNORECASE):
        attrs = input_match.group(1)
        name_match = re.search(r'\bname=["\']([^"\']+)', attrs, re.IGNORECASE)
        if not name_match:
            continue
        name = html.unescape(name_match.group(1))
        names.append(name)
        type_match = re.search(r'\btype=["\']([^"\']+)', attrs, re.IGNORECASE)
        if (type_match and type_match.group(1).lower() == "hidden") or not type_match:
            value_match = re.search(r'\bvalue=["\']([^"\']*)', attrs, re.IGNORECASE)
            hidden[name] = html.unescape(value_match.group(1)) if value_match else ""
    return hidden, names


def _extract_sitekey(page_text: str) -> str:
    patterns = (
        r"""["']site[Kk]ey["']\s*:\s*["']([^"']+)""",
        r'0x4[A-Za-z0-9_-]{20,}',
    )
    for pattern in patterns:
        match = re.search(pattern, str(page_text or ""), re.IGNORECASE)
        if match:
            return match.group(1) if match.lastindex else match.group(0)
    return ""


class CursorRegister:
    REQUEST_TIMEOUT = 30

    def __init__(self, proxy: str = None, log_fn: Callable = print):
        from curl_cffi import requests as curl_req

        self.log = log_fn
        self.s = curl_req.Session(impersonate="safari17_0")
        if proxy:
            self.s.proxies = build_requests_proxy_config(proxy)
        self._page_context = None
        self._next_url = ""
        self._last_redirect = ""
        self._first_name = "Alex"
        self._last_name = "Morgan"

    def _router_state_tree(self, url: str) -> str:
        parsed = urllib.parse.urlsplit(url)
        segments = [part for part in parsed.path.split("/") if part]
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        page = "__PAGE__"
        if query:
            page += "?" + json.dumps(query, separators=(",", ":"), ensure_ascii=False)
        child = [page, {}]
        for segment in reversed(segments):
            child = [segment, {"children": child}]
        route = ["", {"children": ["(main)", {"children": ["(root)", {"children": child}]}]}]
        return urllib.parse.quote(json.dumps(route, separators=(",", ":")), safe="")

    def _base_headers(self, next_action, referer, boundary=None, router_state_tree=None):
        ct = (
            f"multipart/form-data; boundary={boundary}"
            if boundary
            else "application/x-www-form-urlencoded"
        )
        headers = {
            "user-agent": UA,
            "accept": "text/x-component",
            "content-type": ct,
            "origin": AUTH,
            "referer": referer,
        }
        if next_action:
            headers["next-action"] = next_action
        headers["next-router-state-tree"] = router_state_tree or self._router_state_tree(referer)
        return headers

    def _load_page_context(self, url: str) -> dict:
        r = self.s.get(
            url,
            headers={"user-agent": UA, "accept": "text/html"},
            allow_redirects=True,
            timeout=self.REQUEST_TIMEOUT,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Cursor 注册页加载失败: HTTP {r.status_code}")
        final_url = str(getattr(r, "url", "") or url)
        hidden, input_names = _extract_hidden_inputs(r.text)
        action_id = _extract_server_action_id(r.text)
        if not action_id:
            raise RuntimeError("Cursor 注册页未找到当前 Server Action")
        context = {
            "url": final_url,
            "hidden": hidden,
            "input_names": input_names,
            "action_id": action_id,
            "sitekey": _extract_sitekey(r.text),
        }
        self._page_context = context
        return context

    @staticmethod
    def _redirect_from_response(response) -> str:
        for header_name in ("x-action-redirect", "location"):
            value = str(response.headers.get(header_name, "") or "").strip()
            if not value:
                continue
            if value.startswith("http://") or value.startswith("https://") or value.startswith("/"):
                return value
            match = re.search(r"(?:replace|push|redirect);([^;]+)", value, re.IGNORECASE)
            if match:
                return match.group(1)
        body = html.unescape(str(response.text or ""))
        match = re.search(r"(?:replace|push);(https?://[^;\s\"]+|/[^;\s\"]+)", body, re.IGNORECASE)
        return match.group(1) if match else ""

    def _post_action(self, context: dict, fields: dict) -> str:
        merged = dict(context.get("hidden") or {})
        merged.update({key: "" if value is None else str(value) for key, value in fields.items()})
        boundary = _boundary()
        response = self.s.post(
            context["url"],
            headers=self._base_headers(
                context["action_id"],
                context["url"],
                boundary=boundary,
            ),
            data=_multipart(merged, boundary),
            allow_redirects=False,
            timeout=self.REQUEST_TIMEOUT,
        )
        if response.status_code >= 400:
            detail = re.sub(r"\s+", " ", str(response.text or "")).strip()[:240]
            raise RuntimeError(
                f"Cursor Server Action 失败: HTTP {response.status_code} {detail}"
            )
        redirect = self._redirect_from_response(response)
        if redirect:
            self._next_url = urllib.parse.urljoin(context["url"], redirect)
            self._last_redirect = self._next_url
        return self._next_url

    def _next_page_url(self, path: str) -> str:
        current = self._page_context or {}
        parsed = urllib.parse.urlsplit(current.get("url") or f"{AUTH}/")
        query = current.get("hidden") or {}
        params = {
            key: query[key]
            for key in ("state", "redirect_uri", "authorization_session_id")
            if query.get(key)
        }
        result = f"{AUTH}{path}"
        return result + ("?" + urllib.parse.urlencode(params) if params else "")

    def step1_get_session(self):
        # 让 AuthKit 自己生成 authorization_session_id/state。带自定义 state
        # 的入口会被 Cloudflare 偶发判为异常请求，标准根路径更稳定。
        state_encoded = ""
        url = f"{AUTH}/"
        response = self.s.get(
            url,
            headers={"user-agent": UA, "accept": "text/html"},
            allow_redirects=True,
            timeout=self.REQUEST_TIMEOUT,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Cursor 注册页加载失败: HTTP {response.status_code}")
        root_url = str(getattr(response, "url", "") or url)
        signup_links = re.findall(
            r'href=["\']([^"\']*sign-up[^"\']*)', response.text, re.IGNORECASE
        )
        if signup_links:
            signup_url = urllib.parse.urljoin(root_url, html.unescape(signup_links[0]))
        else:
            signup_url = root_url if "/sign-up" in root_url else f"{AUTH}/sign-up"
        self._load_page_context(signup_url)
        state_cookie_name = None
        for cookie in self.s.cookies.jar:
            if "state-" in cookie.name:
                state_cookie_name = cookie.name
                break
        page_state = (self._page_context.get("hidden") or {}).get("state")
        return page_state or state_encoded, state_cookie_name

    def step2_submit_email(self, email, state_encoded):
        context = self._page_context or self._load_page_context(
            f"{AUTH}/sign-up?state={urllib.parse.quote(state_encoded, safe='')}"
        )
        self._post_action(
            context,
            {
                "first_name": self._first_name,
                "last_name": self._last_name,
                "email": email,
                "intent": "sign-up",
            },
        )
        return self._next_url

    def step3_submit_password(self, password, email, state_encoded, yescaptcha_key=""):
        target = self._next_url or self._next_page_url("/sign-up/password")
        context = self._load_page_context(target)
        fields = {
            "first_name": self._first_name,
            "last_name": self._last_name,
            "email": email,
            "password": password,
            "intent": "sign-up",
        }
        if yescaptcha_key:
            from core.base_captcha import YesCaptcha

            self.log("获取 Turnstile token...")
            sitekey = context.get("sitekey") or TURNSTILE_SITEKEY
            if sitekey:
                fields["captchaToken"] = YesCaptcha(yescaptcha_key).solve_turnstile(
                    context["url"], sitekey
                )
        self._post_action(context, fields)
        return self._next_url

    def step4_submit_otp(self, otp, email, state_encoded):
        target = self._next_url
        if not target:
            raise RuntimeError("Cursor 注册未返回验证码页面")
        context = self._load_page_context(target)
        code_name = next(
            (
                name
                for name in context.get("input_names", [])
                if name in {"code", "otp", "verification_code", "magic_code"}
            ),
            "code",
        )
        self._post_action(
            context,
            {"email": email, code_name: otp, "intent": "magic-code"},
        )
        m = re.search(r"[?&]code=([\w-]+)", self._next_url or "")
        return m.group(1) if m else ""

    def step5_get_token(self, auth_code, state_encoded):
        if auth_code:
            url = f"{CURSOR}/api/auth/callback?code={auth_code}&state={state_encoded}"
            self.s.get(
                url,
                headers={"user-agent": UA, "accept": "text/html"},
                allow_redirects=True,
                timeout=self.REQUEST_TIMEOUT,
            )
        elif self._next_url and self._next_url.startswith(CURSOR):
            self.s.get(
                self._next_url,
                headers={"user-agent": UA, "accept": "text/html"},
                allow_redirects=True,
                timeout=self.REQUEST_TIMEOUT,
            )
        for cookie in self.s.cookies.jar:
            if cookie.name == "WorkosCursorSessionToken":
                return urllib.parse.unquote(cookie.value)
        if self._next_url:
            self.s.get(
                self._next_url,
                headers={"user-agent": UA},
                allow_redirects=True,
                timeout=self.REQUEST_TIMEOUT,
            )
        for cookie in self.s.cookies.jar:
            if cookie.name == "WorkosCursorSessionToken":
                return urllib.parse.unquote(cookie.value)
        return ""

    def register(
        self,
        email: str,
        password: str = None,
        otp_callback: Optional[Callable] = None,
        yescaptcha_key: str = "",
    ) -> dict:
        if not password:
            password = _rand_password()
        self.log(f"邮箱: {email}")
        self.log("Step1: 获取 session...")
        state_encoded, _ = self.step1_get_session()
        self.log("Step2: 提交邮箱...")
        self.step2_submit_email(email, state_encoded)
        self.log("Step3: 提交密码 + Turnstile...")
        self.step3_submit_password(password, email, state_encoded, yescaptcha_key)
        auth_code = ""
        token = self.step5_get_token("", state_encoded)
        if not token:
            self.log("等待 OTP 邮件...")
            otp = otp_callback() if otp_callback else input("OTP: ")
            if not otp:
                raise RuntimeError("未获取到验证码")
            self.log(f"验证码: {otp}")
            self.log("Step4: 提交 OTP...")
            auth_code = self.step4_submit_otp(otp, email, state_encoded)
        self.log("Step5: 获取 Token...")
        if not token:
            token = self.step5_get_token(auth_code, state_encoded)
        if not token:
            raise RuntimeError("Cursor 注册完成但未获得 WorkosCursorSessionToken")
        return {"email": email, "password": password, "token": token}
