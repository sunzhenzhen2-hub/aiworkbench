#!/bin/bash
# 把 Agent Native 代码同步到 142 并在对方本机空库引导。禁止 restore 64 备份。
set -euo pipefail

ROOT="/home/workbench"
HOST="${AGENT_NATIVE_HOST:-30.251.255.142}"
REMOTE_DIR="${AGENT_NATIVE_DIR:-/home/aiworkbench}"

python3 "$ROOT/scripts/assert_isolated_db.py" "${DATABASE_URL:-}"

echo "同步到 ${HOST}:${REMOTE_DIR}"
rsync -az \
  --exclude '.venv' \
  --exclude 'web-pc/node_modules' \
  --exclude 'web-dingtalk/node_modules' \
  --exclude 'backups/' \
  --exclude 'backend/.env' \
  --exclude '.git/' \
  "$ROOT/" "root@${HOST}:${REMOTE_DIR}/"

ssh -o BatchMode=yes "root@${HOST}" "chmod +x ${REMOTE_DIR}/scripts/bootstrap_agent_native.sh ${REMOTE_DIR}/scripts/assert_isolated_db.py; ROOT=${REMOTE_DIR} bash ${REMOTE_DIR}/scripts/bootstrap_agent_native.sh"
ssh -o BatchMode=yes "root@${HOST}" "bash -s" <<REMOTE
set -euo pipefail
for f in ${REMOTE_DIR}/web-pc/.env.production ${REMOTE_DIR}/web-pc/.env.development ${REMOTE_DIR}/web-dingtalk/.env.production; do
  [[ -f "\$f" ]] || continue
  grep -q '^VITE_AGENT_NATIVE=' "\$f" || echo 'VITE_AGENT_NATIVE=true' >> "\$f"
done
REMOTE
