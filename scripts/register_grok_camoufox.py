#!/usr/bin/env python3
"""
使用 Patchright (系统 Chrome) + CF Worker 自建邮箱 (cy3124414.xyz) 注册 Grok 账号。

复用项目自带组件：
  - CFWorkerMailbox      : 与用户提供的 API 完全匹配 (POST /admin/new_address, GET /admin/mails, x-admin-auth)
  - GrokRegister 步骤方法 : _goto_email_signup / _submit_email / _submit_otp / _fill_user_form /
                            _solve_turnstile_on_page / _submit_register / _accept_tos_if_needed
  - YesCaptcha           : 第三方 Turnstile 解算 (LocalSolver 实测被 Cloudflare 拒, 见 main())

浏览器：Patchright + 系统 Chrome (channel=chrome)。
  注意：Camoufox (Firefox) 指纹被 x.ai 风控拦截 (CreateEmailValidationCode 403)，
        真实 Chrome 实测通过 (200)。故改用项目推荐的 patchright。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.base_mailbox import CFWorkerMailbox
from core.base_captcha import YesCaptcha
from platforms.grok.core import GrokRegister, _rand_name, _rand_password, UA, TS_RENDER_HOOK

# 追加到真实 turnstile api.js 末尾的包装器：真实 widget 正常渲染（DOM/iframe/sitekey 齐全），
# 同时把页面传给 turnstile.render 的真实 callback 捕获到 window.__tsCallback，
# 由 _solve_turnstile_by_solver 解出 token 后调用 -> React 状态 R 置位 -> 提交通过后端校验。
# （stub 冒充 api.js 方案实测失效：页面显式渲染模式对 stub 有额外依赖，React 从不调 render，
#   diag_105 实证 tsSitekey='' 且无 widget DOM。改走真实 api.js + render 包装，行为最忠实。）
TS_WRAP_HOOK = """
;(function () {
  try {
    var ts = window.turnstile;
    if (ts && ts.render && !ts.__tsRenderWrapped) {
      ts.__tsRenderWrapped = true;
      var origRender = ts.render.bind(ts);
      ts.render = function (el, opts) {
        if (opts && typeof opts.callback === 'function') {
          window.__tsCallback = opts.callback;
          window.__tsAction = opts.action || '';
          window.__tsSitekey = opts.sitekey || '';
        }
        return origRender(el, opts);
      };
    }
  } catch (e) {}
})();
"""

# 前置拦截器（必须 PREPEND 到 api.js 开头）：页面以 api.js?onload=<fn> 显式渲染模式加载，
# 真实 api.js 在自身 body 末尾同步调用 window[onloadName]()，页面 onload 里立即调
# turnstile.render。若包装器追加在 api.js 末尾（TS_WRAP_HOOK），执行顺序晚于 onload 调用，
# callback 永远抓不到（E2E 实测 Run5）。此处替换 window[onloadName]：先包 ts.render 捕获
# callback/sitekey/action，再调用原始 onload -> 真实 widget 正常渲染且 callback 被捕获。
TS_PREPEND_HOOK = """
;(function () {
  try {
    var src = (document.currentScript && document.currentScript.src) || '';
    var m = src.match(/[?&]onload=([^&]+)/);
    if (!m) return;
    var name = m[1];
    if (typeof window[name] !== 'function') return;
    if (window.__tsOnloadFiredSet && window.__tsOnloadFiredSet[name]) return;
    var origOnload = window[name];
    window[name] = function () {
      try {
        if (!window.__tsOnloadFiredSet) window.__tsOnloadFiredSet = {};
        window.__tsOnloadFiredSet[name] = true;
        var ts = window.turnstile;
        if (ts && ts.render && !ts.__tsRenderWrapped) {
          ts.__tsRenderWrapped = true;
          var origRender = ts.render.bind(ts);
          ts.render = function (el, opts) {
            if (opts) {
              if (typeof opts.callback === 'function') {
                window.__tsCallback = opts.callback;
                window.__tsAction = opts.action || '';
                window.__tsSitekey = opts.sitekey || '';
              }
            }
            return origRender(el, opts);
          };
        }
      } catch (e) {}
      return origOnload.apply(this, arguments);
    };
  } catch (e) {}
})();
"""

# ============ 配置 ============
CF_API_URL = os.getenv("CFWORKER_API_URL", "https://cy3124414.xyz")
CF_ADMIN_TOKEN = os.getenv("CFWORKER_ADMIN_TOKEN", "cfmailadmin2026")
CF_DOMAIN = os.getenv("CFWORKER_DOMAIN", "cy3124414.xyz")
YESCAPTCHA_CLIENT_KEY = os.getenv("YESCAPTCHA_CLIENT_KEY", "19792f1fe9d5d91b20f9ae40147df6837fe1ce76136299")
HEADLESS = os.getenv("HEADLESS", "0") == "1"
OTP_TIMEOUT = int(os.getenv("OTP_TIMEOUT", "120"))  # 最多等 120s，轮询间隔 3s
OUT_FILE = os.getenv("GROK_ACCOUNT_FILE", "data/grok_account.txt")


def log(msg):
    print(msg, flush=True)


def main():
    log("== Grok 注册 (Camoufox + CF Worker 邮箱) ==")

    # 1. 邮箱
    mailbox = CFWorkerMailbox(
        api_url=CF_API_URL,
        admin_token=CF_ADMIN_TOKEN,
        domain=CF_DOMAIN,
    )
    acct = mailbox.get_email()
    email = acct.email
    log(f"生成的邮箱: {email}")
    before_ids = mailbox.get_current_ids(acct)

    # 2. 注册器（YesCaptcha 第三方解 Turnstile：本地 LocalSolver 实测被 Cloudflare 拒，
    #    camoufox/chrome 两种浏览器均 ERROR_CAPTCHA_UNSOLVABLE，diag_101 后确认）
    captcha_solver = YesCaptcha(YESCAPTCHA_CLIENT_KEY)
    reg = GrokRegister(captcha_solver=captcha_solver, log_fn=log)

    password = _rand_password()
    given_name = _rand_name()
    family_name = _rand_name()

    # 3. Patchright + 系统 Chrome（patchright 协议层 stealth 是过 x.ai 风控关键：playwright 403 / patchright 200）
    from patchright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.launch(headless=HEADLESS, channel="chrome")
    except Exception:
        browser = playwright.chromium.launch(headless=HEADLESS)
    context = browser.new_context(
        viewport={"width": 1400, "height": 1200},
        user_agent=UA,
    )
    # 拦截真实 turnstile api.js：PREPEND 包装器（替换 onload 函数，先包 ts.render 再调原 onload）
    # + 真实 api.js body + APPEND 兜底包装器。真实 widget 正常渲染，callback 被捕获到 __tsCallback。
    def _fulfill_turnstile(route):
        _req_url = route.request.url
        try:
            resp = route.fetch()
            body = resp.body().decode("utf-8", errors="replace")
            _fetch_ok = True
        except Exception as _e:
            body = TS_RENDER_HOOK
            _fetch_ok = False
            log(f"[FULFILL] fetch失败 {_req_url}: {_e}")
        try:
            route.fulfill(
                status=200,
                body=TS_PREPEND_HOOK + body + TS_WRAP_HOOK,
                content_type="application/javascript",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
            log(f"[FULFILL] 已拦截 {_req_url} fetch_ok={_fetch_ok} body_len={len(body)}")
        except Exception as _fe:
            log(f"[FULFILL] fulfill异常 {_req_url}: {_fe}")

    context.route("**/turnstile/v0/api.js*", _fulfill_turnstile)
    page = context.new_page()
    page.set_viewport_size({"width": 1400, "height": 1200})

    reg._goto_email_signup(page)
    reg._ensure_turnstile_stub(page)
    reg._submit_email(page, email)

    log("等待验证码...")
    code = mailbox.wait_for_code(
        acct,
        keyword="",
        timeout=OTP_TIMEOUT,
        before_ids=before_ids,
        code_pattern=r"[A-Z0-9]{3}-[A-Z0-9]{3}",
    )
    if code:
        code = code.replace("-", "").replace(" ", "")
        log(f"验证码: {code}")
    if not code:
        raise RuntimeError("未获取到验证码")

    reg._submit_otp(page, code)
    reg._fill_user_form(page, given_name, family_name, password)
    reg._ensure_turnstile_stub(page)
    reg._solve_turnstile_on_page(page)
    reg._submit_register(page)
    reg._accept_tos_if_needed(page)

    # sso cookie 仅付费账户/特定流程生成，free 账户不生成（实测），故可选提取
    cookies = context.cookies()
    if not reg._has_auth_cookies(cookies):
        page.wait_for_timeout(5000)
        cookies = context.cookies()
    sso = reg._pick_cookie(cookies, "sso")
    sso_rw = reg._pick_cookie(cookies, "sso-rw")

    _names = sorted({c.get("name", "") for c in cookies})
    if not sso:
        log(f"  [warn] 未提取到 sso cookie（free 账户可能不生成）；当前 cookie 名: {_names}")
    else:
        log(f"  sso={sso[:40]}...")

    log("Grok 注册链路完成")

    result = {
        "platform": "grok",
        "email": email,
        "password": password,
        "given_name": given_name,
        "family_name": family_name,
        "sso": sso,
        "sso_rw": sso_rw,
    }
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        for k, v in result.items():
            f.write(f"{k}: {v}\n")
    log(f"已保存账号信息到 {OUT_FILE}")
    browser.close()
    playwright.stop()
    return result


if __name__ == "__main__":
    main()