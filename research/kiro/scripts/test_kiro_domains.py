"""探测 Kiro 可能的 API 转接域名 + 前端 API Keys 页面。"""
import json
import sys

import cbor2
import requests

acc = json.load(open("/tmp/kiro_test_account.json"))
access_token = acc["accessToken"]
session_token = acc.get("sessionToken", "")

s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"})
cookies = {"AccessToken": access_token, "SessionToken": session_token, "Idp": "BuilderId"}

# 1) 常见 Kiro API 域名探测（OpenAI 兼容 /v1）
for base in ["https://api.kiro.dev", "https://api.kiro.dev/v1", "https://kiro.dev/api",
             "https://app.kiro.dev/api", "https://code.kiro.dev", "https://api.kiro.ai"]:
    for path in ["", "/v1/models", "/v1/chat/completions"]:
        url = base + path
        try:
            r = s.get(url, cookies=cookies, timeout=10)
            print(f"[GET {url}] HTTP {r.status_code} | {r.text[:150]!r}")
        except Exception as e:
            print(f"[GET {url}] ERR {e}")

# 2) 前端路由探测（API Keys 页面）
for path in ["/settings/api-keys", "/settings/api", "/api-keys", "/account/api-keys", "/settings"]:
    try:
        r = s.get("https://app.kiro.dev" + path, cookies=cookies, timeout=10)
        print(f"[app.kiro.dev{path}] HTTP {r.status_code} | title={r.text[r.text.find('<title>'):r.text.find('</title>')+8][:80]!r}")
    except Exception as e:
        print(f"[app.kiro.dev{path}] ERR {e}")