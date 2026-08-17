import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.base_mailbox import BaseMailbox, MailboxAccount
from core.base_platform import RegisterConfig
from platforms.grok.plugin import GrokPlatform


class _Mailbox(BaseMailbox):
    def get_email(self):
        return MailboxAccount(email="protocol@example.com")

    def get_current_ids(self, account):
        return set()

    def wait_for_code(self, account, **kwargs):
        return "123-456"


class _ProtocolClient:
    instances = []

    def __init__(self, config, *, proxy, log):
        self.config = config
        self.proxy = proxy
        self.log = log
        self.browser_session_id = "xai-test-session"
        self.calls = []
        self.instances.append(self)

    def bootstrap(self):
        self.calls.append("bootstrap")
        return SimpleNamespace(sitekey="0x4AAAAAAAhr9JGVDZbrZOo0")

    def send_email_validation_code(self, email):
        self.calls.append(("send", email))

    def verify_email_validation_code(self, email, code):
        self.calls.append(("verify", email, code))

    def validate_password(self, email, password):
        self.calls.append(("password", email, password))

    def solve_turnstile(self):
        self.calls.append("turnstile")
        return ""

    def create_user_and_session(self, **kwargs):
        self.calls.append(("create", kwargs["email"], kwargs["turnstile_token"]))
        return {"sso": "sso-value", "sso_rw": "sso-rw-value", "session_reason": "browser"}

    def close(self):
        self.calls.append("close")


class GrokProtocolRegistrationTests(unittest.TestCase):
    def tearDown(self):
        _ProtocolClient.instances.clear()

    def test_protocol_flow_returns_account_for_task_persistence(self):
        platform = GrokPlatform(
            RegisterConfig(captcha_solver="local_solver"),
            mailbox=_Mailbox(),
        )
        with patch("platforms.grok.protocol.GrokProtocolClient", _ProtocolClient):
            account = platform.register(None)

        self.assertEqual(account.platform, "grok")
        self.assertEqual(account.email, "protocol@example.com")
        self.assertEqual(account.extra["sso"], "sso-value")
        self.assertEqual(
            [call for call in _ProtocolClient.instances[0].calls if isinstance(call, str)],
            ["bootstrap", "turnstile", "close"],
        )
        self.assertIn(("verify", "protocol@example.com", "123456"), _ProtocolClient.instances[0].calls)
        self.assertIn(("create", "protocol@example.com", ""), _ProtocolClient.instances[0].calls)


if __name__ == "__main__":
    unittest.main()
