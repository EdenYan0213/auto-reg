#!/usr/bin/env python3
"""Debug: 检查 email 步骤 turnstile token 是否生成 + 捕获 CreateEmailValidationCode 完整请求头"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camoufox.sync_api import Camoufox

REQ_INFO = {}
RESP_INFO = {}

def on_request(request):
    if "CreateEmailValidationCode" in request.url:
        REQ_INFO["headers"] = dict(request.headers)
        REQ_INFO["post_data_buffer"] = request.post_data_buffer

def on_response(response):
    if "CreateEmailValidationCode" in response.url:
        RESP_INFO["status"] = response.status
        RESP_INFO["headers"] = dict(response.headers)
        try:
            RESP_INFO["body"] = response.text()[:400]
        except Exception as e:
            RESP_INFO["body"] = f"<err {e}>"

with Camoufox(headless=False) as browser:
    context = browser.new_context()
    page = context.new_page()
    page.set_viewport_size({"width": 1400, "height": 1200})
    page.on("request", on_request)
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

    # 检查 turnstile token 输入框
    token_state = page.evaluate(
        """() => {
            const inputs = [...document.querySelectorAll('input[id^="cf-chl-widget-"], input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]')];
            return inputs.map(i => ({name: i.name, id: i.id, value_len: (i.value || '').length}));
        }"""
    )
    print("=== TURNSTILE INPUTS BEFORE SUBMIT ===")
    print(token_state)
    print("FRAMES:", [f.url[:120] for f in page.frames])

    # 提交邮箱
    page.locator("input[type=email]").fill("tmptzykzd5745@cy3124414.xyz")
    page.wait_for_timeout(500)
    page.locator("button[type=submit]").click()
    page.wait_for_timeout(6000)

    print("\n=== REQUEST HEADERS ===")
    for k, v in sorted((REQ_INFO.get("headers") or {}).items()):
        print(f"  {k}: {v}")
    print("\nPOST BODY:", (REQ_INFO.get("post_data_buffer") or b"")[:200])

    print("\n=== RESPONSE ===")
    print("STATUS:", RESP_INFO.get("status"))
    for k, v in sorted((RESP_INFO.get("headers") or {}).items()):
        print(f"  {k}: {v}")
    print("BODY:", RESP_INFO.get("body"))