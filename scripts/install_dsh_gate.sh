#!/bin/bash
# 安装 142 本机助手门禁：按登录名建个人工作区。禁止在现网 64 上跑。
set -euo pipefail

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [[ "${HOST_IP}" == "30.251.255.64" ]]; then
  echo "拒绝：不要在现网 64 上跑此脚本" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p /var/lib/dsh/users
chmod 700 /var/lib/dsh/users

cp "$ROOT/deploy/systemd/dsh-gate.service" /etc/systemd/system/dsh-gate.service
systemctl daemon-reload
if systemctl is-enabled dsh-web >/dev/null 2>&1 || systemctl is-active dsh-web >/dev/null 2>&1; then
  systemctl disable --now dsh-web || true
fi
systemctl enable --now dsh-gate
sleep 1
systemctl --no-pager --full status dsh-gate || true
ss -ltnp | grep 3081 || true
echo "本机助手门禁已监听 127.0.0.1:3081。工作台 DSH_INTERNAL_URL 应指向该地址。"
