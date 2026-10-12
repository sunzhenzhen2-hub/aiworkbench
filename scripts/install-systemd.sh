#!/bin/bash
# 安装并启用项目支撑工作台 systemd 开机自启
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo ">>> 停止手动启动的进程（如有）..."
bash "$ROOT/scripts/stop-dev.sh" 2>/dev/null || true

echo ">>> 安装 systemd 服务..."
cp "$ROOT/deploy/systemd/workbench-backend.service" /etc/systemd/system/
cp "$ROOT/deploy/systemd/workbench-frontend.service" /etc/systemd/system/

systemctl daemon-reload
systemctl enable workbench-backend.service workbench-frontend.service
systemctl restart workbench-backend.service workbench-frontend.service

echo ""
echo "✅ 开机自启已配置"
echo "   后端: http://127.0.0.1:8100  (systemctl status workbench-backend)"
echo "   前端: http://127.0.0.1:5180  (systemctl status workbench-frontend)"
echo ""
systemctl is-active workbench-backend workbench-frontend
