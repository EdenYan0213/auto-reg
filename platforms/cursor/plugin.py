"""Cursor 平台插件"""
from core.base_platform import BasePlatform, Account, AccountStatus, RegisterConfig
from core.base_mailbox import BaseMailbox
from core.registry import register
from platforms.cursor.core import CursorRegister, UA, CURSOR


@register
class CursorPlatform(BasePlatform):
    name = "cursor"
    display_name = "Cursor"
    version = "1.0.0"
    supported_executors = ["protocol", "headless", "headed"]

    def __init__(self, config: RegisterConfig = None, mailbox: BaseMailbox = None):
        super().__init__(config)
        self.mailbox = mailbox

    def register(self, email: str, password: str = None) -> Account:
        log = getattr(self, '_log_fn', print)
        extra = self.config.extra or {}
        mail_acct = self.mailbox.get_email() if self.mailbox else None
        email = email or (mail_acct.email if mail_acct else None)
        if not email:
            raise RuntimeError("邮箱服务未返回邮箱地址")
        before_ids = self.mailbox.get_current_ids(mail_acct) if mail_acct else set()
        otp_timeout = self.get_mailbox_otp_timeout()

        def otp_cb():
            log("等待验证码...")
            code = self.mailbox.wait_for_code(
                mail_acct,
                keyword="",
                timeout=otp_timeout,
                before_ids=before_ids,
            )
            if code: log(f"验证码: {code}")
            return code

        # 与 Grok 保持一致：可通过 *_registration_mode 选择 protocol/browser，
        # executor_type 决定浏览器 headless/headed。Cursor 默认走浏览器，
        # 因为 AuthKit 需要页面生成 signFingerprint signals。
        registration_mode = str(
            extra.get("cursor_registration_mode") or "browser"
        ).strip().lower()
        if registration_mode in {"browser", "playwright", "headless", "headed"}:
            from platforms.cursor.browser import CursorBrowserRegister

            def _extra_bool(name: str, default: bool = False) -> bool:
                value = extra.get(name)
                if value in (None, ""):
                    return default
                return str(value).strip().lower() in {"1", "true", "yes", "on"}

            log(f"使用浏览器模式注册: {email}")
            result = CursorBrowserRegister(
                mailbox=self.mailbox,
                proxy=self.config.proxy,
                log_fn=log,
                headless=(self.config.executor_type or "") == "headless",
                profile_dir=extra.get("cursor_browser_profile_dir") or None,
                human_timeout_seconds=extra.get(
                    "cursor_browser_timeout_seconds", 180
                ),
                http_bootstrap=_extra_bool("cursor_http_bootstrap"),
            ).register(
                email=email,
                password=password or "",
                otp_callback=otp_cb if self.mailbox else None,
                otp_timeout=otp_timeout,
            )
        else:
            yescaptcha_key = extra.get("yescaptcha_key", "")
            reg = CursorRegister(proxy=self.config.proxy, log_fn=log)
            result = reg.register(
                email=email,
                password=password,
                otp_callback=otp_cb if self.mailbox else None,
                yescaptcha_key=yescaptcha_key,
            )

        return Account(
            platform="cursor",
            email=result["email"],
            password=result["password"],
            token=result["token"],
            status=AccountStatus.REGISTERED,
        )

    def check_valid(self, account: Account) -> bool:
        from curl_cffi import requests as curl_req
        try:
            r = curl_req.get(
                f"{CURSOR}/api/auth/me",
                headers={"Cookie": f"WorkosCursorSessionToken={account.token}",
                         "user-agent": UA},
                impersonate="chrome124", timeout=15,
            )
            return r.status_code == 200
        except Exception:
            return False

    def get_platform_actions(self) -> list:
        """返回平台支持的操作列表"""
        return [
            {"id": "switch_account", "label": "切换到桌面应用", "params": []},
            {"id": "get_user_info", "label": "获取用户信息", "params": []},
        ]

    def execute_action(self, action_id: str, account: Account, params: dict) -> dict:
        """执行平台操作"""
        if action_id == "switch_account":
            from platforms.cursor.switch import switch_cursor_account, restart_cursor_ide
            
            token = account.token
            if not token:
                return {"ok": False, "error": "账号缺少 token"}
            
            ok, msg = switch_cursor_account(token)
            if not ok:
                return {"ok": False, "error": msg}
            
            restart_ok, restart_msg = restart_cursor_ide()
            return {
                "ok": True,
                "data": {
                    "message": f"{msg}。{restart_msg}" if restart_ok else msg,
                }
            }
        
        elif action_id == "get_user_info":
            from platforms.cursor.switch import get_cursor_user_info
            
            token = account.token
            if not token:
                return {"ok": False, "error": "账号缺少 token"}
            
            user_info = get_cursor_user_info(token)
            if user_info:
                return {"ok": True, "data": user_info}
            return {"ok": False, "error": "获取用户信息失败"}
        
        raise NotImplementedError(f"未知操作: {action_id}")
