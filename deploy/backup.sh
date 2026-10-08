#!/bin/bash
# 用户数据每日备份：SQLite + .env -> /opt/stockmon/backups，按日轮转保留 14 天
# crontab: 10 3 * * * /opt/stockmon/deploy/backup.sh >> /opt/stockmon/logs/backup.log 2>&1
set -euo pipefail
ENV_FILE=/opt/stockmon/.env
APP_DIR=/opt/stockmon/backend   # 与 systemd WorkingDirectory 一致
DST_DIR=/opt/stockmon/backups
DAY=$(date +%F)
mkdir -p "$DST_DIR"

# ===== R7-D：备份失败自动告警（SMTP） =====
# Bark 是用户级通知渠道（bark_key 存各用户的任务配置里），没有运维全局 key；
# 备份是运维层任务，这里只走 SMTP（配置复用 .env 的 SMTP_HOST/USER/PASSWORD，
# prod 下 deploy.sh 已做硬门槛校验，部署脚本上下文里一定拿得到）。
# 收件人优先级：BACKUP_ALERT_TO > SSL_EMAIL > SMTP_USER；任一缺失则跳过告警只记日志。
alert() {
  local msg="$1" to host port user pass from from_addr subj scheme
  to=$(env_val BACKUP_ALERT_TO)
  [ -z "$to" ] && to=$(env_val SSL_EMAIL)
  [ -z "$to" ] && to=$(env_val SMTP_USER)
  host=$(env_val SMTP_HOST); port=$(env_val SMTP_PORT); user=$(env_val SMTP_USER)
  pass=$(env_val SMTP_PASSWORD); from=$(env_val SMTP_FROM)
  [ -z "$port" ] && port=465
  if [ -z "$host" ] || [ -z "$user" ] || [ -z "$pass" ] || [ -z "$to" ]; then
    echo "[$DAY] alert SKIP: SMTP/收件人配置缺失（需 SMTP_HOST/SMTP_USER/SMTP_PASSWORD + 收件人 BACKUP_ALERT_TO/SSL_EMAIL/SMTP_USER），仅记日志" >&2
    return 0
  fi
  # --mail-from 要裸地址：从 "Name <addr>" 里剥出 addr
  case "$from" in
    *"<"*">") from_addr=$(printf '%s' "$from" | sed -E 's/.*<([^<>]+)>.*/\1/') ;;
    *) from_addr="$from" ;;
  esac
  # 465=隐式 TLS，其余端口走 STARTTLS
  scheme="smtp"; extra="--ssl-reqd"
  if [ "$port" = "465" ]; then scheme="smtps"; extra=""; fi
  # 中文主题走 RFC2047 base64，避免裸 UTF-8 被拒收
  subj=$(printf '%s' "[StockMon] 每日备份失败 $DAY" | base64 | tr -d '\n')
  if printf 'From: %s\r\nTo: %s\r\nSubject: =?UTF-8?B?%s?=\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n%s\r\n' \
      "$from" "$to" "$subj" "$msg" \
    | curl -sS --max-time 30 "$scheme://$host:$port" $extra \
        --mail-from "$from_addr" --mail-rcpt "$to" \
        --user "$user:$pass" -T - >/dev/null 2>&1; then
    echo "[$DAY] alert sent -> $to"
  else
    echo "[$DAY] alert FAIL: 邮件发送失败（curl smtp），仅记日志" >&2
  fi
  return 0
}

# 失败统一出口：记日志 + 发告警 + 非零退出（让 cron 感知）
fail() {
  local msg="$1"
  echo "[$DAY] backup FAIL: $msg" >&2
  alert "$msg"
  exit 1
}

# ===== R5-D-1/D-2：.env 提取器 =====
# R11-P2-10：实现抽到 deploy/lib.sh（与 deploy.sh 共用），此处只 source，消两份拷贝漂移风险。
# R12-P3-1：source 必须在首次 fail 调用之前——fail→alert→env_val，
# 缺 .env 时若 lib.sh 还没 source，会报 env_val: command not found（set -e 下直接退出）。
# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[ -f "$ENV_FILE" ] || fail "$ENV_FILE 不存在"

# ===== P0-26：从 .env 的 DATABASE_URL 解析 sqlite 真实路径（只支持 sqlite） =====
DB_URL=$(env_val DATABASE_URL)
[ -n "$DB_URL" ] || fail "$ENV_FILE 缺少 DATABASE_URL"
case "$DB_URL" in
  sqlite:///*) ;;
  *) fail "仅支持 sqlite 备份，当前 DATABASE_URL=$DB_URL" ;;
esac
DB_PATH="${DB_URL#sqlite:///}"
case "$DB_PATH" in
  /*)  SRC="$DB_PATH" ;;                # sqlite:////abs/path/app.db（绝对路径）
  ./*) SRC="$APP_DIR/${DB_PATH#./}" ;;  # sqlite:///./data/app.db（相对 backend）
  *)   SRC="$APP_DIR/$DB_PATH" ;;       # sqlite:///data/app.db（相对 backend）
esac

[ -f "$SRC" ] || fail "数据库文件不存在: $SRC"

# ===== R4-P1-D7：备份连接加 busy_timeout（3:10 可能与引擎写锁撞车） =====
# .backup 失败时 sqlite3 进程可能仍退出 0，所以产物必须做非空 + 完整性检查，
# 失败走 fail()：写日志 + SMTP 告警 + 非零退出，让 cron 能感知（不能静默"备份成功"）。
# R8-I-2：sqlite3 本体命令加 || fail(...)，否则 set -e 直接终结脚本，
# 绕过 fail()/alert()（备份命令本身失败时运维收不到邮件）。
sqlite3 "$SRC" "PRAGMA busy_timeout=15000;" ".backup '$DST_DIR/app-$DAY.db'" \
  || fail "sqlite 备份命令失败（exit=$?）"
if [ ! -s "$DST_DIR/app-$DAY.db" ]; then
  fail "备份产物为空或缺失: $DST_DIR/app-$DAY.db（可能与引擎写锁撞车，busy_timeout=15s 仍超时）"
fi
if ! sqlite3 "$DST_DIR/app-$DAY.db" "PRAGMA quick_check;" | grep -q '^ok'; then
  fail "备份文件完整性校验未通过: $DST_DIR/app-$DAY.db"
fi
chmod 600 "$DST_DIR/app-$DAY.db"

# .env 一起备份：丢 .env = 全员会话失效 + 密钥/第三方 token 丢失
cp -p "$ENV_FILE" "$DST_DIR/env-$DAY"
chmod 600 "$DST_DIR/env-$DAY"

find "$DST_DIR" -name 'app-*.db' -mtime +14 -delete
find "$DST_DIR" -name 'env-*' -mtime +14 -delete
echo "[$DAY] backup ok: $DST_DIR/app-$DAY.db + $DST_DIR/env-$DAY"
# 恢复（⚠️ 先 systemctl stop 再 restore：WAL 模式下运行中 restore 有损坏风险）：
#   DB=/opt/stockmon/backend/data/app.db   # 默认路径；改过 DATABASE_URL 时以本脚本
#                                          # P0-26 的解析结果为准（相对路径以 /opt/stockmon/backend 为基准）
#   systemctl stop stockmon-api stockmon-engine
#   # R10-P1-D2：WAL 残留先收拢——崩溃后残留的 -wal 与 restore 进去的新主库帧不匹配，
#   # 下次打开有损坏风险。checkpoint 把帧并入主库后删掉空的 -wal/-shm，确认无残留再 restore。
#   sqlite3 "$DB" "PRAGMA wal_checkpoint(TRUNCATE);"
#   rm -f "$DB-wal" "$DB-shm"
#   [ -e "$DB-wal" ] || [ -e "$DB-shm" ] && { echo "ERROR: -wal/-shm 仍存在，排查后再 restore"; exit 1; }
#   sqlite3 "$DB" ".restore '/opt/stockmon/backups/app-YYYY-MM-DD.db'"
#   cp /opt/stockmon/backups/env-YYYY-MM-DD /opt/stockmon/.env && chmod 600 /opt/stockmon/.env
#   systemctl restart stockmon-api stockmon-engine
