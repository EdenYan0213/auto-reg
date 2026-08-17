"""KiroControlPlaneBearerService 探测 v4：传 profileArn 参数。
profileArn: arn:aws:codewhisperer:us-east-1:699475941385:profile/EHGA3GRVQMUK
"""
import json
import cbor2
import requests

acc = json.load(open("/tmp/kiro_test_account.json"))
access_token = acc["accessToken"]
session_token = acc.get("sessionToken", "")
idp = acc.get("idp", "BuilderId")
user_id = "d-9067642ac7.8448a4e8-2001-70fb-b375-fb3a45154fd8"
region = acc.get("region", "us-east-1")
PROFILE_ARN = "arn:aws:codewhisperer:us-east-1:699475941385:profile/EHGA3GRVQMUK"

ENDPOINT = "https://management.us-east-1.kiro.dev"
SERVICE = "KiroControlPlaneBearerService"

s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
})
COOKIES = {"AccessToken": access_token, "SessionToken": session_token,
           "Idp": idp, "UserId": user_id}


def call(op: str, body: dict):
    h = {
        "Accept": "application/cbor",
        "Content-Type": "application/cbor",
        "smithy-protocol": "rpc-v2-cbor",
        "x-amz-user-agent": "aws-sdk-js/1.0.0 ua/2.1 os/macOS lang/js md/browser#Google-Chrome_146 m/N,M,E",
        "Authorization": f"Bearer {access_token}",
        "kiro-client-id": acc.get("clientId", ""),
        "x-kiro-session-token": session_token,
        "x-kiro-user-id": user_id,
        "x-kiro-idp": idp,
        "x-kiro-region": region,
        "Origin": "https://app.kiro.dev",
        "Referer": "https://app.kiro.dev/",
    }
    resp = s.post(f"{ENDPOINT}/service/{SERVICE}/operation/{op}",
                  headers=h, cookies=COOKIES,
                  data=cbor2.dumps(body), timeout=20)
    try:
        out = json.dumps(cbor2.loads(resp.content), default=str)
    except Exception:
        out = resp.text[:300]
    print(f"[{resp.status_code}] {op}")
    print(f"    {out[:600]}")
    print()
    return resp


# 1) GetProfile 带 profileArn
call("GetProfile", {"profileArn": PROFILE_ARN})
# 2) ListApiKeys 带 profileArn
call("ListApiKeys", {"profileArn": PROFILE_ARN})
# 3) ListAvailableProfiles（可能也需要 profileArn）
call("ListAvailableProfiles", {"profileArn": PROFILE_ARN})