"""Tavily 平台插件"""
import random, string
from core.base_platform import BasePlatform, Account, AccountStatus, RegisterConfig
from core.base_mailbox import BaseMailbox
from core.registry import register


@register
class TavilyPlatform(BasePlatform):
    name = "tavily"
    display_name = "Tavily"
    version = "1.0.0"
    supported_executors = ["protocol", "headless", "headed"]

    def __init__(self, config: RegisterConfig = None, mailbox: BaseMailbox = None):
        super().__init__(config)
        self.mailbox = mailbox

    def _register_browser(
        self,
        email: str,
        password: str,
        mail_acct,
        before_ids: set,
        otp_timeout: int,
    ) -> Account:
        if not self.mailbox or not mail_acct:
            raise RuntimeError("Tavily 注册需要可读取验证邮件的邮箱服务")

        from platforms.tavily.browser import TavilyBrowserRegister

        # 和 Grok 一样，executor_type 只决定浏览器是否无头；protocol 也使用
        # 同一真实浏览器会话，避免 Turnstile token 与 Auth0 注册会话分离。
        headless = (self.config.executor_type or "") == "headless"
        result = TavilyBrowserRegister(
            mailbox=self.mailbox,
            proxy=self.config.proxy,
            log_fn=getattr(self, "_log_fn", print),
            headless=headless,
        ).register(
            email=email,
            password=password,
            before_ids=before_ids,
            otp_timeout=otp_timeout,
        )
        return Account(
            platform="tavily",
            email=result["email"],
            password=result["password"],
            status=AccountStatus.REGISTERED,
            extra={"api_key": result["api_key"]},
        )

    def register(self, email: str, password: str = None) -> Account:
        if not password:
            password = "".join(random.choices(string.ascii_letters + string.digits + "!@#", k=14))
        log = getattr(self, '_log_fn', print)

        mail_acct = self.mailbox.get_email() if self.mailbox else None
        email = email or (mail_acct.email if mail_acct else None)
        if not email:
            raise RuntimeError("邮箱服务未返回邮箱地址")
        before_ids = self.mailbox.get_current_ids(mail_acct) if mail_acct else set()
        otp_timeout = self.get_mailbox_otp_timeout()

        registration_mode = str(
            (self.config.extra or {}).get("tavily_registration_mode") or "browser"
        ).strip().lower()
        if registration_mode in {"browser", "playwright", "headed", "headless"}:
            log(f"使用浏览器模式注册: {email}")
            return self._register_browser(
                email, password, mail_acct, before_ids, otp_timeout
            )

        log(f"邮箱: {email}")

        def otp_cb():
            log("等待验证码邮件...")
            code = self.mailbox.wait_for_code(
                mail_acct,
                keyword="",
                timeout=otp_timeout,
                before_ids=before_ids,
            )
            if code: log(f"验证码: {code}")
            return code

        captcha = self._make_captcha(key=self.config.extra.get("yescaptcha_key", ""))

        from platforms.tavily.core import TavilyRegister
        with self._make_executor() as ex:
            reg = TavilyRegister(executor=ex, captcha=captcha, log_fn=log)
            result = reg.register(email=email, password=password,
                                  otp_callback=otp_cb if self.mailbox else None)

        return Account(platform="tavily", email=result["email"], password=result["password"],
                       status=AccountStatus.REGISTERED, extra={"api_key": result["api_key"]})

    def check_valid(self, account: Account) -> bool:
        api_key = account.extra.get("api_key", "")
        if not api_key:
            return False
        import requests
        try:
            r = requests.post("https://api.tavily.com/search",
                              json={"api_key": api_key, "query": "test", "max_results": 1},
                              timeout=10)
            return r.status_code != 401
        except Exception:
            return False
