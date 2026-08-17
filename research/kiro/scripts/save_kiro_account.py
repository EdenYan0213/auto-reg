"""把 /tmp/kiro_test_account.json 的真实 Kiro 注册账号保存到 auto_reg 数据库 (account_manager.db)。"""
import json
import sys

sys.path.insert(0, "/Users/chuang.yan/PycharmProjects/auto_reg")

from core.base_platform import Account, AccountStatus
from core.db import save_account

data = json.load(open("/tmp/kiro_test_account.json"))

account = Account(
    platform="kiro",
    email=data["email"],
    password=data["password"],
    user_id=data.get("userId", ""),
    region=data.get("region", "us-east-1"),
    token=data.get("accessToken", ""),
    status=AccountStatus.REGISTERED,
    extra={
        "name": data.get("name", ""),
        "accessToken": data.get("accessToken", ""),
        "sessionToken": data.get("sessionToken", ""),
        "clientId": data.get("clientId", ""),
        "clientSecret": data.get("clientSecret", ""),
        "clientIdHash": data.get("clientIdHash", ""),
        "refreshToken": data.get("refreshToken", ""),
        "webAccessToken": data.get("webAccessToken", ""),
        "region": data.get("region", "us-east-1"),
        "provider": "BuilderId",
        "authMethod": "IdC",
        # 探测补充
        "portalUserId": "d-9067642ac7.8448a4e8-2001-70fb-b375-fb3a45154fd8",
        "profileArn": "arn:aws:codewhisperer:us-east-1:699475941385:profile/EHGA3GRVQMUK",
        "subscription": "KIRO FREE (Q_DEVELOPER_STANDALONE_FREE)",
        "apiKeysFeature": "enableApiKeys (opt-in, 需在 SettingsApiKeysPage 开启)",
        "testedAt": "2026-08-16",
    },
)

saved = save_account(account)
print(f"[saved] id={saved.id} platform={saved.platform} email={saved.email}")
print(f"  user_id={saved.user_id!r} region={saved.region!r} status={saved.status!r}")
print(f"  token 前 40: {saved.token[:40]!r}...")
print(f"  extra keys: {list(saved.get_extra().keys())}")