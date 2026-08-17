#!/usr/bin/env python3
"""Debug: 跑完 _submit_register 后 dump cookies + URL，确认注册是否成功"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.base_mailbox import CFWorkerMailbox
from core.base_captcha import LocalSolverCaptcha
from platforms.grok.core import GrokRegister, _rand_name, _rand_password

CF_API_URL = os.getenv("CFWORKER_API_URL", "https://cy3124414.xyz")
CF_ADMIN_TOKEN = os.getenv("CFWORKER_ADMIN_TOKEN", "cfmailadmin2026")
CF_DOMAIN = os.getenv("CFWORKER_DOMAIN", "cy3124414.xyz")
SOLVER_URL = os.getenv("LOCAL_SOLVER_URL", "http://127.0.0.1:8889")


def log(msg):
    print(msg, flush=True)


def main():
    log("== Debug: 注册后 cookie 状态 ==")
    mailbox = CFWorkerMailbox(api_url=CF_API_URL, admin_token=CF_ADMIN_TOKEN, domain=CF_DOMAIN)
    acct = mailbox.get_email()
    email = acct.email
    log(f"邮箱: {email}")
    before_ids = mailbox.get_current_ids(acct)

    captcha_solver = LocalSolverCaptcha(SOLVER_URL)
    reg = GrokRegister(captcha_solver=captcha_solver, log_fn=log)

    password = _rand_password()
    given_name = _rand_name()
    family_name = _rand_name()

    from patchright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.launch(headless=False, channel="chrome")
    except Exception:
        browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.set_viewport_size({"width": 1400, "height": 1200})

    reg._goto_email_signup(page)
    reg._submit_email(page, email)

    log("等待验证码...")
    code = mailbox.wait_for_code(
        acct, keyword="", timeout=120, before_ids=before_ids,
        code_pattern=r"[A-Z0-9]{3}-[A-Z0-9]{3}",
    )
    if code:
        code = code.replace("-", "").replace(" ", "")
        log(f"验证码: {code}")
    if not code:
        raise RuntimeError("未获取到验证码")

    reg._submit_otp(page, code)
    reg._fill_user_form(page, given_name, family_name, password)
    reg._solve_turnstile_on_page(page)
    reg._submit_register(page)

    # 关键：dump 当前状态
    page.wait_for_timeout(3000)
    print("\n=== AFTER _submit_register ===")
    print("URL:", page.url)
    cookies = context.cookies()
    print("cookie names:", [c.get("name") for c in cookies])
    print("has sso:", reg._has_auth_cookies(cookies))
    sso = reg._pick_cookie(cookies, "sso")
    print("sso:", (sso[:50] + "...") if sso else None)
    print("checkbox count:", page.locator("input[type=checkbox]").count())
    body = page.locator("body").inner_text()[:300].replace("\n", " | ")
    print("body:", body)

    browser.close()
    playwright.stop()


if __name__ == "__main__":
    main()