"""OpenAI 专用 HTTP 客户端"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Tuple

from core.http_client import HTTPClient, HTTPClientError, RequestConfig

logger = logging.getLogger(__name__)

AUTH_BASE = "https://auth.openai.com"
CLEARANCE_TARGET = "https://auth.openai.com/"
_FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL") or "http://127.0.0.1:8191/v1"
_CLEARANCE_TIMEOUT = int(os.getenv("CF_CLEARANCE_TIMEOUT", "60"))
_CLEARANCE_ENABLED = os.getenv("APP_ENABLE_CF_CLEARANCE", "1").lower() not in {"0", "false", "no"}

_CF_CHALLENGE_MARKERS = (
    "cf-chl-",
    "__cf_chl_",
    "cf-browser-verification",
    "challenge-platform",
    "Just a moment",
    "cf_error_code",
    "cf-challenge",
)


def is_cloudflare_challenge(response: Any) -> bool:
    """Detect a Cloudflare interstitial (403/503 with challenge markers)."""
    status = int(getattr(response, "status_code", 0) or 0)
    if status not in (403, 503):
        return False
    try:
        text = str(getattr(response, "text", "") or "")
    except Exception:
        text = ""
    lower = text.lower()
    return any(marker.lower() in lower for marker in _CF_CHALLENGE_MARKERS)


@dataclass
class ClearanceBundle:
    """A valid cf_clearance cookie set plus the matching user-agent."""

    cookies: Dict[str, str] = field(default_factory=dict)
    user_agent: str = ""
    target_host: str = "auth.openai.com"

    def is_valid(self) -> bool:
        return bool(self.cookies.get("cf_clearance"))


def apply_clearance_to_session(session: Any, bundle: ClearanceBundle) -> None:
    """Inject a clearance bundle's cookies and user-agent into a session."""
    if bundle is None or not bundle.is_valid():
        return
    if not bundle.user_agent:
        return
    try:
        session.headers["User-Agent"] = bundle.user_agent
    except Exception:
        pass
    for name, value in bundle.cookies.items():
        try:
            session.cookies.set(name, value, domain=f".{bundle.target_host}")
        except Exception:
            try:
                session.cookies.set(name, value, domain=bundle.target_host)
            except Exception:
                pass


def _parse_flaresolverr_cookies(solution: Any) -> Dict[str, str]:
    """Extract {name: value} cookies from a FlareSolverr solution payload."""
    cookies: Dict[str, str] = {}
    raw = (solution or {}).get("cookies")
    if isinstance(raw, dict):
        name = str(raw.get("name") or "").strip()
        value = str(raw.get("value") or "").strip()
        if name and value:
            cookies[name] = value
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
            if name and value:
                cookies[name] = value
    return cookies


class CloudflareClearanceProvider:
    """Obtain a valid cf_clearance cookie for auth.openai.com.

    Prefers a local FlareSolverr instance; falls back to a patchright/Playwright
    browser that loads the page and harvests the cookie after the challenge.
    """

    def __init__(self, proxy: str = "", target: str = CLEARANCE_TARGET):
        self.proxy = str(proxy or "").strip()
        self.target = str(target or CLEARANCE_TARGET).strip()
        self._browser_result: Optional[ClearanceBundle] = None

    def solve(self, *, force: bool = False) -> Optional[ClearanceBundle]:
        if not _CLEARANCE_ENABLED:
            return None
        if self._browser_result is not None and not force:
            return self._browser_result
        bundle = self._solve_flaresolverr()
        if bundle is None:
            bundle = self._solve_browser()
        if bundle is not None and bundle.is_valid():
            self._browser_result = bundle
            return bundle
        return None

    def _solve_flaresolverr(self) -> Optional[ClearanceBundle]:
        try:
            import requests as std_requests
        except Exception:
            return None
        payload: Dict[str, Any] = {
            "cmd": "request.get",
            "url": self.target,
            "maxTimeout": 60000,
        }
        if self.proxy:
            payload["proxy"] = {"url": self.proxy}
        try:
            resp = std_requests.post(_FLARESOLVERR_URL, json=payload, timeout=_CLEARANCE_TIMEOUT)
            data = resp.json()
        except Exception:
            return None
        if not isinstance(data, dict) or data.get("status") != "ok":
            return None
        solution = data.get("solution")
        if not isinstance(solution, dict):
            return None
        cookies = _parse_flaresolverr_cookies(solution)
        if not cookies.get("cf_clearance"):
            return None
        user_agent = str(solution.get("userAgent") or "").strip()
        return ClearanceBundle(cookies=cookies, user_agent=user_agent)

    def _solve_browser(self) -> Optional[ClearanceBundle]:
        try:
            from patchright.sync_api import sync_playwright
        except Exception:
            try:
                from playwright.sync_api import sync_playwright
            except Exception:
                logger.warning("Cloudflare clearance: 无可用浏览器（patchright/playwright）")
                return None
        launch_args: Dict[str, Any] = {
            "headless": True,
            "args": ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        }
        if self.proxy:
            from core.proxy_utils import build_playwright_proxy_config
            proxy_config = build_playwright_proxy_config(self.proxy)
            if proxy_config:
                launch_args["proxy"] = proxy_config
        try:
            with sync_playwright() as p:
                browser = None
                for channel in ("chrome", "msedge", None):
                    try:
                        kwargs = dict(launch_args)
                        if channel:
                            kwargs["channel"] = channel
                        else:
                            kwargs.pop("channel", None)
                        browser = p.chromium.launch(**kwargs)
                        break
                    except Exception:
                        continue
                if browser is None:
                    return None
                try:
                    context = browser.new_context(
                        viewport={"width": 1440, "height": 900},
                        user_agent=(
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/136.0.7103.92 Safari/537.36"
                        ),
                        ignore_https_errors=True,
                    )
                    page = context.new_page()
                    page.goto(self.target, wait_until="domcontentloaded", timeout=30000)
                    # Allow the JS challenge to complete.
                    deadline = time.time() + _CLEARANCE_TIMEOUT
                    while time.time() < deadline:
                        cookies = context.cookies(self.target)
                        cf = next(
                            (c for c in cookies if c.get("name") == "cf_clearance"),
                            None,
                        )
                        if cf and cf.get("value"):
                            return ClearanceBundle(
                                cookies={"cf_clearance": str(cf["value"])},
                                user_agent=context.user_agent,
                            )
                        time.sleep(1.5)
                    return None
                finally:
                    browser.close()
        except Exception:
            return None

class OpenAIHTTPClient(HTTPClient):
    """
    OpenAI 专用 HTTP 客户端
    包含 OpenAI API 特定的请求方法
    """

    def __init__(
        self,
        proxy_url: Optional[str] = None,
        config: Optional[RequestConfig] = None
    ):
        """
        初始化 OpenAI HTTP 客户端

        Args:
            proxy_url: 代理 URL
            config: 请求配置
        """
        super().__init__(proxy_url, config)

        # OpenAI 特定的默认配置
        if config is None:
            self.config.timeout = 30
            self.config.max_retries = 3
            # Match the reference project's normal browser TLS profile.  This
            # only keeps the request/session fingerprint consistent; it does
            # not solve or bypass Cloudflare/Sentinel challenges.
            self.config.impersonate = "chrome136"

        self._clearance_provider = CloudflareClearanceProvider(proxy_url or "")
        self._clearance_applied = False


        # 默认请求头
        self.default_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/136.0.7103.92 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Cache-Control": "no-cache",
            "DNT": "1",
            "Sec-GPC": "1",
            "Sec-CH-UA": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
            "Sec-CH-UA-Arch": '"x86"',
            "Sec-CH-UA-Bitness": '"64"',
            "Sec-CH-UA-Full-Version-List": '"Chromium";v="136.0.7103.92", "Google Chrome";v="136.0.7103.92", "Not.A/Brand";v="99.0.0.0"',
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Model": '""',
            "Sec-CH-UA-Platform": '"Windows"',
            "Sec-CH-UA-Platform-Version": '"10.0.0"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
        }

    def prepare_browser_session(self):
        """Apply the reference project's normal browser session defaults."""
        session = self.session
        try:
            # Keep OAuth state on the explicitly configured proxy only.
            session.trust_env = False
        except Exception:
            pass
        session.headers.update(self.default_headers)
        return session

    def refresh_clearance(self, *, force: bool = False) -> bool:
        """Obtain and apply a cf_clearance cookie for auth.openai.com.

        Returns True when a valid clearance bundle is in place for the session.
        The result is cached until the caller forces a refresh.
        """
        if self._clearance_applied and not force:
            return True
        try:
            bundle = self._clearance_provider.solve(force=force)
        except Exception as exc:
            logger.warning(f"Cloudflare clearance 获取异常: {exc}")
            return False
        if bundle is None or not bundle.is_valid():
            logger.warning("Cloudflare clearance 获取失败（FlareSolverr 与浏览器均不可用）")
            return False
        try:
            apply_clearance_to_session(self.session, bundle)
        except Exception as exc:
            logger.warning(f"Cloudflare clearance 注入失败: {exc}")
            return False
        self._clearance_applied = True
        logger.info("Cloudflare clearance 已就绪 (cf_clearance 已注入)")
        return True


    def check_ip_location(self) -> Tuple[bool, Optional[str]]:
        """
        检查 IP 地理位置

        Returns:
            Tuple[是否支持, 位置信息]
        """
        try:
            response = self.get("https://cloudflare.com/cdn-cgi/trace", timeout=10)
            trace_text = response.text

            # 解析位置信息
            import re
            loc_match = re.search(r"loc=([A-Z]+)", trace_text)
            loc = loc_match.group(1) if loc_match else None

            # 检查是否支持
            if loc in ["CN", "HK", "MO", "TW"]:
                return False, loc
            return True, loc

        except Exception as e:
            logger.error(f"检查 IP 地理位置失败: {e}")
            return False, None

    def send_openai_request(
        self,
        endpoint: str,
        method: str = "POST",
        data: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        发送 OpenAI API 请求

        Args:
            endpoint: API 端点
            method: HTTP 方法
            data: 表单数据
            json_data: JSON 数据
            headers: 请求头
            **kwargs: 其他参数

        Returns:
            响应 JSON 数据

        Raises:
            HTTPClientError: 请求失败
        """
        # 合并请求头
        request_headers = self.default_headers.copy()
        if headers:
            request_headers.update(headers)

        # 设置 Content-Type
        if json_data is not None and "Content-Type" not in request_headers:
            request_headers["Content-Type"] = "application/json"
        elif data is not None and "Content-Type" not in request_headers:
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"

        try:
            response = self.request(
                method,
                endpoint,
                data=data,
                json=json_data,
                headers=request_headers,
                **kwargs
            )

            # 检查响应状态码
            response.raise_for_status()

            # 尝试解析 JSON
            try:
                return response.json()
            except json.JSONDecodeError:
                return {"raw_response": response.text}

        except cffi_requests.RequestsError as e:
            raise HTTPClientError(f"OpenAI 请求失败: {endpoint} - {e}")

    def check_sentinel(self, did: str, proxies: Optional[Dict] = None) -> Optional[str]:
        """
        检查 Sentinel 拦截

        Args:
            did: Device ID
            proxies: 代理配置

        Returns:
            Sentinel token 或 None
        """
        from .constants import OPENAI_API_ENDPOINTS

        try:
            sen_req_body = f'{{"p":"","id":"{did}","flow":"authorize_continue"}}'

            response = self.post(
                OPENAI_API_ENDPOINTS["sentinel"],
                headers={
                    "origin": "https://sentinel.openai.com",
                    "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                    "content-type": "text/plain;charset=UTF-8",
                },
                data=sen_req_body,
            )

            if response.status_code == 200:
                return response.json().get("token")
            else:
                logger.warning(f"Sentinel 检查失败: {response.status_code}")
                return None

        except Exception as e:
            logger.error(f"Sentinel 检查异常: {e}")
            return None


def create_http_client(
    proxy_url: Optional[str] = None,
    config: Optional[RequestConfig] = None
) -> HTTPClient:
    """
    创建 HTTP 客户端工厂函数

    Args:
        proxy_url: 代理 URL
        config: 请求配置

    Returns:
        HTTPClient 实例
    """
    return HTTPClient(proxy_url, config)


def create_openai_client(
    proxy_url: Optional[str] = None,
    config: Optional[RequestConfig] = None
) -> OpenAIHTTPClient:
    """
    创建 OpenAI HTTP 客户端工厂函数

    Args:
        proxy_url: 代理 URL
        config: 请求配置

    Returns:
        OpenAIHTTPClient 实例
    """
    return OpenAIHTTPClient(proxy_url, config)
