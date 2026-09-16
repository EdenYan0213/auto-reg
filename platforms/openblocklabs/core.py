"""
OpenBlockLabs 自动注册 (WorkOS AuthKit)

流程:
  1. GET auth-relay/.../initiate_signup → authorization_session_id
  2. GET auth.openblocklabs.com/sign-up?... → 提取 next-action ID
  3. POST /sign-up (first_name/last_name/email/intent=sign-up) → __Host-state cookie
  4. GET /sign-up/password → 提取 next-action ID
  5. POST /sign-up/password (password/signals/...) → pendingAuthenticationToken from RSC body
  6. GET /email-verification → 提取 next-action ID
  7. POST /email-verification (code + pending_authentication_token) → 303 → callback
  8. GET dashboard.openblocklabs.com/auth/callback?code=... → wos-session cookie
  9. GET /api/create-personal-org → 完成

pip install curl_cffi requests
"""

import re, json, time, base64, random, string, os
from urllib.parse import urlencode, urlparse, parse_qs, quote, urljoin
import subprocess, tempfile
import requests as std_requests
from core.proxy_utils import build_requests_proxy_config

# ─── Curl subprocess wrapper (bypasses Cloudflare TLS fingerprint) ───────────

CURL_BIN = "/usr/bin/curl"


class _CurlCookie:
    """Mimics requests cookie with .name / .value attributes."""
    def __init__(self, name: str, value: str):
        self.name = name
        self.value = value


class _CurlCookieJar:
    """Iterable cookie jar backed by a curl cookie file."""
    def __init__(self, cookie_path: str):
        self._path = cookie_path
        self._cookies: list[_CurlCookie] = []

    def _parse(self):
        """Re-read Netscape cookie file after each request."""
        self._cookies.clear()
        try:
            with open(self._path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("\t")
                    if len(parts) >= 7:
                        self._cookies.append(_CurlCookie(parts[5], parts[6]))
        except FileNotFoundError:
            pass

    def __iter__(self):
        self._parse()
        return iter(self._cookies)


class CurlResponse:
    """Minimal response object matching the interface used in core.py."""
    def __init__(self, status_code: int, text: str, url: str,
                 headers: dict, history: list = None):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.headers = headers
        self.history = history or []

    @property
    def ok(self):
        return 200 <= self.status_code < 400


class CurlSession:
    """
    Drop-in replacement for curl_cffi.requests.Session.
    Delegates to /usr/bin/curl via subprocess, bypassing TLS fingerprint detection.
    Cookie state persists via a temporary Netscape cookie file.
    """
    def __init__(self, proxy: str = None):
        self._cookie_file = tempfile.NamedTemporaryFile(
            suffix=".txt", prefix="curl_cookies_", delete=False
        ).name
        self.cookies = _CurlCookieJar(self._cookie_file)
        self.headers: dict = {}
        self.proxies: dict = {}

    def get(self, url: str, params: dict = None, headers: dict = None,
            allow_redirects: bool = True) -> CurlResponse:
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{qs}"
        return self._request("GET", url, headers=headers,
                             allow_redirects=allow_redirects)

    def post(self, url: str, data: bytes = None, headers: dict = None,
             allow_redirects: bool = False) -> CurlResponse:
        return self._request("POST", url, data=data, headers=headers,
                             allow_redirects=allow_redirects)

    def _request(self, method: str, url: str, data: bytes = None,
                 headers: dict = None, allow_redirects: bool = True) -> CurlResponse:
        cmd = [
            CURL_BIN, "-s", "-S",
            "-w", "\n%{http_code}",
            "-b", self._cookie_file,
            "-c", self._cookie_file,
            "-A", self.headers.get("user-agent", "Mozilla/5.0"),
        ]

        if not allow_redirects:
            cmd += ["--max-redirs", "0"]
        else:
            cmd += ["-L", "--max-redirs", "10"]

        # Proxy
        proxy_url = self.proxies.get("https") or self.proxies.get("http")
        if proxy_url:
            cmd += ["--proxy", proxy_url]

        # Merge headers
        merged = {**self.headers}
        if headers:
            merged.update(headers)
        for k, v in merged.items():
            if k.lower() == "user-agent":
                continue  # already set via -A
            cmd += ["-H", f"{k}: {v}"]

        # Use -v to capture ALL intermediate redirect headers in stderr.
        # -D only dumps the FINAL response headers; -v logs every hop.
        cmd += ["-v"]

        data_file = None
        if method == "POST" and data is not None:
            data_file = self._write_data(data)
            cmd += ["--data-binary", f"@{data_file}"]

        cmd.append(url)

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            if data_file:
                try:
                    os.unlink(data_file)
                except OSError:
                    pass
            return CurlResponse(0, "", url, {})

        output = result.stdout
        # Split body and status code (last line after \n)
        lines = output.rsplit("\n", 1)
        if len(lines) == 2:
            body, code_str = lines
            try:
                status_code = int(code_str.strip())
            except ValueError:
                body = output
                status_code = 0
        else:
            body = output
            status_code = 0

        # Parse ALL response headers from -v stderr to build redirect history.
        # curl -v outputs blocks like:
        #   < HTTP/1.1 307 Temporary Redirect
        #   < Location: https://auth.example.com/sign-up?authorization_session_id=...
        #   < set-cookie: ...
        # Each block starts with "< " lines after a "> " request block.
        history: list[CurlResponse] = []
        resp_headers: dict = {}
        current_headers: dict = {}
        current_status = 0

        def _flush_block():
            """Flush the current header block into history."""
            nonlocal current_headers, current_status
            if current_status and current_headers:
                # Skip the very last block (it's the final response, handled separately)
                block_url = current_headers.pop("_curl_redirect_url", url)
                history.append(CurlResponse(current_status, "", block_url, dict(current_headers)))
            current_headers = {}
            current_status = 0

        for line in result.stderr.splitlines():
            # Request line: "> GET /path HTTP/1.1" — marks a new hop
            if line.startswith("> ") and ("GET " in line or "POST " in line or "HEAD " in line):
                _flush_block()
                continue
            # Response status: "< HTTP/1.1 307 Temporary Redirect"
            if line.startswith("< HTTP/") or line.startswith("< HTTP\\"):
                _flush_block()
                parts = line.split(None, 2)
                if len(parts) >= 2:
                    try:
                        current_status = int(parts[1])
                    except ValueError:
                        pass
                continue
            # Header line: "< key: value"
            if line.startswith("< ") and ":" in line[2:]:
                key, _, val = line[2:].partition(":")
                current_headers[key.strip().lower()] = val.strip()
                continue
            # Track redirect URL from curl info lines
            # "  Trying ip:port..."
            # "  Connected to ... "
            # "  Location: ..." is already captured above as header
            # "  > GET /next HTTP/1.1" marks the redirected request
            # Also capture the URL curl is connecting to
            if "Connected to" in line or "Trying" in line:
                continue
            # curl sometimes prints: "< location: ..." (lowercase) — already handled

        # Flush the last intermediate block (before final response)
        _flush_block()

        # The FINAL response headers come from the last "< " block in stderr
        # which we just flushed. But actually the final response headers are in
        # the last block. Let's re-parse: the last block IS the final response.
        # Reconstruct final response headers from the last block in stderr.
        if history:
            last_intermediate = history[-1]
            # If the last flushed block has the same status as the final response,
            # it's actually the final response — pop it from history.
            if last_intermediate.status_code == status_code:
                resp_headers = last_intermediate.headers
                history.pop()
            else:
                resp_headers = current_headers if current_headers else {}
        else:
            resp_headers = current_headers if current_headers else {}

        # Clean up temp files
        if data_file:
            try:
                os.unlink(data_file)
            except OSError:
                pass

        return CurlResponse(status_code, body, url, resp_headers, history=history)

    def _write_data(self, data: bytes) -> str:
        """Write POST body to a temp file, return path."""
        f = tempfile.NamedTemporaryFile(suffix=".dat", prefix="curl_post_", delete=False)
        try:
            f.write(data)
            return f.name
        finally:
            f.close()
            # NOTE: file is NOT unlinked here — caller must unlink after curl runs

    def close(self):
        try:
            os.unlink(self._cookie_file)
        except OSError:
            pass


# ─── 配置 ───────────────────────────────────────────────────────────────────

AUTH_BASE = "https://auth.openblocklabs.com"
DASHBOARD_BASE = "https://dashboard.openblocklabs.com"
DASHBOARD_CALLBACK = f"{DASHBOARD_BASE}/auth/callback"
CLIENT_ID = "client_01K8YDZSSKDMK8GYTEHBAW4N4S"
# ────────────────────────────────────────────────────────────────────────────

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def _rand_password(n=14):
    chars = string.ascii_letters + string.digits + "!@#"
    pw = (
        random.choice(string.ascii_uppercase)
        + random.choice(string.ascii_lowercase)
        + random.choice(string.digits)
        + random.choice("!@#")
        + "".join(random.choices(chars, k=n - 4))
    )
    lst = list(pw)
    random.shuffle(lst)
    return "".join(lst)


def _build_multipart(
    fields: list, boundary: str = "----WebKitFormBoundaryPyAPI"
) -> tuple:
    body = ""
    for name, value in fields:
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
    body += f"--{boundary}--\r\n"
    return body.encode("utf-8"), f"multipart/form-data; boundary={boundary}"


def _make_signals() -> str:
    """生成伪造的 browser signals (base64 JSON)"""
    data = {
        "createdAtMs": int(time.time() * 1000),
        "timezone": "Asia/Shanghai",
        "language": "zh-CN",
        "hardwareConcurrency": 8,
        "webdriver": False,
        "userAgent": UA,
        "appVersion": UA.split("Mozilla/5.0 ")[1] if "Mozilla" in UA else UA,
        "platform": "MacIntel",
        "screen": {
            "width": 1470,
            "height": 956,
            "availWidth": 1470,
            "availHeight": 956,
            "windowOuterWidth": 1470,
            "windowOuterHeight": 956,
            "colorDepth": 24,
            "pixelDepth": 24,
        },
        "maxTouchPoints": 0,
        "deviceMemory": 8,
        "devicePixelRatio": 2,
        "pluginsLength": 5,
        "mimeTypesCount": 2,
        "webdriver": False,
        "playwrightDetected": False,
        "phantomDetected": False,
        "nightmareDetected": False,
        "seleniumDetected": False,
        "puppeteerDetected": False,
        "submittedAtMs": int(time.time() * 1000) + 5000,
    }
    return base64.b64encode(json.dumps(data).encode()).decode()


# ─── Register ────────────────────────────────────────────────────────────────
class OpenBlockLabsRegister:
    def __init__(self, proxy: str = None):
        self.s = CurlSession(proxy=proxy)
        # Impersonation detected by Cloudflare → 404 on /sign-up.
        # Plain TLS (no impersonate) works as regular curl does.
        if proxy:
            self.s.proxies = build_requests_proxy_config(proxy)
        self.s.headers.update(
            {
                "user-agent": UA,
                "accept-language": "zh-CN,zh;q=0.9",
            }
        )
        self.authorization_session_id = None
        self._action_id = None
        self._session_token = ""

    def log(self, msg):
        print(f"[REG] {msg}")

    def _get_headers(self, referer: str = None, accept: str = None) -> dict:
        h = {
            "accept": accept
            or "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "origin": AUTH_BASE,
            "referer": referer or f"{AUTH_BASE}/",
            "sec-ch-ua": '"Not:A-Brand";v="99", "Google Chrome";v="131", "Chromium";v="131"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "sec-fetch-user": "?1",
        }
        return h

    def _extract_action_id(self, text: str) -> str:
        # WorkOS/Next 部署后 Action id 会变化；当前页面已出现 42 位 id，
        # 旧的 {40} 匹配会得到 None，随后 POST /sign-up 直接返回 400。
        patterns = (
            r'\\?"id\\?":\\?"([a-f0-9]{40,64})\\?"\\?,\\?"bound\\?":',
            r'(?<![0-9a-f])([a-f0-9]{40,64})(?![0-9a-f])',
        )
        for pattern in patterns:
            match = re.search(pattern, str(text or ""), re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _post_action(self, url: str, fields: list, router_state: str):
        # Next 当前的 multipart Server Action 字段名是 ``_1_email``。旧实现
        # 少了开头的下划线，线上会返回 ``invalid_params``；保留旧调用点的
        # ``1_`` 写法，在这里统一转换，并补上 action 的 0 字段。
        normalized = []
        has_zero = False
        for name, value in fields:
            name = str(name)
            if name == "0":
                has_zero = True
            if name.startswith("1_"):
                name = "_" + name
            normalized.append((name, value))
        if not has_zero:
            normalized.append(("0", '["$K1"]'))
        body, ct = _build_multipart(normalized)
        return self.s.post(
            url,
            data=body,
            headers={
                "accept": "text/x-component",
                "content-type": ct,
                "origin": AUTH_BASE,
                "referer": url,
                "next-action": self._action_id,
                "next-router-state-tree": router_state,
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
            },
            allow_redirects=False,
        )

    @staticmethod
    def _router_state_tree(path: str) -> str:
        """生成当前 AuthKit 使用的 Next router state tree。"""
        segments = [part for part in str(path or "").split("/") if part]
        node = ["__PAGE__", {}, None, None, 0]
        for segment in reversed(segments):
            node = [segment, {"children": node}, None, None, 0]
        route = [
            "",
            {
                "children": [
                    "(main)",
                    {"children": ["(root)", {"children": node}, None, None, 0]},
                    None,
                    None,
                    0,
                ]
            },
            None,
            None,
            16,
        ]
        return quote(json.dumps(route, separators=(",", ":")), safe="()")

    @staticmethod
    def _action_redirect(response) -> str:
        for header_name in ("x-action-redirect", "location"):
            value = str(response.headers.get(header_name, "") or "").strip()
            if value:
                return value
        body = str(response.text or "")
        match = re.search(
            r"(?:replace|push|redirect);(https?://[^;\\s\"]+|/[^;\\s\"]+)",
            body,
            re.IGNORECASE,
        )
        return match.group(1) if match else ""

    @classmethod
    def _action_succeeded(cls, response, expected_path: str) -> bool:
        if response.status_code == 303:
            return True
        if response.status_code >= 400:
            return False
        redirect = cls._action_redirect(response)
        body = re.sub(r"\\s+", " ", str(response.text or ""))
        if expected_path in redirect or expected_path in body:
            return True
        return not re.search(r'"(?:code|error)"\s*:\s*"(?:invalid_params|invalid_action)', body)

    def step1_initiate_signup(self) -> bool:
        """GET auth.openblocklabs.com/sign-up → authorization_session_id + action ID"""
        self.log("Step1: GET /sign-up")
        r = self.s.get(
            f"{AUTH_BASE}/sign-up",
            params={"redirect_uri": DASHBOARD_CALLBACK},
            headers=self._get_headers(),
            allow_redirects=True,
        )
        final_url = str(r.url)
        parsed = urlparse(final_url)
        qs = parse_qs(parsed.query)
        self.authorization_session_id = qs.get("authorization_session_id", [None])[0]
        if not self.authorization_session_id:
            for rr in r.history:
                loc = rr.headers.get("location", "")
                m = re.search(r"authorization_session_id=([^&]+)", loc)
                if m:
                    self.authorization_session_id = m.group(1)
                    break
        self._action_id = self._extract_action_id(r.text)
        self.log(
            f"  session_id={self.authorization_session_id}, action={self._action_id and self._action_id[:16]}..."
        )
        return bool(self.authorization_session_id)

    def step2_get_signup_page(self) -> bool:
        """已在 step1 完成，直接返回 True"""
        return bool(self.authorization_session_id)

    def step3_submit_signup(self, email: str, first_name: str, last_name: str) -> bool:
        """POST /sign-up (first_name/last_name/email/intent=sign-up) → 303 → /sign-up/password"""
        self.log(f"Step3: POST /sign-up email={email}")
        url = f"{AUTH_BASE}/sign-up?" + urlencode(
            {
                "redirect_uri": DASHBOARD_CALLBACK,
                "authorization_session_id": self.authorization_session_id,
            }
        )
        router_state = self._router_state_tree("sign-up")
        resp = self._post_action(
            url,
            [
                ("1_browser_supports_passkeys", "true"),
                # 当前 AuthKit 会在首个注册提交时校验 signals；空值会返回
                # digest 形式的 HTTP 500。协议模式没有 DOM 可以读取，复用
                # 后续密码步骤使用的结构化信号作为兼容回退。
                ("1_signals", _make_signals()),
                ("1_first_name", first_name),
                ("1_last_name", last_name),
                ("1_email", email),
                ("1_intent", "sign-up"),
                ("1_redirect_uri", DASHBOARD_CALLBACK),
                ("1_authorization_session_id", self.authorization_session_id),
            ],
            router_state,
        )
        self.log(f"  -> {resp.status_code}")
        return self._action_succeeded(resp, "/sign-up/password")

    def step4_get_password_page(self) -> bool:
        """GET /sign-up/password → 提取 next-action ID"""
        self.log("Step4: GET /sign-up/password")
        url = f"{AUTH_BASE}/sign-up/password?" + urlencode(
            {
                "redirect_uri": DASHBOARD_CALLBACK,
                "authorization_session_id": self.authorization_session_id,
            }
        )
        r = self.s.get(
            url,
            headers=self._get_headers(referer=f"{AUTH_BASE}/sign-up"),
            allow_redirects=True,
        )
        self.log(f"  -> {r.status_code}")
        action = self._extract_action_id(r.text)
        if action:
            self._action_id = action
            self.log(f"  action={action[:16]}...")
        # WorkOS returns 404 even on success; action ID is the real signal
        return r.status_code in (200, 404) and action is not None

    def step5_submit_password(
        self, email: str, password: str, first_name: str, last_name: str
    ) -> str:
        """POST /sign-up/password → RSC body 包含 pendingAuthenticationToken"""
        self.log("Step5: POST /sign-up/password")
        url = f"{AUTH_BASE}/sign-up/password?" + urlencode(
            {
                "redirect_uri": DASHBOARD_CALLBACK,
                "authorization_session_id": self.authorization_session_id,
            }
        )
        router_state = self._router_state_tree("sign-up/password")
        resp = self._post_action(
            url,
            [
                ("1_browser_supports_passkeys", "true"),
                ("1_signals", _make_signals()),
                ("1_first_name", first_name),
                ("1_last_name", last_name),
                ("1_email", email),
                ("1_password", password),
                ("1_intent", "sign-up"),
                ("1_redirect_uri", DASHBOARD_CALLBACK),
                ("1_authorization_session_id", self.authorization_session_id),
            ],
            router_state,
        )
        self.log(f"  -> {resp.status_code}")
        body = resp.text
        m = re.search(r'"pendingAuthenticationToken"\s*:\s*"([^"]+)"', body)
        token = m.group(1) if m else None
        self.log(f"  pendingAuthenticationToken={token}")
        if not token:
            self.log(f"  body[:600]: {body[:600]}")
        return token

    def step6_get_email_verification_page(self) -> bool:
        """GET /email-verification → 提取 next-action ID"""
        self.log("Step6: GET /email-verification")
        url = f"{AUTH_BASE}/email-verification?" + urlencode(
            {
                "redirect_uri": DASHBOARD_CALLBACK,
                "authorization_session_id": self.authorization_session_id,
            }
        )
        r = self.s.get(
            url,
            headers=self._get_headers(referer=f"{AUTH_BASE}/sign-up/password"),
            allow_redirects=True,
        )
        self.log(f"  -> {r.status_code}")
        action = self._extract_action_id(r.text)
        if action:
            self._action_id = action
            self.log(f"  action={action[:16]}...")
        return r.status_code in (200, 404) and action is not None

    def step7_submit_otp(self, email: str, code: str, pending_auth_token: str) -> str:
        """POST /email-verification → 303 → dashboard/auth/callback?code=..."""
        self.log("Step7: POST /email-verification")
        url = f"{AUTH_BASE}/email-verification?" + urlencode(
            {
                "redirect_uri": DASHBOARD_CALLBACK,
                "authorization_session_id": self.authorization_session_id,
            }
        )
        fields = [
            ("1_code", code),
            ("1_redirect_uri", DASHBOARD_CALLBACK),
            ("1_authorization_session_id", self.authorization_session_id),
            ("1_email", email),
        ]
        if pending_auth_token:
            fields.append(("1_pending_authentication_token", pending_auth_token))
        resp = self._post_action(
            url,
            fields,
            self._router_state_tree("(fixed-layout)/email-verification"),
        )
        self.log(f"  -> {resp.status_code}")
        redirect = self._action_redirect(resp)
        redirect_target = redirect.split(";", 1)[0].strip()
        self.log(
            f"  x-action-redirect: {'present' if redirect_target else 'none'}"
        )
        if not redirect:
            self.log(f"  body[:400]: {resp.text[:400]}")
        auth_code = None
        if redirect_target:
            callback_url = urljoin(url, redirect_target)
            callback_response = self.s.get(
                callback_url,
                headers=self._get_headers(referer=url),
                allow_redirects=True,
            )
            self._session_token = next(
                (
                    str(cookie.value)
                    for cookie in self.s.cookies.jar
                    if cookie.name == "wos-session" and cookie.value
                ),
                "",
            )
            final_url = str(callback_response.url or callback_url)
            auth_code = parse_qs(urlparse(final_url).query).get("code", [None])[0]
            self.log(
                f"  success redirect followed: HTTP {callback_response.status_code}, "
                f"session={'present' if self._session_token else 'missing'}"
            )
        self.log(f"  auth_code={'present' if auth_code else 'missing'}")
        return auth_code

    def step8_exchange_callback(self, auth_code: str) -> str:
        """GET dashboard/auth/callback?code=... → wos-session cookie"""
        if self._session_token:
            return self._session_token
        self.log("Step8: GET /auth/callback")
        url = f"{DASHBOARD_CALLBACK}?code={auth_code}"
        r = self.s.get(
            url, headers=self._get_headers(referer=AUTH_BASE), allow_redirects=True
        )
        self.log(f"  -> {r.status_code} final={str(r.url)[:80]}")
        for c in self.s.cookies.jar:
            if "wos-session" in c.name:
                return c.value
        return None

    def step9_create_personal_org(self) -> bool:
        """GET /api/create-personal-org → 完成组织创建"""
        self.log("Step9: GET /api/create-personal-org")
        r = self.s.get(
            f"{DASHBOARD_BASE}/api/create-personal-org",
            headers=self._get_headers(referer=f"{DASHBOARD_BASE}/"),
            allow_redirects=True,
        )
        self.log(f"  -> {r.status_code} final={str(r.url)[:80]}")
        return r.status_code in (200, 404)

    def register(
        self,
        email: str = None,
        password: str = None,
        first_name: str = None,
        last_name: str = None,
        account_id: str = None,
        otp_callback=None,
    ) -> dict:
        if not password:
            password = _rand_password()
        if not first_name:
            first_name = "".join(
                random.choices(string.ascii_lowercase, k=5)
            ).capitalize()
        if not last_name:
            last_name = random.choice(string.ascii_uppercase)

        if not self.step1_initiate_signup():
            return {"success": False, "error": "initiate_signup failed"}
        if not self.step2_get_signup_page():
            return {"success": False, "error": "get_signup_page failed"}
        if not self.step3_submit_signup(email, first_name, last_name):
            return {"success": False, "error": "submit_signup failed"}
        if not self.step4_get_password_page():
            return {"success": False, "error": "get_password_page failed"}

        pending_token = self.step5_submit_password(
            email, password, first_name, last_name
        )
        if pending_token is None:
            return {
                "success": False,
                "error": "submit_password failed (email may already be registered)",
            }

        if not self.step6_get_email_verification_page():
            return {"success": False, "error": "get_email_verification_page failed"}

        if not otp_callback:
            raise RuntimeError("otp_callback is required")
        otp = otp_callback()
        if not otp:
            return {"success": False, "error": "OTP timeout"}

        auth_code = self.step7_submit_otp(email, otp, pending_token)
        if not auth_code:
            return {"success": False, "error": "submit_otp failed / no auth_code"}

        session_token = self._session_token or self.step8_exchange_callback(auth_code)
        if not session_token:
            return {
                "success": False,
                "error": "exchange_callback failed / no wos-session",
            }

        self.step9_create_personal_org()

        result = {
            "success": True,
            "email": email,
            "password": password,
            "wos_session": session_token,
        }
        self.log(f"注册成功: {email}")
        return result
