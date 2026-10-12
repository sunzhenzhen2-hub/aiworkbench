#!/bin/bash
# 仅在 30.251.255.142 上安装本机 DSH，不连 64/132。
set -euo pipefail

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [[ "${HOST_IP}" == "30.251.255.64" ]]; then
  echo "拒绝：不要在现网 64 上跑此脚本" >&2
  exit 2
fi

command -v node >/dev/null
command -v npm >/dev/null

npm install -g @deepseek-ai/dsh
DSH_BIN="$(command -v dsh)"
test -n "$DSH_BIN"
"$DSH_BIN" --version || true

mkdir -p /root/.dsh /var/lib/dsh/workspace
chmod 700 /root/.dsh
if [[ ! -f /root/.dsh/.env ]]; then
  umask 077
  cat > /root/.dsh/.env <<'ENV'
# 在本机填写模型钥匙，不要从现网拷贝业务库。
# DEEPSEEK_API_KEY=
# MINIMAX_CN_API_KEY=
ENV
  chmod 600 /root/.dsh/.env
fi

cat > /etc/systemd/system/dsh-web.service <<UNIT
[Unit]
Description=DeepSeek Harness web (142 local)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/var/lib/dsh/workspace
Environment=HOME=/root
Environment=DSH_HOME=/root/.dsh
EnvironmentFile=-/root/.dsh/.env
ExecStart=/usr/bin/python3 /home/aiworkbench/scripts/run_dsh_web.py
Restart=on-failure
RestartSec=5
SyslogIdentifier=dsh-web

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl disable --now dsh-web 2>/dev/null || true
echo "共享 dsh-web 已停用。请再跑 scripts/install_dsh_gate.sh 按登录名启动个人助手。"
