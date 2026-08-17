"""KiroControlPlaneBearerService 探测 v2：加 smithy-protocol header + unit 输入不带 body。
基于 vendor.js: 路径=/service/{service}/operation/{op}; smithy-protocol: rpc-v2-cbor;
输入为 unit 时删除 body 和 content-type。
"""
import json, uuid
import urllib.request, urllib.error

data = json.load(open("/tmp/kiro_test_account.json"))
access_token = data["accessToken"]
session_token = data["sessionToken"]
idp = data.get("idp", "BuilderId")
user_id = "d-9067642ac7.8448a4e8-2001-70fb-b375-fb3a45154fd8"
region = data.get("region", "us-east-1")

CANDIDATE_ENDPOINTS = [
    "https://management.us-east-1.kiro.dev",
    "https://app.kiro.dev",
    "https://codewhisperer.us-east-1.amazonaws.com",
]

# service name 变体
SERVICES = [
    "KiroControlPlaneBearerService",
    "KiroWebPortalService",
]

OPS = [
    ("GetProfile", None),            # unit 输入 -> 无 body
    ("ListAvailableProfiles", None), # unit 输入
    ("ListApiKeys", None),
    ("GetApiKeys", None),
]


def call(endpoint, service, op, body, is_unit):
    url = f"{endpoint}/service/{service}/operation/{op}"
    req = urllib.request.Request(url, method="POST")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("accept", "application/cbor")
    req.add_header("smithy-protocol", "rpc-v2-cbor")
    req.add_header("amz-sdk-invocation-id", str(uuid.uuid4()))
    req.add_header("amz-sdk-request", "attempt=1; max=4")
    req.add_header("x-amz-user-agent", "aws-sdk-js/3.758.0 ua/2.1")
    req.add_header("user-agent", "KiroWebPortal/0.1")
    req.add_header("kiro-client-id", data.get("clientId", ""))
    req.add_header("x-kiro-session-token", session_token)
    req.add_header("x-kiro-user-id", user_id)
    req.add_header("x-kiro-idp", idp)
    req.add_header("x-kiro-region", region)
    payload = b""
    if not is_unit:
        req.add_header("content-type", "application/cbor")
        try:
            import cbor2
            payload = cbor2.dumps(body or {})
        except ImportError:
            payload = b""
    try:
        resp = urllib.request.urlopen(req, payload, timeout=20)
        raw = resp.read()
        try:
            import cbor2
            dec = cbor2.loads(raw)
            return resp.status, "cbor", json.dumps(dec, default=str)[:400]
        except Exception:
            return resp.status, resp.headers.get("Content-Type", "?"), raw[:200].decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, e.headers.get("Content-Type", "?"), raw[:200].decode("utf-8", "replace")
    except Exception as e:
        return 0, "err", str(e)[:200]


for ep in CANDIDATE_ENDPOINTS:
    for svc in SERVICES:
        for op, body in OPS:
            is_unit = body is None
            code, ct, out = call(ep, svc, op, body, is_unit)
            tag = f"{ep} | {svc} | {op}"
            if code == 200 or "cbor" in ct:
                print(f"*** {tag} -> {code} [{ct}]")
                print(f"    {out}")
            else:
                print(f"    {tag} -> {code} [{ct}] {out[:80]}")
    print()