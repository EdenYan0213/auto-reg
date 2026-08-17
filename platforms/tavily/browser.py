"""Tavily 注册浏览器流程。

Auth0 的 Tavily 注册页会在页面运行时生成 Turnstile token，并在注册后
发送“验证链接”邮件，而不是六位数字验证码。验证码、注册会话和验证
链接必须在同一个浏览器上下文中完成，否则 /api/keys 会返回空列表。
"""

from __future__ import annotations

import html
import json
import re
import time
from typing import Callable, Optional

from core.proxy_utils import build_playwright_proxy_config


AUTH0_BASE = "https://auth.tavily.com"
APP_BASE = "https://app.tavily.com"
AUTH0_CLIENT_ID = "RRIAvvXNFxpfTWIozX1mXqLnyUmYSTrQ"
REDIRECT_URI = f"{APP_BASE}/api/auth/callback"

_VERIFY_LINK_RE = re.compile(
    r"https://auth\.tavily\.com/u/email-verification\?ticket=[^\s<>\"']+",
    re.IGNORECASE,
)


class TavilyBrowserCaptchaTimeout(TimeoutError):
    """无头浏览器没有拿到 Auth0 Turnstile token，可安全切换到有头模式。"""


class TavilyBrowserRegister:
    def __init__(
        self,
        mailbox,
        proxy: str | None = None,
        log_fn: Callable = print,
        headless: bool = False,
    ):
        self.mailbox = mailbox
        self.proxy = proxy
        self.log = log_fn
        self.headless = bool(headless)

    def _checkpoint(self) -> None:
        checkpoint = getattr(self.mailbox, "_checkpoint", None)
        if callable(checkpoint):
            checkpoint()

    def _launch(self, playwright):
        launch_options = {"headless": self.headless}
        if self.proxy:
            launch_options["proxy"] = build_playwright_proxy_config(self.proxy)

        errors = []
        for channel in ("chrome", "msedge", None):
            options = dict(launch_options)
            if channel:
                options["channel"] = channel
            try:
                return playwright.chromium.launch(**options)
            except Exception as exc:  # pragma: no cover - machine dependent
                errors.append(f"{channel or 'bundled'}: {exc}")
        raise RuntimeError(
            "所有浏览器启动方式均失败: " + "; ".join(errors[-3:])
        )

    @staticmethod
    def _authorize_url() -> str:
        from urllib.parse import urlencode

        params = {
            "client_id": AUTH0_CLIENT_ID,
            "scope": "openid profile email",
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "screen_hint": "signup",
        }
        return f"{AUTH0_BASE}/authorize?{urlencode(params)}"

    def _wait_for_captcha(self, page, timeout: int = 60) -> str:
        deadline = time.monotonic() + timeout
        captcha = page.locator('input[name="captcha"]')
        while time.monotonic() < deadline:
            self._checkpoint()
            if captcha.count():
                value = str(captcha.input_value() or "").strip()
                if value:
                    return value
            page.wait_for_timeout(1000)
        raise TavilyBrowserCaptchaTimeout(
            "Tavily Turnstile 未在当前浏览器会话中完成，请选择 headed 模式重试"
        )

    def _messages(self, email: str) -> list:
        getter = getattr(self.mailbox, "_get_mails", None)
        if not callable(getter):
            raise RuntimeError(
                "Tavily 注册需要邮箱服务提供原始邮件读取接口，当前邮箱服务不支持验证链接"
            )
        try:
            messages = getter(email)
        except Exception as exc:
            self.log(f"读取 Tavily 验证邮件失败: {exc}")
            return []
        return messages if isinstance(messages, list) else []

    @staticmethod
    def _message_text(mailbox, message: dict) -> str:
        parts = []
        for key in ("subject", "raw", "body", "content", "text", "html"):
            value = message.get(key)
            if value:
                parts.append(str(value))
        raw = "\n".join(parts)
        decoder = getattr(mailbox, "_decode_raw_content", None)
        if callable(decoder):
            try:
                raw = decoder(raw)
            except Exception:
                pass
        return html.unescape(raw)

    def _find_verification_link(self, email: str, before_ids: set) -> str:
        for message in self._messages(email):
            message_id = str(message.get("id", ""))
            if message_id and message_id in before_ids:
                continue
            text = self._message_text(self.mailbox, message)
            match = _VERIFY_LINK_RE.search(text)
            if not match:
                continue
            link = html.unescape(match.group(0)).rstrip("#),.;")
            return link
        return ""

    def _wait_for_verification_link(
        self, email: str, before_ids: set, timeout: int
    ) -> str:
        self.log("等待 Tavily 邮箱验证链接...")
        deadline = time.monotonic() + max(int(timeout), 1)
        while time.monotonic() < deadline:
            self._checkpoint()
            link = self._find_verification_link(email, before_ids)
            if link:
                return link
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(3, remaining))
        raise TimeoutError(f"等待 Tavily 邮箱验证链接超时 ({timeout}s)")

    @staticmethod
    def _keys_from_response(result: dict) -> list:
        try:
            return json.loads(str(result.get("text") or ""))
        except Exception:
            return []

    def _get_api_key(self, page) -> str:
        for _ in range(8):
            result = page.evaluate(
                """async () => {
                    const response = await fetch('/api/keys', {credentials: 'include'});
                    return {status: response.status, text: await response.text()};
                }"""
            )
            if int(result.get("status", 0)) == 200:
                keys = self._keys_from_response(result)
                for item in keys if isinstance(keys, list) else []:
                    key = str(item.get("key", "") or "").strip()
                    if key:
                        return key
            page.wait_for_timeout(2500)
        raise RuntimeError("Tavily 注册完成但未获取到 API Key，请确认邮箱验证已完成")

    def _register_once(
        self,
        email: str,
        password: str,
        before_ids: set,
        otp_timeout: int,
    ) -> dict:
        from patchright.sync_api import sync_playwright

        playwright = sync_playwright().start()
        browser = None
        context = None
        try:
            browser = self._launch(playwright)
            context = browser.new_context(viewport={"width": 1400, "height": 1200})
            page = context.new_page()
            page.goto(self._authorize_url(), wait_until="domcontentloaded", timeout=60000)
            page.locator('input[name="email"]').wait_for(state="visible", timeout=30000)

            self.log("等待 Tavily Turnstile...")
            self._wait_for_captcha(page)
            page.locator('input[name="email"]').fill(email)
            page.locator('button[type="submit"]').first.click(timeout=15000)

            page.locator('input[name="password"]').wait_for(
                state="visible", timeout=60000
            )
            page.locator('input[name="password"]').fill(password)
            page.locator('button[type="submit"]').first.click(timeout=15000)
            page.wait_for_url(re.compile(r"https://app\.tavily\.com/"), timeout=60000)
            page.wait_for_timeout(3000)

            link = self._wait_for_verification_link(email, before_ids, otp_timeout)
            page.goto(link, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(8000)
            api_key = self._get_api_key(page)
            return {"email": email, "password": password, "api_key": api_key}
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
        before_ids: Optional[set] = None,
        otp_timeout: int = 120,
    ) -> dict:
        before_ids = set(before_ids or set())
        try:
            return self._register_once(email, password, before_ids, otp_timeout)
        except TavilyBrowserCaptchaTimeout:
            if self.headless:
                self.log("无头浏览器未通过 Turnstile，切换到 headed 浏览器重试...")
                self.headless = False
                return self._register_once(email, password, before_ids, otp_timeout)
            raise
