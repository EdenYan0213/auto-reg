#!/usr/bin/env python3
"""Debug: 提交邮箱后完整响应 + 页面状态"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camoufox.sync_api import Camoufox
from browserforge.fingerprints import Screen

RESPONSES = []

def on_response(response):
    url = response.url
    if "accounts.x.ai" in url:
        RESPONSES.append((response.status, response.request.method, url[:160]))

with Camoufox(headless=False, screen=Screen(max_width=1920, max_height=1080), os="windows", locale="en-US") as browser:
    context = browser.new_context()
    page = context.new_page()
    page.on("response", on_response)

    page.goto("https://accounts.x.ai/sign-up", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)

    page.evaluate(
        """() => {
            const buttons = [...document.querySelectorAll('button')];
            const target =
              buttons.find((b) => /邮箱|email/i.test((b.innerText || '').trim())) ||
              buttons[1] || null;
            if (target) { target.click(); return true; }
            return false;
        }"""
    )
    page.wait_for_timeout(3000)

    page.locator("input[type=email]").fill("tmptzykzd5745@cy3124414.xyz")
    page.wait_for_timeout(800)
    page.locator("button[type=submit]").click()

    # 轮询看页面是否进入验证码页
    for i in range(8):
        page.wait_for_timeout(1500)
        has_code = page.locator("input[name=code]").count() > 0
        url = page.url
        body = page.locator("body").inner_text()[:150].replace("\n", " | ")
        print(f"[{i}] url={url[:80]} code_input={has_code} body={body}")
        if has_code:
            break

    print("\n=== ALL accounts.x.ai RESPONSES ===")
    for s, m, u in RESPONSES:
        print(f"{s} {m} {u}")