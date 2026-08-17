"""用已注册的真实 Kiro 账号探测 KiroWebPortalService (smithy rpc-v2-cbor)。"""
import json
import sys

import cbor2
import requests

acc = json.load(open("/tmp/kiro_test_account.json"))
access_token = acc["accessToken"]
session_token = acc.get("sessionToken", "")

s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
})

# 1) 拿 UserId (cookie 会话)
r = s.get("https://app.kiro.dev/account/usage",
          cookies={"AccessToken": access_token, "SessionToken": session_token, "Idp": "BuilderId"})
print(f"[usage page] HTTP {r.status_code}")
user_id = s.cookies.get("UserId", "")
if not user_id:
    import re
    m = re.search(r'<meta name="user-id" content="([^"]+)"', r.text)
    user_id = m.group(1) if m else ""
print(f"[UserId] {user_id!r}")

if not user_id:
    print("[portal] 拿不到 UserId，跳过 operation 探测")
    sys.exit(0)

# 2) 探测 portal operation
def call(operation: str, body: dict) -> None:
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
            print(f"  -> {json.dumps(cbor2.loads(resp.content), default=str)[:600]}")
        except Exception as e:
            print(f"  -> cbor decode fail: {e}; raw={resp.content[:200]!r}")
    else:
        try:
            print(f"  -> {json.dumps(cbor2.loads(resp.content), default=str)[:300]}")
        except Exception:
            print(f"  -> raw={resp.text[:200]!r}")

call("GetUserInfo", {"origin": "KIRO_IDE",
                     "profileArn": "arn:aws:codewhisperer:us-east-1:699475941385:profile/EHGA3GRVQMUK"})
call("GetUserUsageAndLimits", {"origin": "KIRO_IDE", "isEmailRequired": True,
                               "profileArn": "arn:aws:codewhisperer:us-east-1:699475941385:profile/EHGA3GRVQMUK"})