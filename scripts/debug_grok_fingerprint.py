#!/usr/bin/env python3
"""Debug: 用正确的 Camoufox API (screen= at launch) 重试 POST，捕获 CF 错误码"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camoufox.sync_api import Camoufox

RESP_INFO = {}

def on_response(response):
    if "CreateEmailValidationCode" in response.url:
        RESP_INFO["status"] = response.status
        RESP_INFO["headers"] = dict(response.headers)
        try:
            RESP_INFO["body"] = response.text()
        except Exception as e:
            RESP_INFO["body"] = f"<err {e}>"

with Camoufox(headless=False, screen=(1400, 1200), os="windows", locale="en-US") as browser:
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
    print("FRAMES:", [f.url[:120] for f in page.frames])

    page.locator("input[type=email]").fill("tmptzykzd5745@cy3124414.xyz")
    page.wait_for_timeout(500)
    page.locator("button[type=submit]").click()
    page.wait_for_timeout(6000)

    print("\n=== RESPONSE ===")
    print("STATUS:", RESP_INFO.get("status"))
    for k, v in sorted((RESP_INFO.get("headers") or {}).items()):
        if k.startswith("cf-") or k in ("server", "content-type"):
            print(f"  {k}: {v}")
    body = RESP_INFO.get("body") or ""
    import re
    m = re.search(r'Error [0-9]{4}|error code[^<]*|Attention Required[^<]*', body, re.I)
    print("ERR MARKER:", m.group(0) if m else "(none)")
    print("BODY LEN:", len(body))