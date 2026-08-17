# Kiro 研究与 Kiro→OpenAI API 网关实证记录

> 日期：2026-08-16 · 目的：验证「免费 Kiro 账号能否转为 OpenAI 兼容 API」

## 1. 逆向结论（vendor.js / main.js 分析）

- **CPS（CodeWhisperer Control Plane Service）端点**：`https://management.us-east-1.kiro.dev/`
- **KiroControlPlaneBearerService**：认证用 `accessToken`（`aoa` 前缀）
- **认证类型**：BUILDER_ID（本账号）；SOCIAL 版（AWSIdC 账号专用）返回 403 `Invalid token`
- **profileArn（BUILDER 版，正确）**：`arn:aws:codewhisperer:us-east-1:638616132270:profile/AAAACCCCXXXX`
- **ListApiKeys**：200 `{"keys": []}`
- **CreateApiKey**：403 `"API key creation requires a Kiro subscription"`（免费档被订阅门槛拦截，**无法走官方 API key 路径**）
- **GetProfile**：403 "not authorized"（仅 AWSIdC 启用）
- 订阅：`KIRO FREE` / `Q_DEVELOPER_STANDALONE_FREE`，50 credits/月，`UPGRADE_CAPABLE`
- 付费档：KIRO_PRO $20 / PRO+ $40 / PRO MAX $100 / POWER（10k credits）

## 2. 数据库（MySQL，非 sqlite）

- `.env` 配 `MYSQL_*`，`core/db.py` 优先 MySQL
- accounts 表共 6 条：grok×2、trae×2、kiro id=5、**kiro id=6（本会话注册）**
- kiro id=6 的 extra_json 已更新：BUILDER profileArn + cpsEndpoint + cpsVerified

## 3. Kiro→API 网关方案对比（用户问题：能否用 sub2api）

| 项目 | 语言/形态 | 协议 | 许可 | 与我们的账号兼容性 |
|---|---|---|---|---|
| **sub2api** (hopol/moeakwak) | Go+Vue+PostgreSQL | OpenAI | LGPL/MIT | **不原生支持 Kiro**（upstream 仅 Claude/OpenAI/Gemini/Antigravity 等） |
| **kiro-gateway** (Jwadow) | Python FastAPI | OpenAI `/v1/chat/completions` + Anthropic `/v1/messages` | AGPL-3.0 | ✅ 凭据格式与 kiro_test_account.json 完全一致 |
| **kiroxy** (nopperabbo) | Go 单二进制 | Anthropic Messages API | MIT | ✅ 走 kiro-cli DB / Builder ID OAuth |
| **KiroaaS** (hnewcity) | 桌面壳 | - | - | 未深入 |

### kiro-gateway 关键点（已实证验证）
- 认证检测：
  - **Kiro Desktop Auth**（无 clientId/clientSecret 时）：`POST https://prod.{region}.auth.desktop.kiro.dev/refreshToken`，body `{"refreshToken": "..."}`
  - **AWS SSO (OIDC)**（有 clientId/clientSecret 时）：`https://oidc.{region}.amazonaws.com/token`
- 配置方式：Option 1 JSON 凭据文件 / Option 2 `.env` REFRESH_TOKEN / Option 3 kiro-cli SQLite / Option 4 AWS SSO cache
- 免费档可用模型：Claude Sonnet 4.5、Haiku 4.5、Sonnet 4、GLM-5、DeepSeek-V3.2、MiniMax M2.5/M2.1、Qwen3-Coder-Next
- 多账号 failover（credentials.json）、流式 SSE、tool calling、vision、web search

## 4. 实证过程（本地部署 kiro-gateway）

1. `git clone --depth 1 https://github.com/Jwadow/kiro-gateway.git` 到 `/tmp/kiro-gateway`，后按用户要求**本地副本**：`research/kiro/kiro-gateway`（含 `.env`、`.venv`，与 /tmp 版本分开）
2. 第一次启动失败：`Client error '401 Unauthorized' for url 'https://prod.us-east-1.auth.desktop.kiro.dev/refreshToken'`（`.env REFRESH_TOKEN` 配置 → kiro-gateway 误判为 Kiro Desktop Auth）
3. **根因（两次 401 的真相）**：desktop IDC 流（`_exchange_desktop_token`）产出的 token 是 **AWS SSO OIDC token**，必须在 `https://oidc.{region}.amazonaws.com/token` 用 **JSON body**（camelCase：`grantType=refresh_token&clientId&clientSecret&refreshToken`）刷新，而**不是** `prod.auth.desktop.kiro.dev/refreshToken`。kiro-gateway `_detect_auth_type`（auth.py L234）：有 clientId+clientSecret → `AWS_SSO_OIDC`，无 → `KIRO_DESKTOP`
4. **验证**：直接用 OIDC 端点 + 账号 JSON 的 clientId/clientSecret/refreshToken 刷新 → **HTTP 200**（新 accessToken/refreshToken，3600s）
5. **修复**：账号 JSON（`kiro_test_account.json`）含 clientId/clientSecret → `.env` 改用 `KIRO_CREDS_FILE` 指向它，让 kiro-gateway 自动走 AWS SSO OIDC；同时补回缺失的 `profileArn`（`arn:aws:codewhisperer:us-east-1:638616132270:profile/AAAACCCCXXXX`）
6. **启动成功**：`Detected auth type: AWS SSO OIDC` → `Token refreshed via AWS SSO OIDC` → 13 个模型 → 账号初始化成功
7. **curl 验证全部通过**：
   - `GET /health` → `{"status":"healthy"}`
   - `GET /v1/models` → 13 个模型（claude-sonnet-4.5 / haiku-4.5 / opus-4.5 / glm-5 / deepseek-3.2 / minimax-m2.5 等）
   - `POST /v1/chat/completions`（claude-sonnet-4.5，非流式）→ 200 真实回复 + `reasoning_content`
   - 流式（glm-5，SSE）→ 正常 chunk + `[DONE]`
8. **注意**：AWS SSO OIDC refresh_token 是**轮换制**（每次刷新换新），网关启动/刷新会自行写回 JSON 文件，无需手动维护

## 5. 已保存文件

- `scripts/`：注册/重登/入库/CPS 探测等脚本 + `kiro_test_account.json`（敏感！勿外传）
- `assets/`：`kiro_vendor.js`（3.3MB）、`kiro_main.js`、`kiro_SettingsApiKeysPage.js`（逆向素材）
- 外部参考：github.com/hopol/sub2api、github.com/moeakwak/sub2api、github.com/Jwadow/kiro-gateway、github.com/nopperabbo/kiroxy、github.com/hnewcity/KiroaaS、github.com/kirodotdev/Kiro/issues/3115（BYOK 功能追踪）