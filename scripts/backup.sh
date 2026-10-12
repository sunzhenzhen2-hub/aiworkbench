#!/bin/bash
# PostgreSQL 每日备份：只读导出，不改动库内任何数据
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="$ROOT/backups"
LOG_FILE="$BACKUP_DIR/backup.log"
mkdir -p "$BACKUP_DIR"

# 优先从 backend/.env 读连接信息，避免口令散落多处
ENV_FILE="$ROOT/backend/.env"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a
  # 只加载 DATABASE_URL 相关行，避免污染环境
  # DATABASE_URL=postgresql+asyncpg://user:pass@host:port/db
  DATABASE_URL="$(grep -E '^DATABASE_URL=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
  set +a
fi

DB_URL="${DATABASE_URL:-postgresql+asyncpg://workbench:workbench_dev@localhost:5432/project_workbench}"
# 解析 postgresql+asyncpg://user:pass@host:port/dbname
REST="${DB_URL#*://}"
USERPASS="${REST%%@*}"
HOSTDB="${REST#*@}"
PGUSER="${USERPASS%%:*}"
PGPASSWORD="${USERPASS#*:}"
PGHOST="${HOSTDB%%:*}"
PORTDB="${HOSTDB#*:}"
PGPORT="${PORTDB%%/*}"
PGDATABASE="${PORTDB#*/}"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$BACKUP_DIR/project_workbench_${TIMESTAMP}.sql.gz"

export PGPASSWORD
pg_dump -U "$PGUSER" -h "$PGHOST" -p "$PGPORT" "$PGDATABASE" | gzip > "$OUT"

# 保留约 30 天
find "$BACKUP_DIR" -name 'project_workbench_*.sql.gz' -mtime +30 -delete

SIZE="$(du -h "$OUT" | awk '{print $1}')"
MSG="$(date '+%F %T') Backup OK size=$SIZE file=$OUT"
echo "$MSG" | tee -a "$LOG_FILE"
