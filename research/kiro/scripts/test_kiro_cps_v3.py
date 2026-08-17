"""KiroControlPlaneBearerService 探测 v3：
对照 portal 成功案例 (requests + cookies + Origin/Referer)，management endpoint 400 是请求格式问题。
400 ValidationException "Improperly formed request" —— 可能因为：
1. 需要 session cookies (AccessToken/SessionToken/Idp/UserId)
2. unit 输入其实需要空 CBOR map {} (Content-Type 保留)
"""
import json, sys
import cbor2
import requests

acc = json.load(open("/tmp/kiro_test_account.json"))
access_token = acc["accessToken"]
session_token = acc.get("sessionToken", "")
idp = acc.get("idp", "BuilderId")
user_id = "d-9067642ac7.8448a4e8-2001-70fb-b375-fb3a45154fd8"
region = acc.get("region", "us-east-1")

ENDPOINT = "https://management.us-east-1.kiro.dev"
SERVICE = "KiroControlPlaneBearerService"

s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
})

COOKIES = {"AccessToken": access_token, "SessionToken": session_token,
           "Idp": idp, "UserId": user_id}


def call(op: str, body=None, with_cookies=True, with_origin=True, unit_no_body=False):
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
    }
    if with_origin:
        h["Origin"] = "https://app.kiro.dev"
        h["Referer"] = "https://app.kiro.dev/"
    payload = b""
    if not unit_no_body:
        payload = cbor2.dumps(body if body is not None else {})
    resp = s.post(f"{ENDPOINT}/service/{SERVICE}/operation/{op}",
                  headers=h,
                  cookies=COOKIES if with_cookies else None,
                  data=payload, timeout=20)
    tag = f"{op} body={body} cookies={with_cookies} origin={with_origin} unit_no_body={unit_no_body}"
    try:
        out = json.dumps(cbor2.loads(resp.content), default=str)[:400]
    except Exception:
        out = resp.text[:200]
    print(f"[{resp.status_code}] {tag}")
    print(f"    {out}")
    return resp


# 1) 带完整 cookies + origin 的 unit 请求（带空 body map）
call("GetProfile", body={}, with_cookies=True, with_origin=True)
# 2) 无 body（unit 真正语义）
call("GetProfile", body=None, with_cookies=True, with_origin=True, unit_no_body=True)
# 3) 无 cookies 但带 origin
call("GetProfile", body={}, with_cookies=False, with_origin=True)
# 4) ListApiKeys 变体
call("ListApiKeys", body={}, with_cookies=True, with_origin=True)