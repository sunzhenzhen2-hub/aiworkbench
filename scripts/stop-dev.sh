#!/bin/bash
# 停止项目支撑工作台开发服务（仅停止本项目进程）
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID_DIR="$ROOT/.pids"

stop_pid() {
  local name=$1 file=$2
  if [[ -f "$file" ]]; then
    local pid
    pid=$(cat "$file")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      echo "✅ 已停止 ${name} (PID ${pid})"
    fi
    rm -f "$file"
  else
    echo "ℹ️  ${name} 未在运行"
  fi
}

stop_pid "后端" "$PID_DIR/backend.pid"
stop_pid "前端" "$PID_DIR/frontend.pid"

# 兜底：仅清理 workbench 目录下启动的 uvicorn/vite 进程
pkill -f "${ROOT}/backend/.venv/bin/uvicorn" 2>/dev/null && echo "✅ 清理残留后端进程" || true
pkill -f "${ROOT}/web-pc/node_modules/.bin/vite" 2>/dev/null && echo "✅ 清理残留前端进程" || true
