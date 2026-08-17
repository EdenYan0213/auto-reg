"""Cursor AuthKit 浏览器注册流程。

Cursor 的注册表单会先调用 signFingerprint，再由页面动态提交 Server
Action。纯 HTTP 请求缺少这段浏览器信号时会得到泛化的 500；这里沿用
Grok 的 patchright 浏览器生命周期，并在同一会话中完成邮箱、密码和 OTP。
"""

from __future__ import annotations

import hashlib
import re
import time
import urllib.parse
from pathlib import Path
from typing import Callable, Optional

from core.proxy_utils import build_playwright_proxy_config
from platforms.cursor.core import AUTH, CursorRegister, _rand_password


class CursorBrowserCaptchaTimeout(TimeoutError):
    """浏览器没有完成 Cursor 的真人验证，可切换到 headed 模式。"""


class CursorBrowserManualStepRequired(TimeoutError):
    """Cursor 需要用户在 headed 浏览器中完成手机号等人工步骤。"""


class CursorBrowserRegister:
    def __init__(
        self,
        mailbox,
        proxy: str | None = None,
        log_fn: Callable = print,
        headless: bool = False,
        profile_dir: str | None = None,
        human_timeout_seconds: int = 180,
        http_bootstrap: bool = False,
    ):
        self.mailbox = mailbox
        self.proxy = proxy
        self.log = log_fn
        self.headless = bool(headless)
        self.profile_dir = str(profile_dir or "").strip()
        try:
            self.human_timeout_seconds = max(60, int(human_timeout_seconds))
        except (TypeError, ValueError):
            self.human_timeout_seconds = 180
        self.http_bootstrap = bool(http_bootstrap)

    def _checkpoint(self) -> None:
        checkpoint = getattr(self.mailbox, "_checkpoint", None)
        if callable(checkpoint):
            checkpoint()

    def _profile_path(self, email: str) -> Path:
        """为每个邮箱保留独立的 Chrome 配置，避免并发任务互相抢锁。"""
        root = Path(self.profile_dir).expanduser() if self.profile_dir else (
            Path(__file__).resolve().parents[2] / "data" / "cursor" / "browser-profiles"
        )
        digest = hashlib.sha256(str(email).strip().lower().encode()).hexdigest()[:20]
        path = root / digest
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _launch(self, playwright, profile_path: Path):
        options = {"headless": self.headless}
        if self.proxy:
            options["proxy"] = build_playwright_proxy_config(self.proxy)
        errors = []

        # 持久化 context 会使用真正的 Chrome 配置，而不是每次都创建一个新的
        # incognito context。headed 模式不覆盖 viewport/UA，让 Chrome 自己报告
        # 与实际版本一致的浏览器信息。
        for channel in ("chrome", "msedge", None):
            current = dict(options)
            if channel:
                current["channel"] = channel
            try:
                current["no_viewport"] = not self.headless
                context = playwright.chromium.launch_persistent_context(
                    str(profile_path), **current
                )
                return None, context
            except Exception as exc:  # pragma: no cover - machine dependent
                errors.append(f"{channel or 'bundled'}: {exc}")

        # 某些机器上的 Chrome 配置目录可能被占用；保留一次临时 context
        # 回退，确保已有的 protocol/headed 行为仍能工作。
        for channel in ("chrome", "msedge", None):
            current = dict(options)
            if channel:
                current["channel"] = channel
            try:
                browser = playwright.chromium.launch(**current)
                return browser, None
            except Exception as exc:  # pragma: no cover - machine dependent
                errors.append(f"temporary {channel or 'bundled'}: {exc}")
        raise RuntimeError("Cursor 浏览器启动失败: " + "; ".join(errors[-5:]))

    @staticmethod
    def _cookie_payload(cookie) -> dict:
        if cookie.name.startswith("__Host-"):
            return {
                "name": cookie.name,
                "value": cookie.value,
                "url": "https://authenticator.cursor.sh" + (cookie.path or "/"),
            }
        return {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain or "authenticator.cursor.sh",
            "path": cookie.path or "/",
        }

    def _bootstrap(self) -> tuple[CursorRegister, str, list[dict]]:
        # 先用 curl_cffi 建立 WorkOS/Cloudflare session，再把 cookie 交给
        # patchright。这样页面不需要从空白浏览器重新过一遍 Cloudflare。
        http = CursorRegister(proxy=self.proxy, log_fn=lambda _: None)
        http.step1_get_session()
        context = http._page_context or {}
        signup_url = context.get("url", "")
        if not signup_url:
            raise RuntimeError("Cursor 注册页地址为空")
        cookies = [self._cookie_payload(cookie) for cookie in http.s.cookies.jar]
        return http, signup_url, cookies

    @staticmethod
    def _entry_url() -> str:
        # 使用 AuthKit 的标准入口，让它自行生成 state/session_id。
        return f"{AUTH}/"

    def _open_signup_page(self, page, entry_url: str) -> None:
        page.goto(entry_url, wait_until="domcontentloaded", timeout=60000)
        if "/sign-up" in page.url:
            return

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            signup = page.locator('a[href*="sign-up"]')
            if signup.count():
                href = signup.first.get_attribute("href") or ""
                if href:
                    page.goto(
                        urllib.parse.urljoin(page.url, href),
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )
                    return
            page.wait_for_timeout(500)
        raise RuntimeError("Cursor 页面未找到注册入口")

    def _wait_for_signup_form(self, page, timeout: int | None = None) -> None:
        email = page.locator('input[name="email"]')
        timeout = timeout or self.human_timeout_seconds
        deadline = time.monotonic() + timeout
        challenge_logged = False
        while time.monotonic() < deadline:
            self._checkpoint()
            if email.count() and email.first.is_visible():
                return
            body = " ".join(page.locator("body").inner_text().split()).lower()
            if not challenge_logged and any(
                marker in body
                for marker in (
                    "安全验证",
                    "security verification",
                    "cloudflare",
                    "just a moment",
                    "checking your browser",
                    "verify you are human",
                    "验证你是人类",
                )
            ):
                challenge_logged = True
                self.log("Cursor 正在等待 Cloudflare 真人验证...")
            page.wait_for_timeout(1000)
        if challenge_logged and self.headless:
            raise CursorBrowserCaptchaTimeout("Cursor 无头浏览器未通过 Cloudflare 真人验证")
        raise TimeoutError("Cursor 注册页等待 Cloudflare 真人验证超时")

    @staticmethod
    def _password_input(page):
        for selector in ('input[type="password"]', 'input[name="password"]'):
            locator = page.locator(selector)
            for index in range(locator.count()):
                candidate = locator.nth(index)
                if candidate.is_visible():
                    return candidate
        return None

    @staticmethod
    def _otp_input(page):
        for selector in (
            'input[name="code"]',
            'input[name="otp"]',
            'input[name="verification_code"]',
            'input[name="magic_code"]',
            'input[autocomplete="one-time-code"]',
        ):
            locator = page.locator(selector)
            for index in range(locator.count()):
                candidate = locator.nth(index)
                if candidate.is_visible():
                    return candidate
        inputs = page.locator("input")
        for index in range(inputs.count()):
            locator = inputs.nth(index)
            if not locator.is_visible():
                continue
            marker = " ".join(
                str(locator.get_attribute(name) or "")
                for name in ("name", "id", "autocomplete", "placeholder", "inputmode")
            ).lower()
            if any(value in marker for value in ("code", "otp", "verification")):
                return locator
        return None

    @staticmethod
    def _phone_input(page):
        for selector in (
            'input[type="tel"]',
            'input[name="phone"]',
            'input[name="phone_number"]',
            'input[name="mobile"]',
            'input[autocomplete="tel"]',
        ):
            locator = page.locator(selector)
            for index in range(locator.count()):
                candidate = locator.nth(index)
                if candidate.is_visible():
                    return candidate
        return None

    @staticmethod
    def _token(context) -> str:
        for cookie in context.cookies():
            if cookie.get("name") == "WorkosCursorSessionToken":
                return str(cookie.get("value") or "")
        return ""

    def _wait_for_signal(self, page, timeout: int = 20) -> bool:
        deadline = time.monotonic() + timeout
        signal = page.locator('input[name="signals"]')
        while time.monotonic() < deadline:
            self._checkpoint()
            if signal.count() and str(signal.input_value() or "").strip():
                return True
            page.wait_for_timeout(1000)
        return False

    def _register_once(
        self,
        email: str,
        password: str,
        otp_callback: Optional[Callable[[], str]],
        otp_timeout: int,
    ) -> dict:
        from patchright.sync_api import sync_playwright

        playwright = sync_playwright().start()
        browser = None
        context = None
        try:
            signup_url, cookies = self._entry_url(), []
            if self.http_bootstrap:
                try:
                    _http, signup_url, cookies = self._bootstrap()
                except Exception as exc:
                    # Cursor/Cloudflare may reject the curl_cffi warm-up even though
                    # the same entry point is available in a real browser session.
                    self.log(f"Cursor HTTP 会话预热失败，改用浏览器直接进入注册页: {exc}")
                    signup_url, cookies = self._entry_url(), []

            profile_path = self._profile_path(email)
            self.log(f"Cursor 使用独立 Chrome 配置: {profile_path.name}")
            browser, context = self._launch(playwright, profile_path)
            if context is None:
                context_options = (
                    {"viewport": {"width": 1400, "height": 1200}}
                    if self.headless
                    else {"no_viewport": True}
                )
                context = browser.new_context(**context_options)
            if cookies:
                context.add_cookies(cookies)
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(15000)
            page.set_default_navigation_timeout(60000)
            self._open_signup_page(page, signup_url)
            self._wait_for_signup_form(page)

            has_signal = self._wait_for_signal(page)
            if not has_signal and self.headless:
                raise CursorBrowserCaptchaTimeout(
                    "Cursor 浏览器未生成真人验证信号"
                )
            if not has_signal:
                self.log("Cursor 页面需要真人验证，请在打开的浏览器窗口中完成...")

            page.locator('input[name="email"]').fill(email)
            for name, value in (("first_name", "Alex"), ("last_name", "Morgan")):
                field = page.locator(f'input[name="{name}"]')
                if field.count():
                    field.fill(value)
            page.locator('button[type="submit"]').first.click(timeout=15000)

            stage = "email"
            human_logged = False
            phone_required = False
            deadline = time.monotonic() + max(
                420, self.human_timeout_seconds + int(otp_timeout) + 180
            )
            while time.monotonic() < deadline:
                self._checkpoint()
                token = self._token(context)
                if token:
                    return {"email": email, "password": password, "token": token}

                password_input = self._password_input(page)
                if password_input and stage != "password":
                    stage = "password"
                    self.log("Cursor 邮箱已接受，设置密码...")
                    password_input.fill(password)
                    page.locator('button[type="submit"]').first.click(timeout=15000)
                    self.log(f"Cursor 密码已提交，等待验证页面 ({page.url})...")
                    page.wait_for_timeout(1500)
                    continue

                phone_input = self._phone_input(page)
                if phone_input:
                    phone_required = True
                    if not str(phone_input.input_value() or "").strip():
                        if self.headless:
                            raise CursorBrowserManualStepRequired(
                                "Cursor 注册需要手机号，无法在无头浏览器中继续"
                            )
                        if stage != "phone":
                            stage = "phone"
                            self.log(
                                "Cursor 注册需要手机号/短信验证，请在打开的浏览器窗口中手动完成..."
                            )
                        page.wait_for_timeout(1000)
                        continue

                otp_input = self._otp_input(page)
                if otp_input:
                    if phone_required:
                        if self.headless:
                            raise CursorBrowserManualStepRequired(
                                "Cursor 注册需要手机短信验证码，无法在无头浏览器中继续"
                            )
                        if stage != "phone_code":
                            stage = "phone_code"
                            self.log(
                                "Cursor 正在等待手机短信验证码，请在浏览器窗口中手动输入..."
                            )
                        page.wait_for_timeout(1000)
                        continue
                    if stage != "otp":
                        stage = "otp"
                        self.log("等待 Cursor 验证码...")
                        code = otp_callback() if otp_callback else input("OTP: ")
                        if not code:
                            raise RuntimeError("未获取到 Cursor 验证码")
                        otp_input.fill(str(code).replace("-", "").replace(" ", ""))
                        page.locator('button[type="submit"]').first.click(timeout=15000)
                    page.wait_for_timeout(1500)
                    continue

                if page.url.startswith("https://cursor.com/"):
                    page.wait_for_timeout(1500)
                    continue

                body = re.sub(r"\s+", " ", page.locator("body").inner_text())
                lowered = body.lower()
                if any(
                    marker in lowered
                    for marker in ("phone number", "phone verification", "手机号", "短信验证")
                ):
                    phone_required = True
                    if self.headless:
                        raise CursorBrowserManualStepRequired(
                            "Cursor 注册需要手机号/短信验证，无法在无头浏览器中继续"
                        )
                    if stage != "phone":
                        stage = "phone"
                        self.log(
                            "Cursor 注册需要手机号/短信验证，请在打开的浏览器窗口中手动完成..."
                        )
                if any(marker in lowered for marker in ("human", "captcha", "verify")):
                    if self.headless:
                        raise CursorBrowserCaptchaTimeout(body[:180])
                    if not human_logged:
                        human_logged = True
                        self.log("Cursor 真人验证仍在等待中...")
                page.wait_for_timeout(1000)

            raise TimeoutError("Cursor 浏览器注册超时")
        finally:
            try:
                if context:
                    context.close()
            except Exception:
                pass
            try:
                if browser:
                    browser.close()
            except Exception:
                pass
            try:
                playwright.stop()
            except Exception:
                pass

    def register(
        self,
        email: str,
        password: str,
        otp_callback: Optional[Callable[[], str]] = None,
        otp_timeout: int = 120,
    ) -> dict:
        # 与协议模式保持一致：未指定密码时生成并返回随机密码，确保保存到
        # 账号记录后仍然可以登录；不能把空密码提交给 AuthKit。
        password = password or _rand_password()
        try:
            return self._register_once(email, password, otp_callback, otp_timeout)
        except CursorBrowserCaptchaTimeout:
            if self.headless:
                self.log("无头浏览器未通过 Cursor 真人验证，切换到 headed 浏览器重试...")
                self.headless = False
                return self._register_once(email, password, otp_callback, otp_timeout)
            raise
        except CursorBrowserManualStepRequired:
            # 手机号/短信验证通常发生在邮箱已提交之后，此时重启注册会话
            # 造成重复注册或“邮箱已存在”；让用户直接选择 headed 模式继续。
            raise
