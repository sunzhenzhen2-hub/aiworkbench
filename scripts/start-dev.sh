#!/bin/bash
# 启动项目支撑工作台开发服务（独立端口，不影响现有应用）
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/.env.ports"

PID_DIR="$ROOT/.pids"
mkdir -p "$PID_DIR"

check_port() {
  local port=$1 name=$2
  if ss -tln | grep -q ":${port} "; then
    local owner
    owner=$(ss -tlnp 2>/dev/null | grep ":${port} " | head -1)
    if [[ ! "$owner" == *"workbench"* ]] && [[ ! -f "$PID_DIR/backend.pid" ]]; then
      echo "❌ 端口 ${port} 已被占用，请先停止占用进程再启动 ${name}"
      echo "   $owner"
      exit 1
    fi
  fi
}

check_port "$WORKBENCH_BACKEND_PORT" "后端"
check_port "$WORKBENCH_FRONTEND_PORT" "前端"

# 启动后端
cd "$ROOT/backend"
if [[ ! -d .venv ]]; then
  echo "请先运行: cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
PYTHONPATH=. .venv/bin/uvicorn app.main:app \
  --host "$WORKBENCH_BACKEND_HOST" --port "$WORKBENCH_BACKEND_PORT" \
  > "$ROOT/.pids/backend.log" 2>&1 &
echo $! > "$PID_DIR/backend.pid"
echo "✅ 后端已启动: http://${WORKBENCH_BACKEND_HOST}:${WORKBENCH_BACKEND_PORT}"

# 启动前端
cd "$ROOT/web-pc"
VITE_API_PROXY="http://${WORKBENCH_BACKEND_HOST}:${WORKBENCH_BACKEND_PORT}" \
  npm run dev -- --host "$WORKBENCH_FRONTEND_HOST" --port "$WORKBENCH_FRONTEND_PORT" \
  > "$ROOT/.pids/frontend.log" 2>&1 &
echo $! > "$PID_DIR/frontend.pid"
echo "✅ 前端已启动: http://${WORKBENCH_FRONTEND_HOST}:${WORKBENCH_FRONTEND_PORT}"
echo ""
echo "演示账号: manager / manager123"
echo "停止服务: bash scripts/stop-dev.sh"
