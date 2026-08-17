"""Kiro 真实注册测试：cfworker 邮箱 + Playwright 全流程，验证当前域名在 AWS Builder ID 体系下能否过"""
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

# 1. 拿 cfworker 邮箱
mb = create_mailbox("cfworker", {
    "cfworker_api_url": env["CFWORKER_API_URL"],
    "cfworker_admin_token": env["CFWORKER_ADMIN_TOKEN"],
    "cfworker_domain": env["CFWORKER_DOMAIN"],
})
acct = mb.get_email()
print(f"[1] 邮箱: {acct.email}")
_before = mb.get_current_ids(acct)

# 2. 注册（headless=False 观察过程；无代理）
reg = KiroRegister(proxy=None, tag="KIRO", headless=False)

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

print("[2] 开始注册 ...")
ok, info = reg.register(
    email=acct.email,
    pwd="KiroTest@" + os.urandom(4).hex(),
    name="Kiro Test User",
    mail_token=None,
    otp_timeout=180,
    otp_callback=otp_cb,
)

print("\n=== 注册结果 ===")
print(f"ok: {ok}")
if not ok:
    print(f"error: {info.get('error')}")
    sys.exit(1)

print(f"email: {info.get('email')}")
for k in ["accessToken", "sessionToken", "refreshToken", "webAccessToken", "clientId", "clientSecret", "clientIdHash", "region"]:
    v = info.get(k, "")
    print(f"{k}: {str(v)[:40]}..." if v else f"{k}: (空)")

# 保存结果
with open("/tmp/kiro_test_account.json", "w") as f:
    json.dump(info, f, ensure_ascii=False, indent=2)
print("\n已保存 /tmp/kiro_test_account.json")
print("=== 结论 ===")
has_token = info.get("accessToken") or info.get("refreshToken") or info.get("webAccessToken")
if has_token:
    print("✅ Kiro 注册成功且拿到 token —— 同域名在 AWS 体系下可用！")
else:
    print("⚠️ 注册可能成功但没拿到 token，需人工检查")