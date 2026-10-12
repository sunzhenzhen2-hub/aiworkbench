#!/bin/bash
# 仅在 30.251.255.142 上执行：本机空库 + 演示数据。禁止连 64。
set -euo pipefail

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [[ "${HOST_IP}" == "30.251.255.64" ]]; then
  echo "拒绝：不能在现网 64 上跑本引导脚本" >&2
  exit 2
fi

ROOT="${ROOT:-/home/aiworkbench}"
cd "$ROOT"
python3 "$ROOT/scripts/assert_isolated_db.py" "postgresql+asyncpg://workbench:workbench_dev@127.0.0.1:5432/project_workbench_agent"

if ! command -v psql >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq postgresql python3-venv python3-pip >/dev/null
fi

sudo -u postgres psql -v ON_ERROR_STOP=1 <<'SQL' || true
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'workbench') THEN
    CREATE ROLE workbench LOGIN PASSWORD 'workbench_dev';
  END IF;
END$$;
SQL
sudo -u postgres psql -c "SELECT 1 FROM pg_database WHERE datname='project_workbench_agent'" | grep -q 1 \
  || sudo -u postgres createdb -O workbench project_workbench_agent

mkdir -p "$ROOT/backend"
cat > "$ROOT/backend/.env" <<'ENV'
DATABASE_URL=postgresql+asyncpg://workbench:workbench_dev@127.0.0.1:5432/project_workbench_agent
SECRET_KEY=agent-native-dev-change-me
DSH_INTERNAL_URL=http://127.0.0.1:3081
DSH_SERVICE_ALLOWLIST=127.0.0.1,30.251.255.142
ENV
python3 "$ROOT/scripts/assert_isolated_db.py" "$(grep ^DATABASE_URL= "$ROOT/backend/.env" | cut -d= -f2-)"

cd "$ROOT/backend"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt
PYTHONPATH=. .venv/bin/python -c "from scripts.assert_isolated_db import database_url_ok" 2>/dev/null || true
PYTHONPATH=. .venv/bin/python ../scripts/seed_demo.py

echo "142 bootstrap done: local db project_workbench_agent, demo users only"
