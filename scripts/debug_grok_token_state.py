#!/usr/bin/env python3
"""Debug: 在提交前检查 invisible Turnstile token 是否生成"""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camoufox.sync_api import Camoufox
from browserforge.fingerprints import Screen

RESP_INFO = {}

def on_response(response):
    if "CreateEmailValidationCode" in response.url:
        RESP_INFO["status"] = response.status

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

    print("screen:", page.evaluate("() => `${screen.width}x${screen.height}`"))
    print("inner:", page.evaluate("() => `${innerWidth}x${innerHeight}`"))
    print("dpr:", page.evaluate("() => devicePixelRatio"))
    print("platform:", page.evaluate("() => navigator.platform"))
    print("vendor:", page.evaluate("() => navigator.vendor"))

    # 每 1.5s 轮询 turnstile token 状态，看是否会自动生成
    for i in range(6):
        state = page.evaluate(
            """() => {
                const inputs = [...document.querySelectorAll('input[id^="cf-chl-widget-"], input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]')];
                return inputs.map(i => ({name: i.name, id: i.id, vlen: (i.value || '').length}));
            }"""
        )
        print(f"[{i}] token inputs:", state)
        page.wait_for_timeout(1500)

    page.locator("input[type=email]").fill("tmptzykzd5745@cy3124414.xyz")
    page.wait_for_timeout(500)
    page.locator("button[type=submit]").click()
    page.wait_for_timeout(6000)
    print("RESP STATUS:", RESP_INFO.get("status"))