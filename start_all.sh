#!/bin/bash
# =============================================================================
# 一键启动脚本：auto_reg + kiro-gateway + grok2api
#   幂等：已在运行的端口跳过，未运行的启动
#   用法：./start_all.sh [stop]
#     （无参数）启动/检查全部服务
#     stop       停止全部服务
# =============================================================================

set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$ROOT/services/external_logs"
mkdir -p "$LOG_DIR"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC}  $1"; }
ok()    { echo -e "${GREEN}[ OK ]${NC}  $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $1"; }

# 端口是否在监听（macOS 兼容）
port_listening() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

# HTTP 健康检查
health() {
  curl -s -m 3 -o /dev/null -w "%{http_code}" "$1" 2>/dev/null
}

# 后台启动并等待健康
launch() {
  local name="$1" cmd="$2" health_url="$3" log="$4" dir="$5" port="$6"
  if port_listening "$port"; then
    ok "$name 已在运行 (端口 $port)，跳过"
    return 0
  fi
  info "启动 $name ..."
  # shellcheck disable=SC2086
  (cd "$dir" && nohup $cmd >>"$log" 2>&1 &)
  for _ in $(seq 1 30); do
    sleep 1
    if port_listening "$port"; then
      ok "$name 已启动 (端口 $port)"
      return 0
    fi
  done
  fail "$name 启动失败，日志: $log"
  return 1
}

stop_service() {
  local name="$1" port="$2" pids
  pids=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null)
  if [ -z "$pids" ]; then
    ok "$name 未运行"
    return 0
  fi
  info "停止 $name (PID: $(echo "$pids" | tr '\n' ' '))..."
  kill $pids 2>/dev/null
  sleep 2
  if port_listening "$port"; then
    kill -9 $pids 2>/dev/null
  fi
  ok "$name 已停止"
}

# main.py 不加载 .env，必须显式注入端口
AUTO_REG_CMD="env PORT=8010 HOST=0.0.0.0 .venv/bin/python main.py"
KIRO_GW_CMD=".venv/bin/python main.py"

# Grok 协议注册使用参考项目的真实页面 Turnstile solver。
REFERENCE_ROOT="$ROOT/../gptGrok2api"
GROK_SOLVER_CMD=".venv/bin/python captcha-solver/server.py"

# _ext_targets 位于 auto_reg 的上级目录（PycharmProjects/_ext_targets）
EXT_ROOT="$ROOT/../_ext_targets"
GROK2API_BIN="$EXT_ROOT/grok2api/bin/grok2api"
GROK2API_CMD="$GROK2API_BIN --config $EXT_ROOT/grok2api/config.yaml"

if [ "${1:-}" = "stop" ]; then
  stop_service "auto_reg"     8010
  stop_service "kiro-gateway" 8766
  stop_service "gptGrok2api Turnstile Solver" 8877
  stop_service "grok2api"     8011
  info "全部服务已停止"
  exit 0
fi

echo "================================================================"
echo " 一键启动：auto_reg + kiro-gateway + Grok Turnstile Solver + grok2api"
echo "================================================================"

launch "auto_reg"     "$AUTO_REG_CMD"     "http://127.0.0.1:8010/"    "$LOG_DIR/auto_reg.log"     "$ROOT" 8010
launch "kiro-gateway" "$KIRO_GW_CMD"      "http://127.0.0.1:8766/health" "$LOG_DIR/kiro-gateway.log" "$ROOT/research/kiro/kiro-gateway" 8766

if [ -f "$REFERENCE_ROOT/captcha-solver/server.py" ] && [ -x "$REFERENCE_ROOT/.venv/bin/python" ]; then
  launch "gptGrok2api Turnstile Solver" "$GROK_SOLVER_CMD" "http://127.0.0.1:8877/health" "$LOG_DIR/grok_solver.log" "$REFERENCE_ROOT" 8877
else
  warn "未找到 gptGrok2api 的 8877 solver，Grok 协议注册需要手动启动它"
fi

if [ ! -x "$GROK2API_BIN" ]; then
  warn "grok2api 二进制不存在，尝试编译..."
  (cd "$EXT_ROOT/grok2api/backend" && GOCACHE="$EXT_ROOT/grok2api/.gocache" go build -o "$GROK2API_BIN" ./cmd/grok2api) || {
    fail "grok2api 编译失败，请手动编译"
  }
fi
launch "grok2api"     "$GROK2API_CMD"     "http://127.0.0.1:8011/health" "$LOG_DIR/grok2api.log"    "$EXT_ROOT/grok2api" 8011

echo
echo "================================================================"
echo " 服务状态 & 管理端地址"
echo "================================================================"
check() {
  local name="$1" url="$2" port="$3"
  local code; code=$(health "$url")
  if port_listening "$port" && [ "$code" != "000" ]; then
    ok   "$name  ->  $url   (HTTP $code)"
  else
    fail "$name  ->  $url   (未就绪)"
  fi
}
check "auto_reg 管理端"      "http://127.0.0.1:8010"      8010
check "kiro-gateway API 文档" "http://127.0.0.1:8766/docs"  8766
if [ -f "$REFERENCE_ROOT/captcha-solver/server.py" ]; then
  check "Grok Turnstile Solver" "http://127.0.0.1:8877/health" 8877
fi
check "grok2api 管理端"      "http://127.0.0.1:8011/admin" 8011
echo
echo "日志目录: $LOG_DIR"
echo "停止全部: $0 stop"
