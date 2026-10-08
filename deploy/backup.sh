#!/bin/bash
# 用户数据每日备份：SQLite + .env -> /opt/stockmon/backups，按日轮转保留 14 天
# crontab: 10 3 * * * /opt/stockmon/deploy/backup.sh >> /opt/stockmon/logs/backup.log 2>&1
set -euo pipefail
ENV_FILE=/opt/stockmon/.env
APP_DIR=/opt/stockmon/backend   # 与 systemd WorkingDirectory 一致
DST_DIR=/opt/stockmon/backups
DAY=$(date +%F)
mkdir -p "$DST_DIR"

[ -f "$ENV_FILE" ] || { echo "[$DAY] backup FAIL: $ENV_FILE 不存在" >&2; exit 1; }

# ===== P0-26：从 .env 的 DATABASE_URL 解析 sqlite 真实路径（只支持 sqlite） =====
DB_URL=$(sed -n 's/^DATABASE_URL=//p' "$ENV_FILE" | head -n1 | tr -d " '\"\t\r")
[ -n "$DB_URL" ] || { echo "[$DAY] backup FAIL: $ENV_FILE 缺少 DATABASE_URL" >&2; exit 1; }
case "$DB_URL" in
  sqlite:///*) ;;
  *) echo "[$DAY] backup FAIL: 仅支持 sqlite 备份，当前 DATABASE_URL=$DB_URL" >&2; exit 1 ;;
esac
DB_PATH="${DB_URL#sqlite:///}"
case "$DB_PATH" in
  /*)  SRC="$DB_PATH" ;;                # sqlite:////abs/path/app.db（绝对路径）
  ./*) SRC="$APP_DIR/${DB_PATH#./}" ;;  # sqlite:///./data/app.db（相对 backend）
  *)   SRC="$APP_DIR/$DB_PATH" ;;       # sqlite:///data/app.db（相对 backend）
esac

[ -f "$SRC" ] || { echo "[$DAY] backup FAIL: 数据库文件不存在: $SRC" >&2; exit 1; }

sqlite3 "$SRC" ".backup '$DST_DIR/app-$DAY.db'"
chmod 600 "$DST_DIR/app-$DAY.db"

# .env 一起备份：丢 .env = 全员会话失效 + 密钥/第三方 token 丢失
cp -p "$ENV_FILE" "$DST_DIR/env-$DAY"
chmod 600 "$DST_DIR/env-$DAY"

find "$DST_DIR" -name 'app-*.db' -mtime +14 -delete
find "$DST_DIR" -name 'env-*' -mtime +14 -delete
echo "[$DAY] backup ok: $DST_DIR/app-$DAY.db + $DST_DIR/env-$DAY"
# 恢复：
#   sqlite3 <上一步解析出的 SRC> ".restore '/opt/stockmon/backups/app-YYYY-MM-DD.db'"
#   cp /opt/stockmon/backups/env-YYYY-MM-DD /opt/stockmon/.env && chmod 600 /opt/stockmon/.env
#   systemctl restart stockmon-api stockmon-engine
