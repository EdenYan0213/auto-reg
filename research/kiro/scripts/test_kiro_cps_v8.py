"""Kiro CPS v8：确认 CreateApiKey 参数格式 + 订阅状态
1. expiresAt 用 epoch 秒
2. SOCIAL profileArn 试 CreateApiKey（看是否不同订阅路径）
3. GetUserUsageAndLimits 全量（找 subscription 字段）
"""
import json
import cbor2
import requests

acc = json.load(open("/tmp/kiro_test_account.json"))
access_token = acc["accessToken"]
idp = acc.get("idp", "BuilderId")
user_id = "d-9067642ac7.8448a4e8-2001-70fb-b375-fb3a45154fd8"
region = acc.get("region", "us-east-1")
BUILDER_ARN = "arn:aws:codewhisperer:us-east-1:638616132270:profile/AAAACCCCXXXX"
SOCIAL_ARN = "arn:aws:codewhisperer:us-east-1:699475941385:profile/EHGA3GRVQMUK"

ENDPOINT = "https://management.us-east-1.kiro.dev"
SERVICE = "KiroControlPlaneBearerService"

s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/146.0.0.0"})
COOKIES = {"AccessToken": access_token, "SessionToken": acc.get("sessionToken", ""),
           "Idp": idp, "UserId": user_id}


def call_cps(op: str, body: dict):
    h = {
        "Accept": "application/cbor",
        "Content-Type": "application/cbor",
        "smithy-protocol": "rpc-v2-cbor",
        "x-amz-user-agent": "aws-sdk-js/1.0.0 ua/2.1 os/macOS lang/js md/browser#Google-Chrome_146 m/N,M,E",
        "Authorization": f"Bearer {access_token}",
        "kiro-client-id": acc.get("clientId", ""),
        "x-kiro-session-token": acc.get("sessionToken", ""),
        "x-kiro-user-id": user_id,
        "x-kiro-idp": idp,
        "x-kiro-region": region,
        "Origin": "https://app.kiro.dev",
        "Referer": "https://app.kiro.dev/",
    }
    resp = s.post(f"{ENDPOINT}/service/{SERVICE}/operation/{op}",
                  headers=h, cookies=COOKIES,
                  data=cbor2.dumps(body), timeout=25)
    try:
        out = json.dumps(cbor2.loads(resp.content), default=str)
    except Exception:
        out = resp.text[:250]
    print(f"[{resp.status_code}] {op} {json.dumps(body)[:120]}")
    print(f"    {out[:400]}")
    print()
    return resp


# 1. expiresAt epoch 秒
call_cps("CreateApiKey", {"profileArn": BUILDER_ARN, "label": "auto-reg-test",
                          "expiresAt": 1784160000})
# 2. SOCIAL profileArn 试 CreateApiKey
call_cps("CreateApiKey", {"profileArn": SOCIAL_ARN, "label": "auto-reg-test"})
# 3. ListApiKeys 用 SOCIAL（对照）
call_cps("ListApiKeys", {"profileArn": SOCIAL_ARN})