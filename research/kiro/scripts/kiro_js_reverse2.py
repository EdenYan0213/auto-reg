"""深度搜索 Kiro 前端 bundle: smithy service 路径 + apiKey + OpenAI 兼容端点。"""
import json
import re

import requests

acc = json.load(open("/tmp/kiro_test_account.json"))
access_token = acc["accessToken"]
session_token = acc.get("sessionToken", "")

s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"})
cookies = {"AccessToken": access_token, "SessionToken": session_token, "Idp": "BuilderId"}

for name in ["vendor", "main"]:
    url = f"https://assets.app.kiro.dev/releases/8177d4667d4be46a/{name}.js"
    jr = s.get(url, cookies=cookies, timeout=60)
    print(f"[{name}.js] HTTP {jr.status_code}, {len(jr.content)/1024:.0f} KB")
    if jr.status_code != 200:
        continue
    text = jr.content.decode("utf-8", "ignore")
    open(f"/tmp/kiro_{name}.js", "w").write(text)

    # 1) smithy service 路径
    services = set(re.findall(r'["\']/service/([A-Za-z0-9]+)/operation/([A-Za-z0-9]+)["\']', text))
    print(f"  [services x operations] {len(services)}")
    for svc, op in sorted(services)[:60]:
        print(f"    {svc} -> {op}")

    # 2) operation/ 单独出现
    ops = set(re.findall(r'["\']operation/([A-Za-z0-9]+)["\']', text))
    print(f"  [operation/ tokens] {len(ops)}: {sorted(ops)[:60]}")

    # 3) apiKey 相关
    apikey_hits = re.findall(r'.{60}[Aa]pi[Kk]ey.{60}', text)
    print(f"  [apiKey context] {len(apikey_hits)}")
    for h in apikey_hits[:8]:
        print(f"    ...{h}...")

    # 4) OpenAI 兼容端点
    for pat in [r'chat/completions', r'/v1/models', r'api\.openai\.com', r'anthropic', r'bedrock', r'conversation']:
        hits = re.findall(r'.{50}' + pat + r'.{50}', text)
        print(f"  [{pat}] {len(hits)} hits")
        for h in hits[:3]:
            print(f"    ...{h}...")
    print()

print("[done]")