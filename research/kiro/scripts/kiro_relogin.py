"""用已有 Kiro 账号重新走桌面 IDC 流程，拿最新 refreshToken 并更新 /tmp/kiro_test_account.json。"""
import os
import sys
import json

sys.path.insert(0, "/Users/chuang.yan/PycharmProjects/auto_reg")

env = {}
for line in open("/Users/chuang.yan/PycharmProjects/auto_reg/.env"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k] = v
os.environ.update(env)

from core.base_mailbox import create_mailbox
from platforms.kiro.core import KiroRegister

old = json.load(open("/tmp/kiro_test_account.json"))
email = old["email"]
pwd = old["password"]
print(f"[0] 重新登录账号: {email}")

# cfworker 邮箱收 OTP
mb = create_mailbox("cfworker", {
    "cfworker_api_url": env["CFWORKER_API_URL"],
    "cfworker_admin_token": env["CFWORKER_ADMIN_TOKEN"],
    "cfworker_domain": env["CFWORKER_DOMAIN"],
})
acct = mb.get_email()
_before = mb.get_current_ids(acct)

def otp_cb():
    print("[*] 等待 AWS Builder ID 验证码邮件 ...")
    code = mb.wait_for_code(
        acct,
        keyword="builder id",
        timeout=180,
        before_ids=_before,
        code_pattern=r'(?is)(?:verification\s+code|验证码)[^0-9]{0,20}(\d{6})',
    )
    print(f"[*] 验证码: {code}")
    return code

reg = KiroRegister(proxy=None, tag="KIRO", headless=False)
print("[1] 执行桌面 IDC 流程 ...")
ok, tokens = reg.fetch_desktop_tokens(email=email, pwd=pwd, otp_callback=otp_cb)

print("\n=== 结果 ===")
if not ok:
    print(f"FAILED: {tokens.get('error')}")
    sys.exit(1)

for k in ["accessToken", "refreshToken", "clientId", "clientSecret", "clientIdHash", "region"]:
    v = tokens.get(k, "")
    print(f"{k}: {str(v)[:50]}..." if v else f"{k}: (空)")

# 合并到旧数据，保留 email/password/sessionToken/webAccessToken/profileArn 等
new = dict(old)
new.update({k: tokens.get(k, "") for k in ["accessToken", "refreshToken", "clientId", "clientSecret", "clientIdHash", "region"]})
with open("/tmp/kiro_test_account.json", "w") as f:
    json.dump(new, f, ensure_ascii=False, indent=2)
print("\n已更新 /tmp/kiro_test_account.json")
print("refreshToken 前30:", new.get("refreshToken", "")[:30])