#!/usr/bin/env python3
import json
import mimetypes
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib import error, request


DEFAULT_BASE_URL = os.environ.get("CONTRIB_SERVER", "http://new.xem8k5.top:7317").rstrip("/")
SCRIPT_DIR = Path(__file__).resolve().parent
KEY_FILE = SCRIPT_DIR / "key.txt"
REDEEM_FILE = SCRIPT_DIR / "redeem.txt"


def load_saved_key() -> str:
    if KEY_FILE.exists():
        return KEY_FILE.read_text(encoding="utf-8").strip()
    return ""


def save_key(key: str) -> None:
    KEY_FILE.write_text(key.strip() + "\n", encoding="utf-8")


def append_redeem(code: str, redeemed_amount: float, remaining_balance: float) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{now}\t{code}\t兑换金额:{redeemed_amount}\t剩余余额:{remaining_balance}\n"
    with REDEEM_FILE.open("a", encoding="utf-8") as f:
        f.write(line)


def pretty(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def request_json(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    payload: Optional[Dict] = None,
    data: Optional[bytes] = None,
    timeout: int = 30,
) -> Tuple[int, Dict]:
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)

    body = data
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = request.Request(url=url, data=body, headers=req_headers, method=method.upper())
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            text = raw.decode("utf-8", errors="replace")
            return resp.status, json.loads(text) if text else {}
    except error.HTTPError as e:
        raw = e.read()
        text = raw.decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(text) if text else {}
        except json.JSONDecodeError:
            return e.code, {"error": text or str(e)}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def build_multipart(file_path: Path, field_name: str = "file") -> Tuple[bytes, str]:
    boundary = f"----codex2api-{uuid.uuid4().hex}"
    filename = file_path.name
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    file_bytes = file_path.read_bytes()

    lines: List[bytes] = []
    lines.append(f"--{boundary}\r\n".encode("utf-8"))
    lines.append(
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode("utf-8")
    )
    lines.append(f"Content-Type: {ctype}\r\n\r\n".encode("utf-8"))
    lines.append(file_bytes)
    lines.append(b"\r\n")
    lines.append(f"--{boundary}--\r\n".encode("utf-8"))

    return b"".join(lines), f"multipart/form-data; boundary={boundary}"


def call_first_success(
    method: str,
    base_url: str,
    paths: List[str],
    headers: Optional[Dict[str, str]] = None,
    payload: Optional[Dict] = None,
    data: Optional[bytes] = None,
) -> Tuple[int, Dict, str]:
    last_status = 0
    last_body: Dict = {"error": "no endpoint tried"}
    last_url = ""
    for path in paths:
        url = f"{base_url}{path}"
        status, body = request_json(method, url, headers=headers, payload=payload, data=data)
        last_status, last_body, last_url = status, body, url
        if status == 200:
            return status, body, url
    return last_status, last_body, last_url


def ask_amount() -> float:
    raw = input("请输入兑换金额(美元，留空默认0): ").strip()
    if not raw:
        return 0.0
    try:
        value = float(raw)
        return value if value > 0 else 0.0
    except ValueError:
        print("金额格式无效，按 0 处理。")
        return 0.0


def parse_paths(raw: str) -> List[Path]:
    targets: List[Path] = []
    for chunk in raw.split(","):
        s = chunk.strip().strip('"').strip("'")
        if not s:
            continue
        p = Path(s).expanduser()
        if p.is_dir():
            targets.extend(sorted([x for x in p.glob("*.json") if x.is_file()]))
        elif p.is_file():
            targets.append(p)
    unique: List[Path] = []
    seen = set()
    for p in targets:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def print_server_stats(data: Dict) -> None:
    print("服务器信息:")
    print(f"- 账号数: {data.get('quota_account_count', '-')}")
    print(f"- 总额度: {data.get('quota_total', '-')}")
    print(f"- 已用额度: {data.get('quota_used', '-')}")
    print(f"- 剩余额度: {data.get('quota_remaining', '-')}")
    print(f"- 已用百分比: {data.get('quota_used_percent', '-')}%")
    print(f"- 剩余百分比: {data.get('quota_remaining_percent', '-')}%")
    print(f"- 剩余额度折算账号数: {data.get('quota_remaining_accounts', '-')}")


def print_key_info(data: Dict) -> None:
    print("API Key 信息:")
    print(f"- ID: {data.get('id', '-')}")
    print(f"- 名称: {data.get('name', '-')}")
    print(f"- 来源: {data.get('source', '-')}")
    print(f"- 余额(USD): {data.get('balance_usd', '-')}")
    print(f"- 绑定账号数: {data.get('bound_account_count', '-')}")
    print(f"- 已结算金额(USD): {data.get('settled_amount_usd', '-')}")
    print(f"- 创建 IP: {data.get('created_ip', '-')}")
    print(f"- 最后使用 IP: {data.get('last_used_ip', '-')}")
    print(f"- 创建时间: {data.get('created_at', '-')}")
    print(f"- 最后使用时间: {data.get('last_used_at', '-')}")


def require_key(current_key: str) -> bool:
    if not current_key:
        print("请先执行 1 或 2 设置 API Key。")
        return False
    return True


def main() -> int:
    base_url = DEFAULT_BASE_URL
    api_key = load_saved_key()

    print(f"贡献工具启动，服务器: {base_url}")
    if api_key:
        print("已从 key.txt 读取 API Key。")

    while True:
        print("\n请选择操作:")
        print("1 获取apikey")
        print("2 设置apikey")
        print("3 选择文件上传")
        print("4 查看服务器信息")
        print("5 查看apikey账户信息")
        print("6 获取兑换码")
        print("7 退出")
        choice = input("输入选项编号: ").strip()

        if choice == "1":
            name = input("请输入 key 名称(留空默认 public-upload): ").strip() or "public-upload"
            status, body, used_url = call_first_success(
                "POST",
                base_url,
                ["/public/generate", "/api/contribution/generate"],
                payload={"name": name},
            )
            if status == 200 and body.get("key"):
                api_key = str(body.get("key", "")).strip()
                save_key(api_key)
                print(f"创建成功，已写入 key.txt: {api_key}")
            else:
                print(f"创建失败 [{status}] {used_url}")
                print(pretty(body))

        elif choice == "2":
            value = input("请输入已有 API Key: ").strip()
            if not value:
                print("未输入 key。")
                continue
            api_key = value
            save_key(api_key)
            print("已保存到 key.txt。")

        elif choice == "3":
            if not require_key(api_key):
                continue
            raw = input("请输入 JSON 文件路径(可多个,逗号分隔; 目录会上传目录下所有 .json): ").strip()
            files = parse_paths(raw)
            if not files:
                print("未找到可上传文件。")
                continue

            ok = 0
            failed = 0
            for path in files:
                try:
                    body_bytes, content_type = build_multipart(path)
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    print(f"[失败] {path}: {e}")
                    continue

                headers = {"X-Public-Key": api_key, "Content-Type": content_type}
                status, body, used_url = call_first_success(
                    "POST",
                    base_url,
                    ["/v0/management/auth-files", "/api/contribution/upload"],
                    headers=headers,
                    data=body_bytes,
                )
                if status == 200:
                    ok += 1
                    print(f"[成功] {path.name} -> {used_url}")
                    if body:
                        print(pretty(body))
                else:
                    failed += 1
                    print(f"[失败] {path.name} [{status}] {used_url}")
                    print(pretty(body))
            print(f"上传完成: 成功 {ok}, 失败 {failed}")

        elif choice == "4":
            status, body, used_url = call_first_success(
                "GET",
                base_url,
                ["/public/quota-stats", "/api/contribution/quota-stats"],
            )
            if status == 200:
                print_server_stats(body)
            else:
                print(f"获取失败 [{status}] {used_url}")
                print(pretty(body))

        elif choice == "5":
            if not require_key(api_key):
                continue
            headers = {"X-Public-Key": api_key}
            status, body, used_url = call_first_success(
                "GET",
                base_url,
                ["/public/key-info", "/api/contribution/key-info"],
                headers=headers,
            )
            if status == 200:
                print_key_info(body)
            else:
                print(f"获取失败 [{status}] {used_url}")
                print(pretty(body))

        elif choice == "6":
            if not require_key(api_key):
                continue
            amount = ask_amount()
            payload = {"amount_usd": amount} if amount > 0 else {}
            headers = {"X-Public-Key": api_key}
            status, body, used_url = call_first_success(
                "POST",
                base_url,
                ["/public/redeem", "/api/contribution/redeem"],
                headers=headers,
                payload=payload,
            )
            if status == 200 and body.get("code"):
                code = str(body.get("code"))
                redeemed = float(body.get("redeemed_amount_usd", 0) or 0)
                remain = float(body.get("remaining_balance_usd", 0) or 0)
                append_redeem(code, redeemed, remain)
                print(f"兑换成功! 额度:{redeemed} 兑换码:{code}")
                print("已写入 redeem.txt")
            else:
                print(f"兑换失败 [{status}] {used_url}")
                print(pretty(body))

        elif choice == "7":
            if api_key:
                save_key(api_key)
            print("已退出。")
            return 0

        else:
            print("无效选项，请输入 1-7。")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已中断。")
        sys.exit(130)
