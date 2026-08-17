"""从 app.kiro.dev 前端 JS bundle 提取 smithy service 路径和 operation 名。"""
import json
import re
import sys

import requests

acc = json.load(open("/tmp/kiro_test_account.json"))
access_token = acc["accessToken"]
session_token = acc.get("sessionToken", "")

s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"})
cookies = {"AccessToken": access_token, "SessionToken": session_token, "Idp": "BuilderId"}

r = s.get("https://app.kiro.dev/", cookies=cookies, timeout=20)
print(f"[index] HTTP {r.status_code}, len={len(r.text)}")

# 提取所有 JS bundle URL
js_urls = re.findall(r'(?:src|href)="([^"]+\.js[^"]*)"', r.text)
# 也找 esbuild/webpack chunk 模式
js_urls += re.findall(r'"([^"]*\/assets\/[^"]+\.js)"', r.text)
js_urls = list(dict.fromkeys(js_urls))  # dedupe preserve order
print(f"[js bundles] {len(js_urls)}")
for u in js_urls[:40]:
    print(f"  {u}")

# 下载并搜索 service/operation
seen = set()
for u in js_urls[:60]:
    url = u if u.startswith("http") else "https://app.kiro.dev" + u
    if url in seen:
        continue
    seen.add(url)
    try:
        jr = s.get(url, cookies=cookies, timeout=30)
        if jr.status_code != 200 or len(jr.content) < 100:
            continue
        text = jr.content.decode("utf-8", "ignore")
        # smithy service path 模式: /service/XXXService/operation/YYY
        for m in re.findall(r'["\']/service/([A-Za-z0-9]+)/operation/([A-Za-z0-9]+)["\']', text):
            print(f"  [SERVICE] {m[0]} -> {m[1]}")
        # operation 名单独出现
        for m in re.findall(r'["\']operation/([A-Za-z0-9]+)["\']', text):
            pass  # 已覆盖
    except Exception as e:
        pass

print("[done]")