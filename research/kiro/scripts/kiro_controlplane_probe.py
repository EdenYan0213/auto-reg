"""探测 KiroControlPlaneBearerService (smithy rpc-v2-cbor) - ApiKeys 服务。"""
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

BASES = [
    "https://app.kiro.dev",
    "https://codewhisperer.us-east-1.amazonaws.com",
]

def call(base: str, operation: str, body: dict) -> None:
    url = f"{base}/service/KiroControlPlaneBearerService/operation/{operation}"
    resp = s.post(
        url,
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
    print(f"[{base.split('//')[1]} /{operation}] HTTP {resp.status_code}")
    if resp.status_code == 200:
        try:
            data = cbor2.loads(resp.content)
            print(f"  !! 成功: {json.dumps(data, default=str)[:700]}")
        except Exception as e:
            print(f"  -> cbor decode fail: {e}; raw={resp.content[:300]!r}")
    else:
        try:
            print(f"  -> {json.dumps(cbor2.loads(resp.content), default=str)[:250]}")
        except Exception:
            print(f"  -> raw={resp.text[:200]!r}")

for base in BASES:
    call(base, "GetProfile", {"origin": "KIRO_IDE", "profileArn": PROFILE_ARN})
    call(base, "ListAvailableProfiles", {"origin": "KIRO_IDE"})
    call(base, "ListApiKeys", {"origin": "KIRO_IDE", "profileArn": PROFILE_ARN})