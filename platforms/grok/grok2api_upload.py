"""grok2api 自动导入（新版 Go API）"""

from __future__ import annotations

import json
import logging
from typing import Tuple

from curl_cffi import CurlMime, requests as cffi_requests

logger = logging.getLogger(__name__)

DEFAULT_POOL = "ssoBasic"
DEFAULT_QUOTAS = {
    "ssoBasic": 80,
    "ssoSuper": 140,
}

# 新版 grok2api 管理端
ADMIN_LOGIN_PATH = "/api/admin/v1/auth/login"
ADMIN_IMPORT_PATH = "/api/admin/v1/accounts/web/import"
ADMIN_EGRESS_NODES_PATH = "/api/admin/v1/egress-nodes"
ADMIN_ACCOUNTS_PATH = "/api/admin/v1/accounts"


def _get_config_value(key: str) -> str:
    try:
        from core.config_store import config_store

        return str(config_store.get(key, "") or "")
    except Exception:
        return ""


def _get_admin_credentials(api_url: str | None, username: str | None, password: str | None) -> Tuple[str, str, str]:
    """解析管理端地址与登录凭据（admin 用户名/密码）。"""
    if not api_url:
        api_url = _get_config_value("grok2api_url")
    if not username:
        username = _get_config_value("grok2api_admin_user") or "admin"
    if not password:
        password = _get_config_value("grok2api_app_key")

    api_url = str(api_url or "").strip()
    username = str(username or "").strip()
    password = str(password or "").strip()
    if not api_url:
        return "", "", "grok2api URL 未配置"
    if not password:
        return "", "", "grok2api 管理密码未配置（设置 → 外部系统 → grok2api App Key 填管理密码）"
    return api_url, username, password


def _extract_sso(account) -> str:
    extra = getattr(account, "extra", {}) or {}
    token = (
        extra.get("sso")
        or extra.get("sso_token")
        or extra.get("sso_rw")
        or getattr(account, "token", "")
    )
    token = str(token or "").strip()
    if token.startswith("sso="):
        token = token[4:]
    return token


def _request_options() -> dict:
    return {
        "proxies": None,
        "verify": False,
        "timeout": 60,
        "impersonate": "chrome110",
    }


def _admin_login(api_url: str, username: str, password: str) -> str:
    """登录管理端，返回 accessToken。"""
    resp = cffi_requests.post(
        f"{api_url.rstrip('/')}{ADMIN_LOGIN_PATH}",
        json={"username": username, "password": password},
        **_request_options(),
    )
    if resp.status_code != 200:
        raise RuntimeError(f"登录失败: HTTP {resp.status_code} - {resp.text[:200]}")

    data = resp.json()
    tokens = ((data.get("data") or {}).get("tokens") or {})
    access_token = str(tokens.get("accessToken") or "")
    if not access_token:
        raise RuntimeError("登录失败: 响应缺少 accessToken")
    return access_token


def _import_web_credentials(api_url: str, access_token: str, sso_text: str) -> Tuple[int, int, int, int]:
    """导入 Grok Web SSO 文本，返回 (created, updated, skipped, syncFailed)。"""
    mime = CurlMime()
    mime.addpart(
        name="files",
        filename="accounts.sso.txt",
        content_type="text/plain",
        data=sso_text.encode("utf-8"),
    )
    resp = cffi_requests.post(
        f"{api_url.rstrip('/')}{ADMIN_IMPORT_PATH}",
        headers={"Authorization": f"Bearer {access_token}"},
        multipart=mime,
        **_request_options(),
    )
    if resp.status_code != 200:
        raise RuntimeError(f"导入失败: HTTP {resp.status_code} - {resp.text[:200]}")

    # 响应为 SSE 流，解析最后的 complete / error 事件
    created = updated = skipped = sync_failed = 0
    for block in resp.text.split("\n\n"):
        event, data = "", ""
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):].strip()
        if event == "error" and data:
            try:
                payload = json.loads(data)
                message = payload.get("message") or "导入失败"
            except Exception:
                message = data
            raise RuntimeError(message)
        if event == "complete" and data:
            try:
                payload = json.loads(data)
                created = int(payload.get("created") or 0)
                updated = int(payload.get("updated") or 0)
                skipped = int(payload.get("skipped") or 0)
                sync_failed = int(payload.get("syncFailed") or 0)
            except Exception:
                pass
    return created, updated, skipped, sync_failed


def _find_health_grok_web_node(api_url: str, access_token: str) -> str:
    """返回一个 healthy 的 grok_web egress 节点 id，没有则返回空串。"""
    resp = cffi_requests.get(
        f"{api_url.rstrip('/')}{ADMIN_EGRESS_NODES_PATH}",
        headers={"Authorization": f"Bearer {access_token}"},
        **_request_options(),
    )
    if resp.status_code != 200:
        return ""
    items = ((resp.json().get("data") or {}).get("items") or [])
    for node in items:
        if node.get("scope") == "grok_web" and node.get("probeStatus") == "healthy":
            return str(node.get("id") or "")
    return ""


def _assign_accounts_to_node(api_url: str, access_token: str, node_id: str, account_ids: list[str]) -> None:
    """把账号绑定到指定 egress 节点（mode=manual）。"""
    resp = cffi_requests.post(
        f"{api_url.rstrip('/')}{ADMIN_EGRESS_NODES_PATH}/{node_id}/accounts",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={"provider": "grok_web", "ids": account_ids, "mode": "manual"},
        **_request_options(),
    )
    if resp.status_code != 200:
        raise RuntimeError(f"绑定节点失败: HTTP {resp.status_code} - {resp.text[:200]}")


def _find_unassigned_account_ids(api_url: str, access_token: str) -> list[str]:
    """查找未绑定 egress 节点的 grok_web 账号 id（按创建时间倒序取前 50）。"""
    resp = cffi_requests.get(
        f"{api_url.rstrip('/')}{ADMIN_ACCOUNTS_PATH}",
        params={"provider": "grok_web", "page": 1, "pageSize": 50},
        headers={"Authorization": f"Bearer {access_token}"},
        **_request_options(),
    )
    if resp.status_code != 200:
        return []
    items = ((resp.json().get("data") or {}).get("items") or [])
    ids = []
    for item in items:
        if not item.get("egressNodeId"):
            ids.append(str(item.get("id") or ""))
    return ids


def upload_to_grok2api(
    account,
    api_url: str | None = None,
    app_key: str | None = None,
    pool_name: str | None = None,
    quota=None,
) -> Tuple[bool, str]:
    """上传 Grok 账号到 grok2api（新版管理 API：登录 → 导入 → 绑定节点）。

    注意：新版 grok2api 已无 App Key 概念，登录使用 admin 用户名 + 密码。
    app_key 参数兼容旧调用方，语义为管理密码（grok2api_app_key 配置项）。
    """
    api_url, username, password = _get_admin_credentials(api_url, None, app_key)

    token = _extract_sso(account)
    if not token:
        return False, "账号缺少 sso token"

    try:
        access_token = _admin_login(api_url, username, password)
        created, updated, skipped, sync_failed = _import_web_credentials(api_url, access_token, token)

        if created + updated <= 0:
            return False, "导入失败: 没有新增或更新的账号"

        # 绑定节点：导入的新账号默认无出口节点，绑定到 healthy 的 grok_web 节点
        node_id = _find_health_grok_web_node(api_url, access_token)
        if node_id:
            unassigned_ids = _find_unassigned_account_ids(api_url, access_token)
            if unassigned_ids:
                try:
                    _assign_accounts_to_node(api_url, access_token, node_id, unassigned_ids)
                    bound = f"，已绑定 {len(unassigned_ids)} 个账号到节点 {node_id}"
                except Exception as e:
                    bound = f"，但绑定节点失败: {e}"
            else:
                bound = ""
        else:
            bound = "，但未找到 healthy 的 grok_web 节点，账号未绑定出口"

        msg = f"导入成功: 新增 {created}、更新 {updated}"
        if skipped:
            msg += f"、跳过 {skipped}"
        if sync_failed:
            msg += f"、同步失败 {sync_failed}"
        return True, msg + bound
    except Exception as e:
        logger.error(f"grok2api 导入异常: {e}")
        return False, f"导入异常: {e}"