"""
支付核心逻辑 — 生成 Plus/Team 支付链接、无痕打开浏览器、检测订阅状态
"""

from __future__ import annotations

import logging
import random
import subprocess
import sys
import time
import uuid
from typing import Optional

from curl_cffi import requests as cffi_requests
from core.proxy_utils import build_requests_proxy_config
from .platform_oauth import PLATFORM_AUTH0_CLIENT

# from ..database.models import Account  # removed: external dep

logger = logging.getLogger(__name__)

PAYMENT_CHECKOUT_URL = "https://chatgpt.com/backend-api/payments/checkout"
TEAM_CHECKOUT_BASE_URL = "https://chatgpt.com/checkout/openai_llc/"
STRIPE_CHECKOUT_BASE_URL = "https://checkout.stripe.com/c/pay/"
_STRIPE_VERSION = "2025-03-31.basil; checkout_server_update_beta=v1; checkout_manual_approval_preview=v1"
_PAYMENT_IMPERSONATE = "chrome136"


def _resolve_stripe_hosted_url(checkout_data: dict, proxy: Optional[str] = None) -> str:
    """调 Stripe init 获取完整的 hosted checkout URL（含 #fragment）。

    checkout.stripe.com/c/pay/{cs_id} 只有 session ID，缺少 #fragment
    会被 Stripe 判为 "incomplete link"。必须通过 Stripe init 拿到完整 URL。
    """
    cs_id = str(checkout_data.get("checkout_session_id") or "").strip()
    pk = str(checkout_data.get("publishable_key") or "").strip()
    if not cs_id or not pk:
        return ""

    stripe_js_id = str(uuid.uuid4())
    body = {
        "browser_locale": "en-US",
        "browser_timezone": "America/New_York",
        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
        "elements_session_client[elements_init_source]": "custom_checkout",
        "elements_session_client[referrer_host]": "chatgpt.com",
        "elements_session_client[stripe_js_id]": stripe_js_id,
        "elements_session_client[locale]": "en",
        "elements_session_client[is_aggregation_expected]": "false",
        "elements_options_client[saved_payment_method][enable_save]": "auto",
        "elements_options_client[saved_payment_method][enable_redisplay]": "auto",
        "key": pk,
        "_stripe_version": _STRIPE_VERSION,
    }
    try:
        resp = cffi_requests.post(
            f"https://api.stripe.com/v1/payment_pages/{cs_id}/init",
            data=body,
            headers={
                "Accept": "application/json",
                "Origin": "https://js.stripe.com",
                "Referer": "https://js.stripe.com/",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            proxies=_build_proxies(proxy),
            timeout=20,
            impersonate=_PAYMENT_IMPERSONATE,
        )
        if resp.status_code == 200:
            return str(resp.json().get("stripe_hosted_url") or "").strip()
    except Exception as exc:
        logger.warning(f"Stripe init 失败: {exc}")
    return ""


def _build_proxies(proxy: Optional[str]) -> Optional[dict]:
    return build_requests_proxy_config(proxy)


def _base_payment_headers(account: "Account") -> dict:
    """Return the checkout request headers aligned with the reference project."""
    headers = {
        "Authorization": f"Bearer {account.access_token}",
        "Content-Type": "application/json",
        "oai-language": "zh-CN",
        "auth0-client": PLATFORM_AUTH0_CLIENT,
        "accept": "application/json, text/plain, */*",
        "accept-encoding": "gzip, deflate, br",
        "accept-language": "en-US,en;q=0.9",
        "origin": "https://chatgpt.com",
        "priority": "u=1, i",
        "referer": "https://chatgpt.com/",
        "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
        "sec-ch-ua-full-version-list": '"Chromium";v="136.0.7103.92", "Google Chrome";v="136.0.7103.92", "Not.A/Brand";v="99.0.0.0"',
        "sec-ch-ua-model": '""',
        "sec-ch-ua-platform-version": '"10.0.0"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.7103.92 Safari/537.36"
        ),
    }
    try:
        from .utils import generate_datadog_trace
        headers.update(generate_datadog_trace())
    except Exception:
        pass
    if getattr(account, "cookies", ""):
        headers["cookie"] = account.cookies
        oai_did = _extract_oai_did(account.cookies)
        if oai_did:
            headers["oai-device-id"] = oai_did
    return headers


def _post_checkout_with_cf_retry(
    payload: dict,
    account: "Account",
    proxy: Optional[str],
    *,
    label: str,
) -> dict:
    """POST to the payments checkout endpoint, retrying once on a CF challenge."""
    headers = _base_payment_headers(account)
    for attempt in range(2):
        try:
            resp = cffi_requests.post(
                PAYMENT_CHECKOUT_URL,
                headers=headers,
                json=payload,
                proxies=_build_proxies(proxy),
                timeout=30,
                impersonate=_PAYMENT_IMPERSONATE,
            )
        except Exception as exc:
            logger.warning(f"{label} checkout 请求异常: {exc}")
            raise
        if not _is_cf_challenge(resp):
            return resp
        logger.warning(f"{label} checkout 命中 Cloudflare challenge (HTTP {resp.status_code})")
        if attempt == 1:
            break
        cf_cookie = _obtain_cf_clearance(proxy)
        if not cf_cookie:
            break
        headers["cookie"] = f"{headers.get('cookie', '')}; cf_clearance={cf_cookie}".strip("; ")
        time.sleep(random.uniform(1.5, 3.0))
    return resp


def _is_cf_challenge(resp: object) -> bool:
    try:
        from .http_client import is_cloudflare_challenge
        return bool(is_cloudflare_challenge(resp))
    except Exception:
        return False


def _obtain_cf_clearance(proxy: Optional[str]) -> Optional[str]:
    try:
        from .http_client import CloudflareClearanceProvider
        bundle = CloudflareClearanceProvider(
            proxy or "", target="https://chatgpt.com/"
        ).solve()
        if bundle and bundle.is_valid():
            return str(bundle.cookies.get("cf_clearance") or "").strip() or None
    except Exception:
        pass
    return None


_COUNTRY_CURRENCY_MAP = {
    "SG": "SGD",
    "US": "USD",
    "TR": "TRY",
    "JP": "JPY",
    "HK": "HKD",
    "GB": "GBP",
    "EU": "EUR",
    "AU": "AUD",
    "CA": "CAD",
    "IN": "INR",
    "BR": "BRL",
    "MX": "MXN",
}


def _raise_checkout_error(resp, label: str):
    """Surface the real error body from the checkout endpoint instead of a bare status code."""
    try:
        body = resp.text[:500] if resp.text else ""
    except Exception:
        body = ""
    raise RuntimeError(f"{label} checkout 失败 HTTP {resp.status_code}: {body}")


def _extract_oai_did(cookies_str: str) -> Optional[str]:
    """从 cookie 字符串中提取 oai-device-id"""
    for part in cookies_str.split(";"):
        part = part.strip()
        if part.startswith("oai-did="):
            return part[len("oai-did=") :].strip()
    return None


def _parse_cookie_str(cookies_str: str, domain: str) -> list:
    """将 'key=val; key2=val2' 格式解析为 Playwright cookie 列表"""
    cookies = []
    for part in cookies_str.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        cookies.append(
            {
                "name": name.strip(),
                "value": value.strip(),
                "domain": domain,
                "path": "/",
            }
        )
    return cookies


def _open_url_system_browser(url: str) -> bool:
    """回退方案：调用系统浏览器以无痕模式打开"""
    platform = sys.platform
    try:
        if platform == "win32":
            for browser, flag in [("chrome", "--incognito"), ("msedge", "--inprivate")]:
                try:
                    subprocess.Popen(f'start {browser} {flag} "{url}"', shell=True)
                    return True
                except Exception:
                    continue
        elif platform == "darwin":
            subprocess.Popen(
                ["open", "-a", "Google Chrome", "--args", "--incognito", url]
            )
            return True
        else:
            for binary in ["google-chrome", "chromium-browser", "chromium"]:
                try:
                    subprocess.Popen([binary, "--incognito", url])
                    return True
                except FileNotFoundError:
                    continue
    except Exception as e:
        logger.warning(f"系统浏览器无痕打开失败: {e}")
    return False


def generate_plus_link(
    account: Account,
    proxy: Optional[str] = None,
    country: str = "SG",
    with_promo: bool = False,
) -> str:
    """生成 Plus 支付链接（后端携带账号 cookie 发请求）

    with_promo 默认 False，与参考项目一致：plus-1-month-free promo 已不稳定，
    默认不带 promo；需要时显式传 with_promo=True。
    """
    if not account.access_token:
        raise ValueError("账号缺少 access_token")

    currency = _COUNTRY_CURRENCY_MAP.get(country, "USD")

    payload = {
        "entry_point": "all_plans_pricing_modal",
        "plan_name": "chatgptplusplan",
        "billing_details": {"country": country, "currency": currency},
        "checkout_ui_mode": "custom",
    }
    if with_promo:
        payload["promo_campaign"] = {
            "promo_campaign_id": "plus-1-month-free",
            "is_coupon_from_query_param": False,
        }

    resp = _post_checkout_with_cf_retry(payload, account, proxy, label="Plus")
    if resp.status_code >= 400:
        _raise_checkout_error(resp, "Plus")
    data = resp.json()
    if "checkout_session_id" in data:
        hosted_url = _resolve_stripe_hosted_url(data, proxy)
        return hosted_url or STRIPE_CHECKOUT_BASE_URL + data["checkout_session_id"]
    raise ValueError(data.get("detail", "API 未返回 checkout_session_id"))


def generate_team_link(
    account: Account,
    workspace_name: str = "MyTeam",
    price_interval: str = "month",
    seat_quantity: int = 5,
    proxy: Optional[str] = None,
    country: str = "SG",
    with_promo: bool = False,
) -> str:
    """生成 Team 支付链接（后端携带账号 cookie 发请求）"""
    if not account.access_token:
        raise ValueError("账号缺少 access_token")

    currency = _COUNTRY_CURRENCY_MAP.get(country, "USD")

    payload = {
        "entry_point": "all_plans_pricing_modal",
        "plan_name": "chatgptteamplan",
        "team_plan_data": {
            "workspace_name": workspace_name,
            "price_interval": price_interval,
            "seat_quantity": seat_quantity,
        },
        "billing_details": {"country": country, "currency": currency},
        "cancel_url": "https://chatgpt.com/#pricing",
        "checkout_ui_mode": "custom",
    }
    if with_promo:
        payload["promo_campaign"] = {
            "promo_campaign_id": "team-1-month-free",
            "is_coupon_from_query_param": True,
        }

    resp = _post_checkout_with_cf_retry(payload, account, proxy, label="Team")
    if resp.status_code >= 400:
        _raise_checkout_error(resp, "Team")
    data = resp.json()
    if "checkout_session_id" in data:
        hosted_url = _resolve_stripe_hosted_url(data, proxy)
        return hosted_url or STRIPE_CHECKOUT_BASE_URL + data["checkout_session_id"]
    raise ValueError(data.get("detail", "API 未返回 checkout_session_id"))


def open_url_incognito(url: str, cookies_str: Optional[str] = None) -> bool:
    """用 Playwright 以无痕模式打开 URL，可注入 cookie"""
    import threading

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("playwright 未安装，回退到系统浏览器")
        return _open_url_system_browser(url)

    def _launch():
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False, args=["--incognito"])
                ctx = browser.new_context()
                if cookies_str:
                    ctx.add_cookies(_parse_cookie_str(cookies_str, "chatgpt.com"))
                page = ctx.new_page()
                page.goto(url)
                # 保持窗口打开直到用户关闭
                page.wait_for_timeout(300_000)  # 最多等待 5 分钟
        except Exception as e:
            logger.warning(f"Playwright 无痕打开失败: {e}")

    threading.Thread(target=_launch, daemon=True).start()
    return True


def check_subscription_status(account: Account, proxy: Optional[str] = None) -> str:
    """
    检测账号当前订阅状态。

    Returns:
        'free' / 'plus' / 'team'
    """
    if not account.access_token:
        raise ValueError("账号缺少 access_token")

    headers = {
        "Authorization": f"Bearer {account.access_token}",
        "Content-Type": "application/json",
    }

    resp = cffi_requests.get(
        "https://chatgpt.com/backend-api/me",
        headers=headers,
        proxies=_build_proxies(proxy),
        timeout=20,
        impersonate="chrome110",
    )
    resp.raise_for_status()
    data = resp.json()

    # 解析订阅类型
    plan = data.get("plan_type") or ""
    if "team" in plan.lower():
        return "team"
    if "plus" in plan.lower():
        return "plus"

    # 尝试从 orgs 或 workspace 信息判断
    orgs = data.get("orgs", {}).get("data", [])
    for org in orgs:
        settings_ = org.get("settings", {})
        if settings_.get("workspace_plan_type") in ("team", "enterprise"):
            return "team"

    return "free"
