"""Grok (x.ai) 平台插件"""
from core.base_platform import BasePlatform, Account, AccountStatus, RegisterConfig
from core.base_mailbox import BaseMailbox
from core.registry import register


@register
class GrokPlatform(BasePlatform):
    name = "grok"
    display_name = "Grok"
    version = "1.0.0"

    def __init__(self, config: RegisterConfig = None, mailbox: BaseMailbox = None):
        super().__init__(config)
        self.mailbox = mailbox

    def _protocol_config(self, yescaptcha_key: str) -> dict:
        """将 auto_reg 的任务配置映射到参考项目的协议客户端。"""
        import os

        extra = self.config.extra or {}
        solver_type = str(self.config.captcha_solver or "yescaptcha").strip().lower()
        solver_url = (
            extra.get("grok_solver_url")
            or extra.get("solver_url")
            or os.getenv("GROK_SOLVER_URL")
            or "http://127.0.0.1:8877"
        )
        if solver_type in {"local", "local_solver"} or not yescaptcha_key:
            provider = "local"
            api_key = ""
            api_base = str(solver_url).strip().rstrip("/")
        else:
            provider = "yescaptcha" if solver_type == "yescaptcha" else solver_type
            api_key = yescaptcha_key
            api_base = str(extra.get("grok_captcha_api_base") or "").strip()

        def value(name: str, default=None):
            current = extra.get(name)
            return default if current in (None, "") else current

        return {
            # 参考项目的稳定路径：xconsole + Next Server Action + gRPC-Web。
            "signup_flow": str(value("grok_signup_flow", value("signup_flow", "xconsole"))).strip().lower(),
            "provider": provider,
            "api_key": api_key,
            "api_base": api_base,
            "sitekey": str(value("grok_sitekey", value("sitekey", ""))).strip(),
            "action": str(value("grok_action", value("action", ""))).strip(),
            "action_id": str(value("grok_action_id", value("action_id", ""))).strip(),
            "castle_pk": str(value("grok_castle_pk", value("castle_pk", ""))).strip(),
            "castle_sdk_url": str(value("grok_castle_sdk_url", value("castle_sdk_url", ""))).strip(),
            "next_router_state_tree": value("grok_next_router_state_tree", value("next_router_state_tree", "")),
            "request_timeout": value("grok_request_timeout", 30),
            "captcha_timeout": value("grok_captcha_timeout", 180),
            "captcha_poll_interval": value("grok_captcha_poll_interval", 3),
            "local_real_page": True,
            "local_concurrency": value("grok_local_concurrency", 2),
            "local_attempt_timeout": value("grok_local_attempt_timeout", 60),
            "local_queue_timeout": value("grok_local_queue_timeout", 60),
            "local_max_attempts": value("grok_local_max_attempts", 3),
            "castle_timeout": value("grok_castle_timeout", 20),
            "user_agent": str(value("grok_user_agent", "")).strip(),
            "impersonate": str(value("grok_impersonate", "chrome146")).strip(),
            # 参考项目的成功路径：由 8877 solver 保持注册页面、验证码和提交在同一浏览器会话。
            "browser_flow_enabled": True,
            "xconsole_reference_signup_enabled": False,
        }

    def _register_protocol(self, current_email: str, password: str | None, otp_cb, log) -> dict:
        from platforms.grok.protocol import GrokProtocolClient

        from core.config_store import config_store

        extra = self.config.extra or {}
        yescaptcha_key = str(
            extra.get("yescaptcha_key") or config_store.get("yescaptcha_key", "") or ""
        ).strip()
        protocol_config = self._protocol_config(yescaptcha_key)
        client = GrokProtocolClient(
            protocol_config,
            proxy=self.config.proxy or "",
            log=log,
        )
        try:
            log("Step1: 协议打开 Grok 注册页...")
            metadata = client.bootstrap()
            log(f"  注册元数据已加载，sitekey={metadata.sitekey[:12]}...")

            log(f"Step2: 提交邮箱 {current_email} ...")
            client.send_email_validation_code(current_email)
            log("等待验证码...")
            code = otp_cb() if otp_cb else input("验证码: ").strip()
            code = str(code or "").replace("-", "").replace(" ", "").strip()
            if not code:
                raise RuntimeError("未获取到 Grok 邮箱验证码")

            log(f"Step3: 协议提交邮箱验证码 {code} ...")
            client.verify_email_validation_code(current_email, code)

            from platforms.grok.core import _rand_name, _rand_password

            given_name = _rand_name()
            family_name = _rand_name()
            actual_password = password or _rand_password()
            log(f"Step4: 校验用户信息 {given_name} {family_name} ...")
            client.validate_password(current_email, actual_password)

            log("Step5: 在同一真实浏览器会话中完成 Turnstile...")
            token = client.solve_turnstile()
            if client.browser_session_id:
                log("  Turnstile 已交由 8877 同会话浏览器处理")
            else:
                if not token:
                    raise RuntimeError("Turnstile solver 未返回 token")
                log(f"  Turnstile token 已获取: {token[:24]}...")

            log("Step6: 通过 Next Server Action 创建账号...")
            result = client.create_user_and_session(
                email=current_email,
                code=code,
                given_name=given_name,
                family_name=family_name,
                password=actual_password,
                turnstile_token=token,
            )
            result.update(
                {
                    "email": current_email,
                    "password": actual_password,
                    "given_name": given_name,
                    "family_name": family_name,
                }
            )
            if not str(result.get("sso") or "").strip():
                raise RuntimeError("Grok 账号已提交，但没有获得 sso cookie")
            log("  Grok 账号创建并获取 sso cookie 成功")
            return result
        finally:
            client.close()

    def _register_browser_legacy(self, current_email: str, password: str | None, otp_cb, log) -> dict:
        """显式兼容旧浏览器流程；默认不走这里。"""
        from platforms.grok.core import GrokRegister

        from core.config_store import config_store

        yescaptcha_key = self.config.extra.get("yescaptcha_key") or config_store.get("yescaptcha_key", "")
        captcha_solver = self._make_captcha(key=yescaptcha_key)
        return GrokRegister(
            captcha_solver=captcha_solver,
            yescaptcha_key=yescaptcha_key,
            proxy=self.config.proxy,
            log_fn=log,
        ).register(
            email=current_email,
            password=password,
            otp_callback=otp_cb if self.mailbox else None,
        )

    def register(self, email: str, password: str = None) -> Account:
        log = getattr(self, "_log_fn", print)
        extra = self.config.extra or {}
        mode = str(extra.get("grok_registration_mode") or "protocol").strip().lower()
        mailbox_attempts = 1 if email else int(extra.get("grok_mailbox_attempts", 8))
        mailbox_attempts = max(1, min(mailbox_attempts, 20))
        otp_timeout = self.get_mailbox_otp_timeout()
        last_error = None

        for attempt in range(1, mailbox_attempts + 1):
            mail_acct = None
            current_email = email
            if self.mailbox and not current_email:
                mail_acct = self.mailbox.get_email()
                current_email = mail_acct.email if mail_acct else None
            if not current_email:
                raise RuntimeError("邮箱服务未返回邮箱地址")
            log(f"邮箱: {current_email}")
            before_ids = self.mailbox.get_current_ids(mail_acct) if (self.mailbox and mail_acct) else set()

            def otp_cb():
                log("等待验证码...")
                code = self.mailbox.wait_for_code(
                    mail_acct,
                    keyword="",
                    timeout=otp_timeout,
                    before_ids=before_ids,
                    code_pattern=r"[A-Z0-9]{3}-[A-Z0-9]{3}",
                )
                if code:
                    code = code.replace("-", "").replace(" ", "")
                    log(f"验证码: {code}")
                return code

            try:
                if mode in {"browser", "legacy", "playwright"}:
                    result = self._register_browser_legacy(current_email, password, otp_cb, log)
                else:
                    result = self._register_protocol(
                        current_email,
                        password,
                        otp_cb if self.mailbox else None,
                        log,
                    )
                break
            except Exception as error:
                last_error = error
                retryable = bool(getattr(error, "mail_retryable", False))
                message = str(error)
                retryable = retryable or any(
                    marker in message.lower()
                    for marker in ("邮箱域名", "邮箱已存在", "email domain", "disposable email", "email_in_use")
                )
                if attempt < mailbox_attempts and retryable and not email:
                    log(f"Grok 邮箱不可用，切换新邮箱重试 {attempt + 1}/{mailbox_attempts}")
                    continue
                raise
        else:
            raise last_error if last_error else RuntimeError("Grok 注册失败")

        return Account(
            platform="grok",
            email=result["email"],
            password=result["password"],
            status=AccountStatus.REGISTERED,
            extra={
                "sso": result.get("sso", ""),
                "sso_rw": result.get("sso_rw", ""),
                "given_name": result.get("given_name", ""),
                "family_name": result.get("family_name", ""),
                "session_reason": result.get("session_reason", ""),
                "redirect_url": result.get("redirect_url", ""),
            },
        )

    def check_valid(self, account: Account) -> bool:
        return bool((account.extra or {}).get("sso"))

    def get_platform_actions(self) -> list:
        return [
            {"id": "upload_grok2api", "label": "导入 grok2api", "params": []},
        ]

    def execute_action(self, action_id: str, account: Account, params: dict) -> dict:
        if action_id == "upload_grok2api":
            from platforms.grok.grok2api_upload import upload_to_grok2api

            ok, msg = upload_to_grok2api(account)
            return {"ok": ok, "data": {"message": msg}}
        raise NotImplementedError(f"未知操作: {action_id}")
