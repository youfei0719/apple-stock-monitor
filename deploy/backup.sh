#!/bin/bash
# 用户数据每日备份：SQLite -> /opt/stockmon/backups，按日轮转保留 14 天
# crontab: 0 3 * * * /opt/stockmon/deploy/backup.sh >> /opt/stockmon/logs/backup.log 2>&1
set -euo pipefail
SRC=/opt/stockmon/data/app.db
DST_DIR=/opt/stockmon/backups
DAY=$(date +%F)
mkdir -p "$DST_DIR"
if [ -f "$SRC" ]; then
  sqlite3 "$SRC" ".backup '$DST_DIR/app-$DAY.db'"
  chmod 600 "$DST_DIR/app-$DAY.db"
  find "$DST_DIR" -name 'app-*.db' -mtime +14 -delete
  echo "[$DAY] backup ok: $DST_DIR/app-$DAY.db"
else
  echo "[$DAY] backup SKIP: $SRC not found" >&2
  exit 1
fi
# 恢复：sqlite3 /opt/stockmon/data/app.db ".restore '/opt/stockmon/backups/app-YYYY-MM-DD.db'"
