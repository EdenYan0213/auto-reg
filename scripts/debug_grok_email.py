#!/usr/bin/env python3
"""Debug: 查看 Grok 提交邮箱后的页面状态"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camoufox.sync_api import Camoufox
from platforms.grok.core import UA

def dump_state(page, tag):
    print(f"\n===== {tag} =====")
    print("URL:", page.url)
    try:
        inputs = page.evaluate("""() => [...document.querySelectorAll('input')].map(i => ({name: i.name, type: i.type, placeholder: i.placeholder}))""")
        print("INPUTS:", inputs)
    except Exception as e:
        print("INPUTS ERR:", e)
    try:
        print("BODY:", page.locator("body").inner_text()[:400])
    except Exception as e:
        print("BODY ERR:", e)

with Camoufox(headless=False) as browser:
    context = browser.new_context()
    page = context.new_page()
    page.set_viewport_size({"width": 1400, "height": 1200})

    page.goto("https://accounts.x.ai/sign-up", wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    dump_state(page, "初始页面")

    # 检查是否有 email input
    email_input = page.locator("input[type=email]")
    print("\nemail input count:", email_input.count())
    if email_input.count() == 0:
        # 可能需要点按钮
        clicked = page.evaluate(
            """() => {
                const buttons = [...document.querySelectorAll('button')];
                const target =
                  buttons.find((b) => /邮箱|email/i.test((b.innerText || '').trim())) ||
                  buttons[1] ||
                  null;
                if (target) { target.click(); return true; }
                return false;
            }"""
        )
        print("clicked entry:", clicked)
        page.wait_for_timeout(2500)
        dump_state(page, "点击后")

    email_input = page.locator("input[type=email]")
    if email_input.count() > 0:
        email_input.fill("tmptzykzd5745@cy3124414.xyz")
        page.wait_for_timeout(500)
        # 找到提交按钮
        btns = page.evaluate("""() => [...document.querySelectorAll('button')].map(b => ({text: (b.innerText||'').trim(), type: b.type, disabled: b.disabled}))""")
        print("\nBUTTONS:", btns)
        page.locator("button[type=submit]").click()
        print("clicked submit")
        page.wait_for_timeout(4000)
        dump_state(page, "提交后 4s")
        page.wait_for_timeout(6000)
        dump_state(page, "提交后 10s")