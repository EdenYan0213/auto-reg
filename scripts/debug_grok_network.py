#!/usr/bin/env python3
"""Debug: 捕获 Grok 提交邮箱时的网络请求，定位 403 来源"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camoufox.sync_api import Camoufox

CAPTURED = []

def on_response(response):
    if "accounts.x.ai" in response.url or "grok.com" in response.url or "x.ai" in response.url:
        if response.status >= 400 or "send" in response.url.lower() or "code" in response.url.lower() or "signup" in response.url.lower():
            CAPTURED.append((response.status, response.request.method, response.url[:200]))

with Camoufox(headless=False) as browser:
    context = browser.new_context()
    page = context.new_page()
    page.set_viewport_size({"width": 1400, "height": 1200})
    page.on("response", on_response)

    page.goto("https://accounts.x.ai/sign-up", wait_until="domcontentloaded")
    page.wait_for_timeout(2500)

    # 点击 Sign up with email
    clicked = page.evaluate(
        """() => {
            const buttons = [...document.querySelectorAll('button')];
            const target =
              buttons.find((b) => /邮箱|email/i.test((b.innerText || '').trim())) ||
              buttons[1] || null;
            if (target) { target.click(); return true; }
            return false;
        }"""
    )
    page.wait_for_timeout(2500)

    # 检查 Turnstile iframe 是否存在
    frames = [f.url for f in page.frames]
    print("FRAMES:", frames)
    try:
        tk = page.evaluate(
            """() => {
                const el = document.querySelector('[data-sitekey]');
                const ifr = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                return {sitekey_el: el ? el.getAttribute('data-sitekey') : null, iframe: ifr ? ifr.src.slice(0,120) : null};
            }"""
        )
        print("TURNSTILE:", tk)
    except Exception as e:
        print("TURNSTILE ERR:", e)

    # 提交邮箱
    page.locator("input[type=email]").fill("tmptzykzd5745@cy3124414.xyz")
    page.wait_for_timeout(500)
    page.locator("button[type=submit]").click()
    page.wait_for_timeout(6000)

    print("\n=== FAILED/KEY RESPONSES ===")
    for status, method, url in CAPTURED:
        print(f"{status} {method} {url}")
    print("BODY:", page.locator("body").inner_text()[:300])