#!/usr/bin/env python3
"""Debug: 等待 invisible Turnstile 完成后再提交"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camoufox.sync_api import Camoufox
from browserforge.fingerprints import Screen

KEY_RESPONSES = []

def on_response(response):
    url = response.url
    if "CreateEmailValidationCode" in url or ("sign-in" in url and response.status == 403):
        KEY_RESPONSES.append((response.status, response.request.method, url[:120]))

with Camoufox(headless=False, screen=Screen(max_width=1920, max_height=1080), os="windows", locale="en-US", humanize=True) as browser:
    context = browser.new_context()
    page = context.new_page()
    page.on("response", on_response)

    page.goto("https://accounts.x.ai/sign-up", wait_until="domcontentloaded")
    page.wait_for_timeout(4000)

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
    page.wait_for_timeout(1000)

    # 长时间等待 invisible turnstile 完成
    for i in range(5):
        page.wait_for_timeout(3000)
        # 模拟轻微鼠标移动
        try:
            page.mouse.move(400 + i * 30, 300 + i * 15)
        except Exception:
            pass
        print(f"[{i}] waiting turnstile... frames={[f.url[:60] for f in page.frames]}")

    # 提交
    page.locator("input[type=email]").fill("tmptzykzd5745@cy3124414.xyz")
    page.wait_for_timeout(1000)
    page.locator("button[type=submit]").click()
    page.wait_for_timeout(6000)

    print("\n=== KEY RESPONSES ===")
    for s, m, u in KEY_RESPONSES:
        print(f"{s} {m} {u}")
    print("code_input:", page.locator("input[name=code]").count())
    print("body:", page.locator("body").inner_text()[:200].replace("\n", " | "))