#!/usr/bin/env python3
"""Debug: 提取 Cloudflare 403 页面的错误码 (1020/1025/1015 等)"""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camoufox.sync_api import Camoufox
from browserforge.fingerprints import Screen

CF_BODY = ""

def on_response(response):
    global CF_BODY
    if "CreateEmailValidationCode" in response.url:
        try:
            CF_BODY = response.text()
        except Exception:
            pass

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

    # 提取错误码
    codes = re.findall(r'Error (\d{4})', CF_BODY)
    print("ERROR CODES:", codes)
    # 提取原因文本
    reasons = re.findall(r'(?:What can I do|error code)[^<]{0,200}', CF_BODY, re.I)
    for r in reasons[:3]:
        print("REASON:", r.strip()[:200])
    # 检查是否有 challenge 标识
    has_chl = 'cf-chl' in CF_BODY or 'challenge-platform' in CF_BODY
    print("HAS CHALLENGE MARKER:", has_chl)
    print("BODY LEN:", len(CF_BODY))