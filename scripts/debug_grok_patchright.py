#!/usr/bin/env python3
"""Debug: 项目推荐浏览器 (patchright + msedge/chromium) 测试 CreateEmailValidationCode"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

KEY_RESPONSES = []
BODY = ""

def on_response(response):
    global BODY
    url = response.url
    if "CreateEmailValidationCode" in url:
        KEY_RESPONSES.append((response.status, response.request.method, url[:120]))
        try:
            BODY = response.text()
        except Exception:
            pass

def run_with(browser, label):
    global KEY_RESPONSES, BODY
    KEY_RESPONSES = []
    BODY = ""
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
    page.wait_for_timeout(2500)

    page.locator("input[type=email]").fill("tmptzykzd5745@cy3124414.xyz")
    page.wait_for_timeout(800)
    page.locator("button[type=submit]").click()
    page.wait_for_timeout(7000)

    print(f"\n=== [{label}] RESULTS ===")
    for s, m, u in KEY_RESPONSES:
        print(f"{s} {m} {u}")
    print("code_input:", page.locator("input[name=code]").count())
    print("body:", page.locator("body").inner_text()[:200].replace("\n", " | "))
    import re
    codes = re.findall(r'Error (\d{4})', BODY)
    print("CF ERROR CODES:", codes)
    context.close()

from patchright.sync_api import sync_playwright

playwright = sync_playwright().start()

# 尝试系统 Chrome，失败则回退默认 chromium
try:
    browser = playwright.chromium.launch(headless=False, channel="chrome")
    run_with(browser, "chrome")
    browser.close()
except Exception as e:
    print(f"chrome 启动失败: {e}")

try:
    browser = playwright.chromium.launch(headless=False)
    run_with(browser, "chromium")
    browser.close()
except Exception as e:
    print(f"chromium 启动失败: {e}")

playwright.stop()