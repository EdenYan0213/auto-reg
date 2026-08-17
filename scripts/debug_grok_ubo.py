#!/usr/bin/env python3
"""Debug: 检查 Camoufox 下 Turnstile 脚本是否被 UBO 拦截 / 捕获 requestfailed + console"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camoufox.sync_api import Camoufox

FAILED = []
CONSOLE = []
CF_REQS = []

def on_request(request):
    if "challenges.cloudflare.com" in request.url or "turnstile" in request.url.lower():
        CF_REQS.append(("REQ", request.method, request.url[:150]))

def on_requestfailed(req):
    failure = req.failure
    err = failure if isinstance(failure, str) else (failure or {}).get("errorText", "") if failure else ""
    FAILED.append((req.method, req.url[:150], err))

def on_console(msg):
    CONSOLE.append((msg.type, msg.text[:200]))

with Camoufox(headless=False) as browser:
    context = browser.new_context()
    page = context.new_page()
    page.set_viewport_size({"width": 1400, "height": 1200})
    page.on("request", on_request)
    page.on("requestfailed", on_requestfailed)
    page.on("console", on_console)

    page.goto("https://accounts.x.ai/sign-up", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)

    # 点击 Sign up with email
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

    # 提交邮箱前检查 turnstile
    print("=== CF/TURNSTILE REQUESTS ===")
    for r in CF_REQS:
        print(r)
    print("FRAMES:", [f.url for f in page.frames])

    # 提交邮箱
    page.locator("input[type=email]").fill("tmptzykzd5745@cy3124414.xyz")
    page.wait_for_timeout(500)
    page.locator("button[type=submit]").click()
    page.wait_for_timeout(6000)

    print("\n=== FAILED REQUESTS ===")
    for m, u, e in FAILED:
        print(f"{m} {u} -> {e}")

    print("\n=== CONSOLE (filtered) ===")
    for t, txt in CONSOLE:
        low = txt.lower()
        if any(k in low for k in ["turnstile", "captcha", "challenge", "cloudflare", "error", "403", "blocked", "cf-"]):
            print(f"[{t}] {txt}")

    print("\n=== POST-EMAIL CF REQS ===")
    for r in CF_REQS:
        print(r)