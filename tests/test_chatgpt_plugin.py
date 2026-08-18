import unittest
from unittest import mock

from core.base_mailbox import MailboxAccount
from core.base_platform import Account, RegisterConfig
from platforms.chatgpt.plugin import ChatGPTPlatform


class _BlankMailbox:
    def get_email(self):
        return MailboxAccount(email="", account_id="blank-mailbox")

    def wait_for_code(self, *args, **kwargs):
        return "123456"


class _TrackingMailbox:
    def __init__(self):
        self.account = MailboxAccount(email="demo@example.com", account_id="tracked-mailbox")
        self.wait_call = None
        self.current_ids_calls = []

    def get_email(self):
        return self.account

    def get_current_ids(self, account):
        self.current_ids_calls.append(account)
        return {"mid-1"}

    def wait_for_code(self, *args, **kwargs):
        self.wait_call = (args, kwargs)
        return "123456"


class _FakeAdapter:
    def run(self, context):
        context.email_service.create_email()
        raise AssertionError("create_email 应该先报错")


class _VerificationAdapter:
    def __init__(self):
        self.run_called = False

    def run(self, context):
        self.run_called = True
        context.email_service.create_email()
        code = context.email_service.get_verification_code(
            timeout=30,
            otp_sent_at=123.0,
            exclude_codes={"654321"},
        )
        self.last_code = code
        return mock.Mock(success=True)

    def build_account(self, result, fallback_password):
        return {"success": True, "password": fallback_password}


class _CheckoutAdapter:
    def run(self, _context):
        return mock.Mock(success=True)

    def build_account(self, _result, fallback_password):
        return Account(
            platform="chatgpt",
            email="demo@example.com",
            password=fallback_password,
            token="access-token",
            extra={"access_token": "access-token", "cookies": "session=secret"},
        )


class _FailureAdapter:
    def run(self, _context):
        return mock.Mock(
            success=False,
            email="failed@example.com",
            error_message="Sentinel 浏览器验证未完成",
        )


class ChatGPTPluginTests(unittest.TestCase):
    def test_custom_provider_rejects_blank_email(self):
        platform = ChatGPTPlatform(
            config=RegisterConfig(extra={"chatgpt_registration_mode": "refresh_token"}),
            mailbox=_BlankMailbox(),
        )

        with mock.patch(
            "platforms.chatgpt.plugin.build_chatgpt_registration_mode_adapter",
            return_value=_FakeAdapter(),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                platform.register()

        self.assertIn("custom_provider 返回空邮箱地址", str(ctx.exception))

    def test_custom_provider_uses_mailbox_baseline_for_verification_code(self):
        mailbox = _TrackingMailbox()
        platform = ChatGPTPlatform(
            config=RegisterConfig(extra={"chatgpt_registration_mode": "refresh_token"}),
            mailbox=mailbox,
        )
        adapter = _VerificationAdapter()

        with mock.patch(
            "platforms.chatgpt.plugin.build_chatgpt_registration_mode_adapter",
            return_value=adapter,
        ):
            result = platform.register()

        self.assertTrue(adapter.run_called)
        self.assertEqual(adapter.last_code, "123456")
        self.assertEqual(result["success"], True)
        self.assertEqual(mailbox.current_ids_calls, [mailbox.account])
        self.assertIsNotNone(mailbox.wait_call)
        _, kwargs = mailbox.wait_call
        self.assertEqual(kwargs.get("before_ids"), {"mid-1"})
        self.assertEqual(kwargs.get("otp_sent_at"), 123.0)
        self.assertEqual(kwargs.get("exclude_codes"), {"654321"})

    def test_failed_registration_keeps_generated_email_for_task_history(self):
        platform = ChatGPTPlatform(
            config=RegisterConfig(extra={"chatgpt_registration_mode": "refresh_token"}),
            mailbox=_TrackingMailbox(),
        )

        with mock.patch(
            "platforms.chatgpt.plugin.build_chatgpt_registration_mode_adapter",
            return_value=_FailureAdapter(),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                platform.register()

        self.assertIn("Sentinel 浏览器验证未完成", str(ctx.exception))
        self.assertEqual(platform.last_registration_email, "failed@example.com")

    def test_auto_checkout_link_is_saved_without_completing_payment(self):
        platform = ChatGPTPlatform(
            config=RegisterConfig(
                extra={
                    "chatgpt_auto_payment_link": True,
                    "chatgpt_payment_plan": "plus",
                    "chatgpt_payment_country": "SG",
                }
            ),
            mailbox=_TrackingMailbox(),
        )

        with mock.patch(
            "platforms.chatgpt.plugin.build_chatgpt_registration_mode_adapter",
            return_value=_CheckoutAdapter(),
        ), mock.patch(
            "platforms.chatgpt.payment.generate_plus_link",
            return_value="https://chatgpt.com/checkout/openai_llc/test-session",
        ) as generate_link:
            account = platform.register()

        self.assertEqual(
            account.extra["cashier_url"],
            "https://chatgpt.com/checkout/openai_llc/test-session",
        )
        self.assertEqual(account.extra["checkout_link_status"], "ready")
        generate_link.assert_called_once()


if __name__ == "__main__":
    unittest.main()
