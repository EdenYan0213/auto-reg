"""探测 KiroWebPortalService 的 API Key / 会话相关 operation。"""
import json
import sys

import cbor2
import requests

acc = json.load(open("/tmp/kiro_test_account.json"))
access_token = acc["accessToken"]
session_token = acc.get("sessionToken", "")

s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"})

r = s.get("https://app.kiro.dev/account/usage",
          cookies={"AccessToken": access_token, "SessionToken": session_token, "Idp": "BuilderId"})
user_id = s.cookies.get("UserId", "")
if not user_id:
    import re
    m = re.search(r'<meta name="user-id" content="([^"]+)"', r.text)
    user_id = m.group(1) if m else ""
print(f"[UserId] {user_id!r}")

PROFILE_ARN = "arn:aws:codewhisperer:us-east-1:699475941385:profile/EHGA3GRVQMUK"

def call(operation: str, body: dict) -> dict:
    resp = s.post(
        f"https://app.kiro.dev/service/KiroWebPortalService/operation/{operation}",
        headers={
            "Accept": "application/cbor",
            "Content-Type": "application/cbor",
            "smithy-protocol": "rpc-v2-cbor",
            "Origin": "https://app.kiro.dev",
            "Referer": "https://app.kiro.dev/account/usage",
            "x-amz-user-agent": "aws-sdk-js/1.0.0 ua/2.1 os/macOS lang/js md/browser#Google-Chrome_146 m/N,M,E",
            "Authorization": f"Bearer {access_token}",
        },
        cookies={"AccessToken": access_token, "SessionToken": session_token,
                 "Idp": "BuilderId", "UserId": user_id},
        data=cbor2.dumps(body),
        timeout=20,
    )
    print(f"[{operation}] HTTP {resp.status_code}")
    if resp.status_code == 200:
        try:
            return cbor2.loads(resp.content)
        except Exception as e:
            print(f"  -> cbor decode fail: {e}; raw={resp.content[:300]!r}")
            return {}
    try:
        print(f"  -> {json.dumps(cbor2.loads(resp.content), default=str)[:300]}")
    except Exception:
        print(f"  -> raw={resp.text[:200]!r}")
    return {}

# 1) API Key 相关 operation 探测（基于 enableApiKeys flag）
for op in ["GetApiKeys", "ListApiKeys", "GetUserApiKeys", "CreateApiKey",
           "GetAvailableApiKeyFeatures", "GetApiKeyFeatures"]:
    r = call(op, {"origin": "KIRO_IDE", "profileArn": PROFILE_ARN})
    if r:
        print(f"  !! {op} 返回数据: {json.dumps(r, default=str)[:800]}")

# 2) 会话/注册状态
call("GetUserSessionInfo", {"origin": "KIRO_IDE", "profileArn": PROFILE_ARN})

# 3) 检查 enableMidwayObo / enableExternalOidcLogin 对应的 operation 猜测
for op in ["GetOboConfig", "GetExternalOidcConfig"]:
    r = call(op, {"origin": "KIRO_IDE", "profileArn": PROFILE_ARN})
    if r:
        print(f"  !! {op} 返回数据: {json.dumps(r, default=str)[:400]}")