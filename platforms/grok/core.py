"""
Grok (x.ai) 自动注册

当前链路改为浏览器辅助注册：
1. 邮箱收码
2. 浏览器推进到完成注册页
3. 点击真实 Turnstile 复选框拿 token
4. 完成注册并接受 ToS
5. 提取 sso / sso-rw cookie
"""
import ctypes
import random
import string
import time
from typing import Callable, Optional, Tuple


UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

TS_RENDER_HOOK = """(() => {
  const probe = (ev, extra) => {
    try {
      if (!window.__tsProbe) return;
      window.__tsProbe(ev, extra || {});
    } catch (_) {}
  };
  probe('stub-hook-start', {
    tsType: typeof window.turnstile,
    tsStubInstalled: !!window.__tsStubInstalled,
    tsWrapped: !!(window.turnstile && window.turnstile.__tsRenderWrapped),
    hasProbe: !!window.__tsProbe,
  });
  // 1) 注入 turnstile stub：让 React provider 立即拿到 window.turnstile 并调 stub.render，
  //    从而捕获真实 callback/sitekey/action（E2E 实测：无 stub 时 provider 拿不到 -> widget 不渲染 -> R 为空）。
  //    非破坏性：仅当尚未捕获任何状态时初始化默认值，避免覆盖真实 api.js 已捕获的 __tsCallback/__tsSitekey。
  const __tsReal = window.turnstile;
  const __tsRealWrapped = !!(__tsReal && (__tsReal.__tsRenderWrapped || __tsReal.__tsHooked));
  if (__tsRealWrapped) {
    probe('stub-skip-real-wrapped', { tsType: typeof __tsReal, hasGetResponse: typeof __tsReal.getResponse === 'function' });
  } else if (!window.__tsStubInstalled) {
    window.__tsStubInstalled = true;
    if (window.__tsCallback === undefined) window.__tsCallback = null;
    if (window.__tsAction === undefined) window.__tsAction = '';
    if (window.__tsSitekey === undefined) window.__tsSitekey = '';
    const stub = {
      render: (el, opts) => {
        probe('stub-render-called', {
          cbType: opts ? typeof opts.callback : 'n/a',
          hasSitekey: !!(opts && opts.sitekey),
          sitekey: (opts && opts.sitekey) || '',
          action: (opts && opts.action) || '',
          optKeys: opts ? Object.keys(opts).join(',') : '',
        });
        if (opts) {
          if (typeof opts.callback === 'function') window.__tsCallback = opts.callback;
          window.__tsAction = opts.action || '';
          window.__tsSitekey = opts.sitekey || '';
        }
        return 'stub-widget';
      },
      remove: () => {},
      reset: () => {},
      execute: () => {},
      getResponse: () => '',
      isExpired: () => false,
      ready: (cb) => { if (typeof cb === 'function') { try { cb(); } catch (_) {} } },
    };
    window.turnstile = stub;
    probe('stub-installed', { replacedType: 'window.turnstile' });
  } else {
    probe('stub-skip-already-installed');
  }
  // 2) 显式渲染模式：x.ai 以 api.js?onload=<fn> 加载，页面只有在 api.js 就绪回调被调用后
  //    才会调 turnstile.render()。真实 api.js 会调用 window[onloadName]()；stub 必须补上这一环，
  //    否则 React 永不 render -> __tsSitekey/__tsCallback 始终为空（diag_104 实证）。
  const fireOnloads = () => {
    if (!window.__tsOnloadFiredSet) window.__tsOnloadFiredSet = {};
    for (const s of document.querySelectorAll('script[src*="turnstile"]')) {
      const m = (s.src || '').match(/[?&]onload=([^&]+)/);
      if (!m) continue;
      const name = m[1];
      if (window.__tsOnloadFiredSet[name]) continue;
      if (typeof window[name] === 'function') {
        window.__tsOnloadFiredSet[name] = true;
        probe('stub-fire-onload', { name: name });
        try { window[name](); } catch (_) {}
      } else {
        probe('stub-fire-onload-skip', { name: name, nameType: typeof window[name] });
      }
    }
  };
  fireOnloads();
  let onloadRetries = 0;
  const onloadRetry = setInterval(() => {
    fireOnloads();
    if (++onloadRetries >= 10) clearInterval(onloadRetry);
  }, 200);
  // 3) 兜底：若真实 api.js 稍后加载并覆盖 window.turnstile，仍包装其 render 捕获 callback
  const poll = setInterval(() => {
    const ts = window.turnstile;
    if (!ts || !ts.render || ts.__hooked) return;
    ts.__hooked = true;
    probe('poll-hook-wrap', { tsType: typeof ts, tsRenderStr: String(ts.render).slice(0, 50) });
    const origRender = ts.render.bind(ts);
    ts.render = (el, opts) => {
      probe('poll-render-called', {
        cbType: opts ? typeof opts.callback : 'n/a',
        hasSitekey: !!(opts && opts.sitekey),
        sitekey: (opts && opts.sitekey) || '',
        action: (opts && opts.action) || '',
        optKeys: opts ? Object.keys(opts).join(',') : '',
      });
      if (opts) {
        if (typeof opts.callback === 'function') window.__tsCallback = opts.callback;
        if (opts.action) window.__tsAction = opts.action;
        if (opts.sitekey) window.__tsSitekey = opts.sitekey;
      }
      return origRender(el, opts);
    };
    clearInterval(poll);
  }, 100);
})();
"""


def _rand_name(n: int = 6) -> str:
    return "".join(random.choices(string.ascii_lowercase, k=n)).capitalize()


def _rand_password(n: int = 12) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=n)) + ",,,aA1"


class GrokRegister:
    def __init__(self, captcha_solver=None, yescaptcha_key: str = "", proxy=None, log_fn=print):
        self.captcha_solver = captcha_solver
        self.key = yescaptcha_key
        self.proxy = proxy
        self.log = log_fn

    def _wait_until(self, fn: Callable[[], bool], timeout: float = 30.0, interval: float = 0.5, desc: str = ""):
        start = time.time()
        while time.time() - start < timeout:
            if fn():
                return
            time.sleep(interval)
        raise TimeoutError(desc or "等待超时")

    @staticmethod
    def _has_auth_cookies(cookies: list) -> bool:
        return any(cookie.get("name") in {"sso", "sso-rw"} for cookie in cookies)

    def _launch_browser(self):
        # 必须用 patchright：其协议层 stealth 是过 x.ai 风控的关键
        # （plain playwright + 系统 Chrome 提交邮箱 403，patchright 同配置 200，diag_100/101）。
        from patchright.sync_api import sync_playwright

        playwright = sync_playwright().start()
        launch_kwargs = {
            "headless": False,
        }
        if self.proxy:
            launch_kwargs["proxy"] = {"server": self.proxy}
        for channel in ("chrome", "msedge", None):
            if channel:
                launch_kwargs["channel"] = channel
            else:
                launch_kwargs.pop("channel", None)
            try:
                browser = playwright.chromium.launch(**launch_kwargs)
                return playwright, browser
            except Exception:
                continue
        raise RuntimeError("所有浏览器启动方式均失败: chrome/msedge/chromium 均不可用")

    def _goto_email_signup(self, page) -> None:
        self.log("Step1: 打开 Grok 注册页...")
        page.goto("https://accounts.x.ai/sign-up", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # 页面可能尚未渲染完按钮/输入框，重试查找邮箱入口（最多 ~30s）
        for attempt in range(6):
            if page.locator("input[type=email]").count() > 0:
                break
            clicked = page.evaluate(
                """() => {
                    const buttons = [...document.querySelectorAll('button')];
                    const target =
                      buttons.find((b) => /邮箱|email|Email/i.test((b.innerText || '').trim())) ||
                      buttons[1] ||
                      null;
                    if (target) {
                      target.click();
                      return true;
                    }
                    return false;
                }"""
            )
            if clicked and page.locator("input[type=email]").count() > 0:
                break
            page.wait_for_timeout(4000)
        else:
            page.locator("input[type=email]").wait_for(state="visible", timeout=10000)
        page.locator("input[type=email]").wait_for(state="visible", timeout=10000)

    def _submit_email(self, page, email: str) -> None:
        self.log(f"Step2: 提交邮箱 {email} ...")
        page.locator("input[type=email]").fill(email)
        page.locator("button[type=submit]").click()

        def _email_verify_ready() -> bool:
            return page.locator("input[name=code]").count() > 0

        try:
            self._wait_until(_email_verify_ready, timeout=15, desc="等待邮箱验证码页超时")
        except Exception:
            body = page.locator("body").inner_text()
            if any(x in body for x in ["域名", "已被拒绝", "其他邮箱地址", "disposable", "rejected"]):
                raise RuntimeError(f"邮箱域名被拒绝: {body[:200]}")
            raise RuntimeError(f"邮箱提交失败: {body[:200]}")

    def _submit_otp(self, page, code: str) -> None:
        self.log(f"Step3: 提交邮箱验证码 {code} ...")
        otp_input = page.locator("input[name=code]")
        otp_input.click()
        try:
            otp_input.press("Control+A")
        except Exception:
            pass
        otp_input.type(code, delay=120)
        page.wait_for_timeout(1500)
        submit_disabled = page.evaluate(
            "() => !!document.querySelector('button[type=submit]')?.disabled"
        )
        if not submit_disabled:
            page.locator("button[type=submit]").click()
        else:
            otp_input.press("Enter")

        def _user_form_ready() -> bool:
            return page.locator("input[name=givenName]").count() > 0

        self._wait_until(_user_form_ready, timeout=20, desc="等待完成注册页超时")
        self.log("  已进入完成注册页")

    def _fill_user_form(self, page, given_name: str, family_name: str, password: str) -> None:
        self.log(f"Step4: 填写用户信息 {given_name} {family_name} ...")
        page.locator("input[name=givenName]").fill(given_name)
        page.locator("input[name=familyName]").fill(family_name)
        page.locator("input[name=password]").fill(password)

    @staticmethod
    def _find_turnstile_widget(page) -> Tuple[object, Optional[dict]]:
        for frame in page.frames:
            if "challenges.cloudflare.com" not in frame.url:
                continue
            try:
                frame_el = frame.frame_element()
                box = frame_el.bounding_box()
            except Exception:
                box = None
            if box and box["width"] > 100 and box["height"] >= 50:
                return frame, box
        return None, None

    @staticmethod
    def _read_turnstile_token(page) -> str:
        return page.evaluate(
            """() => {
                return (
                    document.querySelector('input[id^="cf-chl-widget-"]')?.value ||
                    document.querySelector('input[name="cf-turnstile-response"]')?.value ||
                    ''
                );
            }"""
        )

    @staticmethod
    def _read_turnstile_sitekey(page) -> str:
        import re as _re
        # 1) stub render 已捕获 opts.sitekey —— 最可靠
        from_stub = page.evaluate("() => window.__tsSitekey || ''")
        if from_stub:
            return from_stub
        from_dom = page.evaluate(
            """() => {
                const byData = document.querySelector('[data-sitekey]')?.getAttribute('data-sitekey');
                if (byData) return byData;
                for (const iframe of document.querySelectorAll('iframe')) {
                    const src = iframe.src || '';
                    if (!src.includes('challenges.cloudflare.com')) continue;
                    try {
                        const u = new URL(src, location.href);
                        const k = u.searchParams.get('k');
                        if (k) return k;
                    } catch (_) {}
                }
                return '';
            }"""
        )
        if from_dom:
            return from_dom

        for frame in page.frames:
            if "challenges.cloudflare.com" not in frame.url:
                continue
            m = _re.search(r"(0x[0-9A-Za-z]{15,})", frame.url)
            if m:
                return m.group(1)
        return ""

    @staticmethod
    def _has_turnstile_error(page) -> bool:
        keywords = ["验证失败", "故障排除", "verification failed", "troubleshoot", "try again"]
        texts = []
        try:
            texts.append(page.locator("body").inner_text(timeout=800))
        except Exception:
            pass

        for frame in page.frames:
            if "challenges.cloudflare.com" not in frame.url:
                continue
            try:
                texts.append(frame.locator("body").inner_text(timeout=500))
            except Exception:
                continue

        merged = "\n".join(texts).lower()
        return any(k.lower() in merged for k in keywords)

    @staticmethod
    def _inject_turnstile_token(page, token: str) -> bool:
        return bool(
            page.evaluate(
                """(token) => {
                    const selectors = [
                        'input[id^="cf-chl-widget-"]',
                        'input[name="cf-turnstile-response"]',
                        'textarea[name="cf-turnstile-response"]',
                        'textarea[name="g-recaptcha-response"]',
                    ];
                    const inputs = [];
                    for (const sel of selectors) {
                        document.querySelectorAll(sel).forEach((el) => inputs.push(el));
                    }
                    if (!inputs.length) {
                        const fallback = document.createElement('input');
                        fallback.type = 'hidden';
                        fallback.name = 'cf-turnstile-response';
                        document.body.appendChild(fallback);
                        inputs.push(fallback);
                    }
                    for (const el of inputs) {
                        el.value = token;
                        el.setAttribute('value', token);
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    return inputs.length > 0;
                }""",
                token,
            )
        )

    def _wait_turnstile_token(self, page, wait_rounds: int = 25, wait_ms: int = 500) -> str:
        for _ in range(wait_rounds):
            token = self._read_turnstile_token(page)
            if token and len(token) > 20:
                return token
            page.wait_for_timeout(wait_ms)
        return ""

    def _native_click_turnstile(self, page, box, offset_x: float) -> str:
        import platform
        system = platform.system()
        if system == "Darwin":
            try:
                from Quartz import (
                    CGEventCreateMouseEvent, CGEventPost, CGEventSetIntegerValueField,
                    kCGEventLeftMouseDown, kCGEventLeftMouseUp, kCGEventMouseMoved,
                    kCGHIDEventTap, kCGMouseButtonLeft, kCGMouseEventClickState,
                )
            except ImportError as e:
                raise RuntimeError(f"macOS 原生点击需要 pyobjc-framework-Quartz: {e}") from e
            self.log("  [macOS] 使用 Quartz CGEvent 原生点击（需在系统设置>隐私与安全>辅助功能 中授权终端/IDE）")
            user32 = None
        elif system == "Windows":
            try:
                user32 = ctypes.windll.user32
                try:
                    user32.SetProcessDPIAware()
                except Exception:
                    pass
            except Exception as e:
                raise RuntimeError(f"当前系统不支持原生点击: {e}") from e
        else:
            raise RuntimeError(f"当前系统不支持原生点击: {system}")

        page.bring_to_front()
        metrics = page.evaluate(
            """() => ({
                screenX,
                screenY,
                outerWidth,
                outerHeight,
                innerWidth,
                innerHeight,
                dpr: window.devicePixelRatio,
            })"""
        )

        border_x = max(0, (metrics["outerWidth"] - metrics["innerWidth"]) / 2)
        chrome_y = max(0, metrics["outerHeight"] - metrics["innerHeight"] - border_x)
        raw_x = metrics["screenX"] + border_x + box["x"] + offset_x
        raw_y = metrics["screenY"] + chrome_y + box["y"] + box["height"] / 2
        dpr = float(metrics.get("dpr") or 1.0)
        points = [(raw_x, raw_y)]
        if abs(dpr - 1.0) > 0.05:
            points.append((raw_x * dpr, raw_y * dpr))

        for idx, (screen_x, screen_y) in enumerate(points, start=1):
            self.log(f"  Native click #{idx}: ({screen_x:.1f}, {screen_y:.1f})")
            if user32 is not None:
                # Windows: user32 API
                user32.SetCursorPos(int(screen_x), int(screen_y))
                time.sleep(0.15)
                user32.mouse_event(0x0002, 0, 0, 0, 0)
                time.sleep(0.12)
                user32.mouse_event(0x0004, 0, 0, 0, 0)
            else:
                # macOS: Quartz CGEvent（真实系统级点击，可绕过 Playwright 合成事件检测）
                pos = (float(screen_x), float(screen_y))
                move = CGEventCreateMouseEvent(None, kCGEventMouseMoved, pos, kCGMouseButtonLeft)
                CGEventPost(kCGHIDEventTap, move)
                time.sleep(0.15)
                down = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, pos, kCGMouseButtonLeft)
                CGEventSetIntegerValueField(down, kCGMouseEventClickState, 1)
                CGEventPost(kCGHIDEventTap, down)
                time.sleep(0.12)
                up = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, pos, kCGMouseButtonLeft)
                CGEventSetIntegerValueField(up, kCGMouseEventClickState, 1)
                CGEventPost(kCGHIDEventTap, up)

            token = self._wait_turnstile_token(page, wait_rounds=18, wait_ms=450)
            if token:
                return token

        raise RuntimeError("Native click 后仍未获取到 token")

    def _solve_turnstile_by_solver(self, page, sitekey: str = "") -> str:
        if not self.captcha_solver:
            return ""
        solver_name = type(self.captcha_solver).__name__.lower()
        if "manual" in solver_name:
            return ""
        from core.base_captcha import LocalSolverCaptcha

        client_key = getattr(self.captcha_solver, "client_key", None)
        if client_key is not None and not str(client_key).strip():
            self.log("  YesCaptcha 未配置 key，自动降级到本地 LocalSolver")
            self.captcha_solver = LocalSolverCaptcha()
        if not sitekey:
            sitekey = self._read_turnstile_sitekey(page)
        if not sitekey:
            self.log("  未提取到 Turnstile sitekey，跳过验证码服务兜底")
            return ""
        action = ""
        try:
            action = page.evaluate("() => window.__tsAction || ''") or ""
        except Exception:
            action = ""
        if isinstance(self.captcha_solver, LocalSolverCaptcha):
            self.log(
                f"  兜底: 调用本地 LocalSolver 解 Turnstile "
                f"(sitekey={sitekey[:8]}..., action={action or '无'})"
            )
            token = self.captcha_solver.solve_turnstile(page.url, sitekey, action=action)
        else:
            self.log(
                f"  兜底: 调用验证码服务解 Turnstile "
                f"(sitekey={sitekey[:8]}..., action={action or '无'})"
            )
            token = self.captcha_solver.solve_turnstile(page.url, sitekey, action=action)
        if not token:
            return ""

        # 主路径：直接从 widget DOM 读取 data-callback 全局函数并调用（不依赖 api.js 拦截/包裹）。
        # 表单页 E2E 实测：真实 turnstile widget iframe 存在(frame[3])，但 __tsCallback 可能被 stub 安装
        # 清空或 api.js 未过拦截(缓存命中) -> 直接调 window[data-callback](token) 置位前端 R 状态。
        cb_fired = page.evaluate(
            """(token) => {
                const el = document.querySelector('.cf-turnstile, [data-sitekey]');
                if (!el) return false;
                const cbName = el.getAttribute('data-callback');
                if (cbName && typeof window[cbName] === 'function') {
                    window[cbName](token);
                    return true;
                }
                return false;
            }""",
            token,
        )
        if cb_fired:
            self.log("  已直接调用 widget data-callback 全局函数，前端验证状态已更新")
            page.wait_for_timeout(700)
            if self._inject_turnstile_token(page, token):
                page.wait_for_timeout(400)
                return self._read_turnstile_token(page) or token
            return token

        # 回退路径：等待 stub/包裹捕获的 __tsCallback
        for _ in range(10):
            if page.evaluate("() => typeof window.__tsCallback === 'function'"):
                break
            page.wait_for_timeout(300)
        callback_fired = page.evaluate(
            """(token) => {
                if (typeof window.__tsCallback === 'function') {
                    window.__tsCallback(token);
                    return true;
                }
                return false;
            }""",
            token,
        )
        if callback_fired:
            self.log("  已调用页面 turnstile.render callback，前端验证状态已更新")
            page.wait_for_timeout(700)
        if self._inject_turnstile_token(page, token):
            page.wait_for_timeout(400)
            return self._read_turnstile_token(page) or token
        return token

    def _solve_turnstile_on_page(self, page) -> str:
        self.log("Step5: 获取 Turnstile token...")
        sitekey = self._read_turnstile_sitekey(page)
        if sitekey:
            self.log(f"  已提取 Turnstile sitekey: {sitekey[:12]}...")

        # 主路径：本地/第三方 solver 直接解 token 并注入 stub 捕获的 callback（前端 R 置位）
        if self.captcha_solver:
            try:
                token = self._solve_turnstile_by_solver(page, sitekey=sitekey)
                if token:
                    self.log(f"  Turnstile token(兜底): {token[:40]}...")
                    return token
            except Exception as e:
                self.log(f"  solver 失败，转入点击兜底: {e}")

        # 兜底路径：点击页面内真实 Turnstile 复选框（Cloudflare managed widget，成功率低）
        self.log("Step5b: 点击页面内 Turnstile 复选框(兜底)...")
        last_error = None
        for attempt in range(8):
            frame, box = self._find_turnstile_widget(page)
            if not box:
                page.wait_for_timeout(1000)
                if last_error is None:
                    last_error = "未找到可点击的 Turnstile iframe"
                continue

            click_x = box["x"] + min(28, max(18, box["width"] * 0.08))
            click_y = box["y"] + box["height"] / 2
            self.log(f"  Turnstile click #{attempt + 1}: ({click_x:.1f}, {click_y:.1f})")
            try:
                if frame:
                    frame.locator("body").click(
                        position={"x": min(28, max(18, box["width"] * 0.08)), "y": box["height"] / 2},
                        timeout=2500,
                    )
                    page.wait_for_timeout(120)
                page.mouse.move(click_x, click_y)
                page.mouse.down()
                page.wait_for_timeout(120)
                page.mouse.up()
                token = self._wait_turnstile_token(page, wait_rounds=28, wait_ms=450)
                if token:
                    self.log(f"  Turnstile token: {token[:40]}...")
                    return token
            except Exception as e:
                last_error = str(e)

            try:
                token = self._native_click_turnstile(page, box, min(28, max(18, box["width"] * 0.08)))
                if token:
                    self.log(f"  Turnstile token: {token[:40]}...")
                    return token
            except Exception as e:
                last_error = str(e)

            if self._has_turnstile_error(page):
                self.log("  检测到 Turnstile 验证失败提示，准备重试...")
            page.wait_for_timeout(900 + attempt * 120)

        raise RuntimeError(last_error or "Turnstile 求解失败")

    def _submit_register(self, page) -> None:
        self.log("Step6: 提交完成注册...")

        def _tos_or_account_ready() -> bool:
            url = page.url
            body = page.locator("body").inner_text()
            return (
                "/accept-tos" in url
                or "/account" in url
                # OneTrust 弹窗也有 >=2 个复选框，不能作为成功信号；仅靠真实跳转/cookie/文案
                or ("接受服务条款" in body and "您的账户" in body)
                or self._has_auth_cookies(page.context.cookies())
            )

        last_error = "等待注册后跳转超时"
        for submit_attempt in range(1, 4):
            page.locator("button[type=submit]").click()
            page.wait_for_timeout(900)
            start = time.time()
            while time.time() - start < 18:
                if _tos_or_account_ready():
                    page.wait_for_timeout(1200)
                    return
                if self._has_turnstile_error(page):
                    last_error = "Cloudflare 验证失败"
                    break
                page.wait_for_timeout(500)
            else:
                last_error = "等待注册后跳转超时"

            if submit_attempt < 3:
                self.log(f"  提交失败({last_error})，重新过 Turnstile 后重试...")
                self._solve_turnstile_on_page(page)

        raise RuntimeError(last_error)

    def _accept_tos_if_needed(self, page) -> None:
        def _tos_or_account_or_cookie() -> bool:
            url = page.url
            body = page.locator("body").inner_text()
            return (
                "/accept-tos" in url
                or "/account" in url
                or "接受服务条款" in body
                or "您的账户" in body
                or self._has_auth_cookies(page.context.cookies())
            )

        try:
            self._wait_until(_tos_or_account_or_cookie, timeout=12, interval=0.5)
        except Exception:
            pass

        # 注册已成功（已带 sso cookie 或已在账户页），无需再走 ToS
        if self._has_auth_cookies(page.context.cookies()) or "/account" in page.url:
            return

        # 仅当确实在 /accept-tos 页（有 ToS 复选框）时才继续，避免 OneTrust 弹窗误判
        if "/accept-tos" not in page.url:
            page.wait_for_timeout(2500)
            if "/accept-tos" not in page.url:
                return

        self.log("Step7: 接受 ToS ...")
        checkbox_labels = [
            "我确认已阅读并接受 企业服务条款，并知晓 隐私政策。",
            "我确认我已年满 18 岁。",
        ]
        for label in checkbox_labels:
            try:
                box = page.get_by_role("checkbox", name=label)
                if not box.is_checked():
                    box.check()
            except Exception:
                pass

        page.get_by_role("button", name="继续").click()

        def _account_ready() -> bool:
            url = page.url
            body = page.locator("body").inner_text()
            return "/account" in url or "您的账户" in body or self._has_auth_cookies(page.context.cookies())

        self._wait_until(_account_ready, timeout=20, desc="等待账户页超时")
        page.wait_for_timeout(1500)

    @staticmethod
    def _pick_cookie(cookies: list, name: str) -> str:
        domains = [".x.ai", "accounts.x.ai", ".grok.com", ".grokusercontent.com", ".grokipedia.com"]
        for domain in domains:
            for cookie in cookies:
                if cookie.get("name") == name and cookie.get("domain") == domain:
                    return cookie.get("value", "")
        for cookie in cookies:
            if cookie.get("name") == name:
                return cookie.get("value", "")
        return ""

    def _ensure_turnstile_stub(self, page) -> None:
        # patchright 1.61.2 的 add_init_script 失效（route 注入不生效，diag_94/95/96），
        # 改用 post-load page.evaluate 注入 stub（diag_89/101 验证有效）。__tsStubInstalled 幂等。
        try:
            page.evaluate(TS_RENDER_HOOK)
        except Exception:
            pass

    def register(self, email: str, password: str = None, otp_callback: Optional[Callable[[], str]] = None) -> dict:
        if not password:
            password = _rand_password()
        given_name = _rand_name()
        family_name = _rand_name()

        playwright = None
        browser = None
        context = None
        try:
            playwright, browser = self._launch_browser()
            context = browser.new_context(
                viewport={"width": 1400, "height": 1200},
                user_agent=UA,
            )
            # 拦截真实 turnstile api.js：其在自动化环境下静默不初始化 window.turnstile，
            # 且若稍后执行会覆盖我们注入的 stub。空 body 200 保证 script 加载不报错。
            context.route("**/turnstile/v0/api.js*", lambda route: route.fulfill(status=200, body=""))
            page = context.new_page()

            self._goto_email_signup(page)
            self._ensure_turnstile_stub(page)
            self._submit_email(page, email)

            if not otp_callback:
                code = input("验证码: ").strip()
            else:
                self.log("等待验证码...")
                code = otp_callback() or ""
            if not code:
                raise RuntimeError("未获取到验证码")

            self._submit_otp(page, code)
            self._fill_user_form(page, given_name, family_name, password)
            self._ensure_turnstile_stub(page)
            self._solve_turnstile_on_page(page)
            self._submit_register(page)
            self._accept_tos_if_needed(page)

            cookies = context.cookies()
            if not self._has_auth_cookies(cookies):
                page.wait_for_timeout(5000)
                cookies = context.cookies()
            sso = self._pick_cookie(cookies, "sso")
            sso_rw = self._pick_cookie(cookies, "sso-rw")
            if not sso:
                raise RuntimeError("注册成功但未提取到 sso cookie")

            self.log(f"  ✅ sso={sso[:40]}...")
            self.log("Grok 注册链路完成")
            return {
                "email": email,
                "password": password,
                "given_name": given_name,
                "family_name": family_name,
                "sso": sso,
                "sso_rw": sso_rw,
                "cookies": cookies,
            }
        finally:
            try:
                if context:
                    context.close()
            except Exception:
                pass
            try:
                if browser:
                    browser.close()
            except Exception:
                pass
            try:
                if playwright:
                    playwright.stop()
            except Exception:
                pass
