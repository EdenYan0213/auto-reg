"""下载 SettingsApiKeysPage.js + 相关 chunk，提取 ApiKeys service 的真实调用。"""
import json
import re

import requests

acc = json.load(open("/tmp/kiro_test_account.json"))
access_token = acc["accessToken"]
session_token = acc.get("sessionToken", "")

s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"})
cookies = {"AccessToken": access_token, "SessionToken": session_token, "Idp": "BuilderId"}

BASE = "https://assets.app.kiro.dev/releases/8177d4667d4be46a"

# 1) SettingsApiKeysPage.js
for chunk in ["SettingsApiKeysPage.js"]:
    url = f"{BASE}/{chunk}"
    r = s.get(url, cookies=cookies, timeout=30)
    print(f"[{chunk}] HTTP {r.status_code}, {len(r.content)/1024:.0f} KB")
    if r.status_code != 200:
        continue
    text = r.content.decode("utf-8", "ignore")
    open(f"/tmp/kiro_{chunk}", "w").write(text)

    # service 路径
    for m in sorted(set(re.findall(r'["\'](/service/[A-Za-z0-9]+/operation/[A-Za-z0-9]+)["\']', text))):
        print(f"  [path] {m}")
    for m in sorted(set(re.findall(r'["\']([A-Za-z0-9]+Service)["\']', text)))[:20]:
        print(f"  [svc] {m}")

    # operation 调用上下文
    for op in ["CreateApiKey", "ListApiKeys", "DeleteApiKey"]:
        for m in re.findall(r'.{80}' + op + r'.{80}', text):
            print(f"  [{op}] ...{m}...")
            print()

    # 引用的其他 chunk
    for m in re.findall(r'import\("\./([A-Za-z0-9_-]+\.js)"\)', text):
        print(f"  [import] {m}")

# 2) 从 vendor.js 找 ApiKeys service 的 endpoint 定义（Smithy client 配置）
vtext = open("/tmp/kiro_vendor.js").read()
# smithy client 常包含 endpointPrefix / serviceId
for m in re.findall(r'.{80}ApiKeys.{80}', vtext)[:20]:
    print(f"[vendor ApiKeys ctx] ...{m}...")
    print()