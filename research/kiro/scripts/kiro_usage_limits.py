"""Kiro portal: GetUserUsageAndLimits 全量 + 找 OpenAI 兼容端点线索"""
import json
import cbor2
import requests

acc = json.load(open("/tmp/kiro_test_account.json"))
access_token = acc["accessToken"]
idp = acc.get("idp", "BuilderId")
user_id = "d-9067642ac7.8448a4e8-2001-70fb-b375-fb3a45154fd8"

s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/146.0.0.0"})
COOKIES = {"AccessToken": access_token, "SessionToken": acc.get("sessionToken", ""),
           "Idp": idp, "UserId": user_id}


def portal_call(op: str, body: dict):
    h = {
        "Accept": "application/cbor",
        "Content-Type": "application/cbor",
        "smithy-protocol": "rpc-v2-cbor",
        "x-amz-user-agent": "aws-sdk-js/1.0.0 ua/2.1 os/macOS lang/js md/browser#Google-Chrome_146 m/N,M,E",
        "Authorization": f"Bearer {access_token}",
        "Origin": "https://app.kiro.dev",
        "Referer": "https://app.kiro.dev/",
    }
    resp = s.post(f"https://app.kiro.dev/service/KiroWebPortalService/operation/{op}",
                  headers=h, cookies=COOKIES, data=cbor2.dumps(body), timeout=25)
    try:
        return resp.status_code, cbor2.loads(resp.content)
    except Exception:
        return resp.status_code, resp.text[:300]


code, data = portal_call("GetUserUsageAndLimits", {"origin": "KIRO_IDE"})
print(f"[GetUserUsageAndLimits] HTTP {code}")
print(json.dumps(data, indent=2, default=str)[:4000])