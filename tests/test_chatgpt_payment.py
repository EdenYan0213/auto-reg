import unittest
from unittest import mock

from platforms.chatgpt.payment import (
    _base_payment_headers,
    _post_checkout_with_cf_retry,
)


class _Account:
    def __init__(self, access_token, cookies=""):
        self.access_token = access_token
        self.cookies = cookies


class PaymentHeaderTests(unittest.TestCase):
    def test_base_payment_headers_are_aligned_with_reference(self):
        account = _Account(
            "at-123", cookies="oai-did=device-1; cf_clearance=abc"
        )
        headers = _base_payment_headers(account)

        self.assertEqual(headers["Authorization"], "Bearer at-123")
        self.assertEqual(headers["oai-device-id"], "device-1")
        self.assertIn("auth0-client", headers)
        self.assertEqual(headers["priority"], "u=1, i")
        self.assertIn('"Chromium";v="136"', headers["sec-ch-ua"])
        self.assertIn("cf_clearance=abc", headers["cookie"])
        self.assertEqual(headers["origin"], "https://chatgpt.com")

    def test_base_payment_headers_omit_device_when_no_cookies(self):
        headers = _base_payment_headers(_Account("at-123", ""))
        self.assertNotIn("oai-device-id", headers)
        self.assertNotIn("cookie", headers)

    @mock.patch("platforms.chatgpt.payment.time.sleep")
    @mock.patch("platforms.chatgpt.payment._obtain_cf_clearance", return_value="abc")
    @mock.patch("platforms.chatgpt.payment.cffi_requests.post")
    def test_checkout_retries_once_on_cloudflare_challenge(self, mock_post, mock_cf, mock_sleep):
        challenge = mock.Mock(status_code=403)
        challenge.text = "<html>cf-browser-verification</html>"
        ok = mock.Mock(status_code=200)
        ok.json.return_value = {"checkout_session_id": "cs-1"}
        mock_post.side_effect = [challenge, ok]

        resp = _post_checkout_with_cf_retry(
            {"plan_name": "chatgptplusplan"},
            _Account("at-123", ""),
            None,
            label="Plus",
        )

        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("impersonate", mock_post.call_args_list[1][1])
        self.assertEqual(mock_post.call_args_list[1][1]["impersonate"], "chrome136")
        self.assertIn("cf_clearance=abc", mock_post.call_args_list[1][1]["headers"]["cookie"])


if __name__ == "__main__":
    unittest.main()
