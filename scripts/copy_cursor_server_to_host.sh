#!/usr/bin/env bash
# 把本机离线 Cursor 远程服务端拷到新机器，避免外网下载。
set -euo pipefail

HOST="${1:-}"
USER="${CURSOR_REMOTE_USER:-root}"
SRC="${CURSOR_OFFLINE_DIR:-/home/workbench/offline/cursor-remote}"

if [[ -z "$HOST" ]]; then
  echo "用法: $0 <IP或主机名>" >&2
  echo "示例: $0 30.251.255.142" >&2
  exit 1
fi

if [[ ! -d "$SRC/bin/linux-x64" ]] || ! find "$SRC/bin/linux-x64" -mindepth 1 -maxdepth 1 -type d | grep -q .; then
  echo "离线包为空，先执行: bash /home/workbench/scripts/sync_cursor_offline_bundle.sh" >&2
  exit 1
fi

SSH=(ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=15)
RSH="ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=15"
if [[ -n "${SSHPASS:-}" ]] && command -v sshpass >/dev/null 2>&1; then
  SSH=(sshpass -e ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=15)
  RSH="sshpass -e ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=15"
fi

echo "目标 ${USER}@${HOST}  ：准备 ~/.cursor-server"
"${SSH[@]}" "${USER}@${HOST}" 'mkdir -p ~/.cursor-server/bin/linux-x64 ~/.cursor-server/extensions'

echo "拷贝 bin/linux-x64"
rsync -a --info=stats1 -e "$RSH" "$SRC/bin/linux-x64/" "${USER}@${HOST}:~/.cursor-server/bin/linux-x64/"

shopt -s nullglob
bins=("$SRC"/cursor-*)
if [[ ${#bins[@]} -gt 0 ]]; then
  echo "拷贝 cursor-* 启动文件"
  rsync -a -e "$RSH" "${bins[@]}" "${USER}@${HOST}:~/.cursor-server/"
fi
shopt -u nullglob

if [[ -d "$SRC/extensions" ]]; then
  echo "拷贝 extensions"
  rsync -a -e "$RSH" "$SRC/extensions/" "${USER}@${HOST}:~/.cursor-server/extensions/"
fi

echo "写入远程设置：英文 locale，避免连上后去应用市场装中文包而卡住"
"${SSH[@]}" "${USER}@${HOST}" 'bash -s' <<'REMOTE'
set -euo pipefail
DATA="${HOME}/.cursor-server/data"
mkdir -p "$DATA/Machine" "$DATA/User"
cat > "$DATA/argv.json" <<'JSON'
{
  "locale": "en"
}
JSON
cat > "$DATA/Machine/settings.json" <<'JSON'
{
  "extensions.autoUpdate": false,
  "extensions.autoCheckUpdates": false,
  "extensions.ignoreRecommendations": true,
  "update.mode": "none",
  "http.proxySupport": "off"
}
JSON
cat > "$DATA/User/settings.json" <<'JSON'
{
  "extensions.autoUpdate": false,
  "extensions.autoCheckUpdates": false,
  "extensions.ignoreRecommendations": true,
  "locale": "en",
  "http.proxySupport": "off"
}
JSON
if [[ "$(id -u)" -eq 0 ]]; then
  for host in marketplace.cursorapi.com marketplace.visualstudio.com; do
    grep -q " ${host}$" /etc/hosts 2>/dev/null || echo "127.0.0.1 ${host}" >> /etc/hosts
    grep -q "::1 ${host}$" /etc/hosts 2>/dev/null || echo "::1 ${host}" >> /etc/hosts
  done
fi
REMOTE

echo "远端核对："
"${SSH[@]}" "${USER}@${HOST}" 'du -sh ~/.cursor-server/bin/linux-x64/* 2>/dev/null | sort'
echo "完成。请在 Cursor 里关掉旧窗口后重新连该机器。"
