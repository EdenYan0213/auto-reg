#!/usr/bin/env python3
"""Debug: dump 完整 403 响应文本 + 响应头"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camoufox.sync_api import Camoufox
from browserforge.fingerprints import Screen

CAPTURED = {}

def on_response(response):
    url = response.url
    if "CreateEmailValidationCode" in url:
        try:
            CAPTURED["body"] = response.text()
        except Exception as e:
            CAPTURED["body"] = f"<err {e}>"
        CAPTURED["headers"] = dict(response.headers)
        CAPTURED["status"] = response.status

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
    page.wait_for_timeout(2500)
    page.locator("input[type=email]").fill("tmptzykzd5745@cy3124414.xyz")
    page.wait_for_timeout(600)
    page.locator("button[type=submit]").click()
    page.wait_for_timeout(6000)

    print("STATUS:", CAPTURED.get("status"))
    print("\n=== RESPONSE HEADERS ===")
    for k, v in CAPTURED.get("headers", {}).items():
        if k.lower() in ("cf-ray", "server", "cf-mitigated", "cf-cache-status", "content-type", "x-powered-by", "via", "alt-svc", "cf-chl", "report-to", "cache-control"):
            print(f"  {k}: {v}")
    print("\n=== FULL BODY (first 2000 chars) ===")
    print(CAPTURED.get("body", "")[:2000])