"""KiroControlPlaneBearerService 探测 v7：CreateApiKey
用 BUILDER_ID profileArn。CreateApiKeyRequest = {profileArn, label, expiresAt?}
"""
import json
import cbor2
import requests

acc = json.load(open("/tmp/kiro_test_account.json"))
access_token = acc["accessToken"]
idp = acc.get("idp", "BuilderId")
user_id = "d-9067642ac7.8448a4e8-2001-70fb-b375-fb3a45154fd8"
region = acc.get("region", "us-east-1")
PROFILE_ARN = "arn:aws:codewhisperer:us-east-1:638616132270:profile/AAAACCCCXXXX"

ENDPOINT = "https://management.us-east-1.kiro.dev"
SERVICE = "KiroControlPlaneBearerService"

s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/146.0.0.0"})
COOKIES = {"AccessToken": access_token, "SessionToken": acc.get("sessionToken", ""),
           "Idp": idp, "UserId": user_id}


def call(op: str, body: dict):
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
    print(f"[{resp.status_code}] {op} body={json.dumps(body)}")
    print(f"    {out[:500]}")
    print()
    return resp


# 尝试 1: 仅 profileArn + label
call("CreateApiKey", {"profileArn": PROFILE_ARN, "label": "auto-reg-test"})
# 尝试 2: 带 expiresAt（ISO 字符串）
call("CreateApiKey", {"profileArn": PROFILE_ARN, "label": "auto-reg-test",
                      "expiresAt": "2027-08-16T00:00:00Z"})
# 尝试 3: 无 label
call("CreateApiKey", {"profileArn": PROFILE_ARN})