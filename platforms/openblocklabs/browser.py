"""OpenBlockLabs 的真实浏览器注册流程。

OpenBlockLabs 在邮箱验证码提交后会先进入 ``/success``，再由页面脚本
完成 WorkOS 会话跳转。纯 HTTP 可以走到验证码提交，但拿不到最终的
``wos-session``；这里让整个 AuthKit 注册流程在同一个浏览器上下文中完成。

Cloudflare/Turnstile 只等待用户在有头浏览器中完成，不在代码里自动点击
或绕过验证。
"""

from __future__ import annotations

import random
import re
import string
import time
from typing import Callable, Optional
from urllib.parse import quote

from core.proxy_utils import build_playwright_proxy_config
from platforms.openblocklabs.core import AUTH_BASE, CLIENT_ID, DASHBOARD_BASE, DASHBOARD_CALLBACK, _rand_password


class OpenBlockLabsBrowserCaptchaTimeout(TimeoutError):
    """无头浏览器遇到真人验证，需要改用有头浏览器手动完成。"""


class OpenBlockLabsBrowserRegister:
    def __init__(
        self,
        mailbox,
        proxy: str | None = None,
        log_fn: Callable[[str], None] = print,
        headless: bool = False,
        human_timeout_seconds: int = 180,
    ):
        self.mailbox = mailbox
        self.proxy = proxy
        self.log = log_fn
        self.headless = bool(headless)
        try:
            self.human_timeout_seconds = max(60, int(human_timeout_seconds))
        except (TypeError, ValueError):
            self.human_timeout_seconds = 180

    def _checkpoint(self) -> None:
        checkpoint = getattr(self.mailbox, "_checkpoint", None)
        if callable(checkpoint):
            checkpoint()

    def _launch(self, playwright):
        options = {"headless": self.headless}
        if self.proxy:
            options["proxy"] = build_playwright_proxy_config(self.proxy)

        errors = []
        for channel in ("chrome", "msedge", None):
            current = dict(options)
            if channel:
                current["channel"] = channel
            try:
                return playwright.chromium.launch(**current)
            except Exception as exc:  # pragma: no cover - machine dependent
                errors.append(f"{channel or 'bundled'}: {exc}")
        raise RuntimeError("所有浏览器启动方式均失败: " + "; ".join(errors[-3:]))

    @staticmethod
    def _visible(page, selectors: list[str], *, enabled: bool = False):
        for selector in selectors:
            try:
                locator = page.locator(selector)
                for index in range(locator.count()):
                    item = locator.nth(index)
                    if not item.is_visible():
                        continue
                    if enabled and not item.is_enabled():
                        continue
                    return item, selector
            except Exception:
                continue
        return None, ""

    @staticmethod
    def _has_challenge(page) -> bool:
        try:
            body = (page.locator("body").inner_text() or "").lower()
            markers = (
                "just a moment",
                "checking your browser",
                "verifying you are human",
                "verify you are human",
                "performing security verification",
                "security check to access",
                "ray id",
            )
            if any(marker in body for marker in markers):
                return True
            return any(
                "challenges.cloudflare.com" in str(frame.url or "")
                for frame in page.frames
            )
        except Exception:
            return False

    def _wait_for_visible(
        self,
        page,
        selectors: list[str],
        label: str,
        timeout: int | None = None,
    ):
        deadline = time.monotonic() + max(
            int(timeout or self.human_timeout_seconds), 1
        )
        warned = False
        while time.monotonic() < deadline:
            self._checkpoint()
            item, used_selector = self._visible(page, selectors)
            if item:
                return item, used_selector

            if self._has_challenge(page):
                if self.headless:
                    raise OpenBlockLabsBrowserCaptchaTimeout(
                        "检测到 Cloudflare 真人验证，请切换到有头浏览器后手动完成"
                    )
                if not warned:
                    self.log(
                        "检测到 Cloudflare 真人验证，请在打开的浏览器窗口中手动完成；"
                        "程序不会自动点击或绕过验证"
                    )
                    warned = True
            page.wait_for_timeout(500)
        raise TimeoutError(f"等待 OpenBlockLabs {label} 超时，当前页面: {page.url}")

    def _fill(self, page, selectors: list[str], value: str, label: str) -> None:
        item, selector = self._wait_for_visible(page, selectors, label)
        item.fill(str(value or ""))
        self.log(f"已填写{label}: {selector}")

    def _click_submit(self, page, label: str = "继续") -> None:
        item, selector = self._wait_for_visible(
            page,
            [
                'button[type="submit"]',
                'button:has-text("继续")',
                'button:has-text("Continue")',
                'button:has-text("Sign up")',
            ],
            f"{label}按钮",
            timeout=30,
        )
        item.click(timeout=10000)
        self.log(f"已点击{label}按钮: {selector}")

    def _entry_url(self) -> str:
        return (
            f"{AUTH_BASE}/?client_id={CLIENT_ID}"
            f"&redirect_uri={quote(DASHBOARD_CALLBACK, safe='')}"
        )

    @staticmethod
    def _random_name() -> tuple[str, str]:
        first = "".join(random.choices(string.ascii_lowercase, k=5)).capitalize()
        last = "".join(random.choices(string.ascii_lowercase, k=5)).capitalize()
        return first, last

    def _get_wos_session(self, context) -> str:
        try:
            for cookie in context.cookies():
                if cookie.get("name") == "wos-session" and cookie.get("value"):
                    return str(cookie["value"])
        except Exception:
            pass
        return ""

    def _wait_for_dashboard(self, page, context, timeout: int = 90) -> str:
        deadline = time.monotonic() + max(int(timeout), 1)
        while time.monotonic() < deadline:
            self._checkpoint()
            session = self._get_wos_session(context)
            if session:
                return session
            if "dashboard.openblocklabs.com" in str(page.url or ""):
                # URL 到 dashboard 但 cookie 尚未写入时，再给页面脚本一点时间。
                page.wait_for_timeout(500)
                session = self._get_wos_session(context)
                if session:
                    return session
            page.wait_for_timeout(500)
        raise RuntimeError(
            "验证码已提交但未获取到 wos-session，当前页面: "
            + str(page.url or "")
        )

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
        try:
            browser = self._launch(playwright)
            context = browser.new_context(viewport={"width": 1400, "height": 1000})
            page = context.new_page()
            page.goto(
                self._entry_url(),
                wait_until="domcontentloaded",
                timeout=60000,
            )
            self._wait_for_visible(
                page,
                [
                    'input[type="email"]',
                    'input[name="email"]',
                    'input[autocomplete="email"]',
                ],
                "邮箱表单",
            )

            signup_link, _ = self._visible(page, ['a[href*="/sign-up"]'])
            if signup_link:
                signup_link.click(timeout=10000)
            else:
                raise RuntimeError("OpenBlockLabs 页面未找到注册入口")

            self._fill(
                page,
                ['input[name="last_name"]', 'input[autocomplete="family-name"]'],
                self._random_name()[1],
                "姓氏",
            )
            self._fill(
                page,
                ['input[name="first_name"]', 'input[autocomplete="given-name"]'],
                self._random_name()[0],
                "名字",
            )
            self._fill(
                page,
                [
                    'input[name="email"]',
                    'input[type="email"]',
                    'input[autocomplete="email"]',
                ],
                email,
                "邮箱",
            )
            self._click_submit(page, "注册信息提交")

            self._fill(
                page,
                [
                    'input[name="password"]',
                    'input[type="password"]',
                    'input[autocomplete="new-password"]',
                ],
                password,
                "密码",
            )
            self._click_submit(page, "密码提交")

            otp_input, _ = self._wait_for_visible(
                page,
                [
                    'input[autocomplete="one-time-code"]',
                    'input[data-test="otp-input"]',
                    'input[data-index="0"]',
                ],
                "邮箱验证码输入框",
                timeout=60,
            )
            if not self.mailbox:
                raise RuntimeError("OpenBlockLabs 注册需要可读取验证码的邮箱服务")
            self.log("等待 OpenBlockLabs 邮箱验证码...")
            code = self.mailbox.wait_for_code(
                self.mailbox_account,
                keyword="",
                timeout=otp_timeout,
                before_ids=before_ids,
            )
            if not code:
                raise RuntimeError("未获取到 OpenBlockLabs 邮箱验证码")
            code = re.sub(r"\D", "", str(code))
            if not code:
                raise RuntimeError("OpenBlockLabs 邮箱验证码格式无效")

            otp_input.click()
            try:
                otp_input.fill(code)
            except Exception:
                page.keyboard.type(code, delay=80)
            self._click_submit(page, "验证码提交")
            session = self._wait_for_dashboard(page, context, timeout=90)

            # WorkOS 回调完成后，按参考流程访问一次个人组织接口；失败不影响
            # wos-session 的获取，避免把已成功注册误判为失败。
            try:
                page.goto(
                    f"{DASHBOARD_BASE}/api/create-personal-org",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            except Exception:
                pass

            self.log(f"OpenBlockLabs 注册成功: {email}")
            return {"email": email, "password": password, "wos_session": session}
        finally:
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
        password: str | None = None,
        mail_acct=None,
        before_ids: Optional[set] = None,
        otp_timeout: int = 120,
    ) -> dict:
        self.mailbox_account = mail_acct
        if not self.mailbox_account:
            raise RuntimeError("OpenBlockLabs 注册需要可读取验证码的邮箱服务")
        if not password:
            password = _rand_password()
        return self._register_once(
            email=email,
            password=password,
            before_ids=set(before_ids or set()),
            otp_timeout=otp_timeout,
        )
