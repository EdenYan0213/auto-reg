#!/usr/bin/env python3
"""Debug: 捕获 CreateEmailValidationCode 的请求体和响应体，定位 403 原因"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camoufox.sync_api import Camoufox

TARGET_URL = None
REQ_BODY = None
RESP_BODY = None
RESP_STATUS = None

def on_request(request):
    global TARGET_URL, REQ_BODY
    if "CreateEmailValidationCode" in request.url:
        TARGET_URL = request.url
        try:
            REQ_BODY = request.post_data
        except Exception:
            REQ_BODY = "<no post_data>"

def on_response(response):
    global RESP_BODY, RESP_STATUS
    if "CreateEmailValidationCode" in response.url:
        RESP_STATUS = response.status
        try:
            RESP_BODY = response.text()[:800]
        except Exception as e:
            RESP_BODY = f"<err {e}>"

with Camoufox(headless=False) as browser:
    context = browser.new_context()
    page = context.new_page()
    page.set_viewport_size({"width": 1400, "height": 1200})
    page.on("request", on_request)
    page.on("response", on_response)

    page.goto("https://accounts.x.ai/sign-up", wait_until="domcontentloaded")
    page.wait_for_timeout(2500)

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

    page.locator("input[type=email]").fill("tmptzykzd5745@cy3124414.xyz")
    page.wait_for_timeout(500)
    page.locator("button[type=submit]").click()
    page.wait_for_timeout(6000)

    print("=== REQUEST ===")
    print("URL:", TARGET_URL)
    print("BODY:", REQ_BODY)
    print("\n=== RESPONSE ===")
    print("STATUS:", RESP_STATUS)
    print("BODY:", RESP_BODY)