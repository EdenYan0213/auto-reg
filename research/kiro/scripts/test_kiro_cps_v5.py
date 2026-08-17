"""KiroControlPlaneBearerService 探测 v5：换 token 变体。
CPS client 的 token 来自 tokenRefreshFunction，可能不是 accessToken。
候选：accessToken / webAccessToken / sessionToken / refreshToken
"""
import json
import cbor2
import requests

acc = json.load(open("/tmp/kiro_test_account.json"))
idp = acc.get("idp", "BuilderId")
user_id = "d-9067642ac7.8448a4e8-2001-70fb-b375-fb3a45154fd8"
region = acc.get("region", "us-east-1")
PROFILE_ARN = "arn:aws:codewhisperer:us-east-1:699475941385:profile/EHGA3GRVQMUK"

ENDPOINT = "https://management.us-east-1.kiro.dev"
SERVICE = "KiroControlPlaneBearerService"

TOKENS = {
    "accessToken": acc["accessToken"],
    "webAccessToken": acc.get("webAccessToken", ""),
    "sessionToken": acc.get("sessionToken", ""),
    "refreshToken": acc.get("refreshToken", ""),
}

s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/146.0.0.0"})


def call(token_name: str, op: str, body: dict, header_name="Authorization", header_tpl=None):
    h = {
        "Accept": "application/cbor",
        "Content-Type": "application/cbor",
        "smithy-protocol": "rpc-v2-cbor",
        "x-amz-user-agent": "aws-sdk-js/1.0.0 ua/2.1 os/macOS lang/js md/browser#Google-Chrome_146 m/N,M,E",
        "kiro-client-id": acc.get("clientId", ""),
        "x-kiro-session-token": acc.get("sessionToken", ""),
        "x-kiro-user-id": user_id,
        "x-kiro-idp": idp,
        "x-kiro-region": region,
        "Origin": "https://app.kiro.dev",
        "Referer": "https://app.kiro.dev/",
    }
    if header_tpl:
        h[header_name] = header_tpl
    else:
        h[header_name] = f"Bearer {TOKENS[token_name]}"
    resp = s.post(f"{ENDPOINT}/service/{SERVICE}/operation/{op}",
                  headers=h, data=cbor2.dumps(body), timeout=20)
    try:
        out = json.dumps(cbor2.loads(resp.content), default=str)
    except Exception:
        out = resp.text[:200]
    print(f"[{resp.status_code}] {op} token={token_name} hdr={header_name} -> {out[:250]}")
    return resp


# GetProfile 用不同 token 作为 Authorization Bearer
for tn in ["accessToken", "webAccessToken", "sessionToken", "refreshToken"]:
    if TOKENS[tn]:
        call(tn, "GetProfile", {"profileArn": PROFILE_ARN})
print()
# 尝试不同 header 名（smithy bearer 中间件可能用 x-amz-security-token 或 cookie）
call("webAccessToken", "GetProfile", {"profileArn": PROFILE_ARN}, header_name="x-amz-security-token")
call("accessToken", "GetProfile", {"profileArn": PROFILE_ARN}, header_name="x-amz-security-token")
call("accessToken", "GetProfile", {"profileArn": PROFILE_ARN}, header_name="x-kiro-access-token")