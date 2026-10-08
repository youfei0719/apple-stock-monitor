#!/bin/bash
# deploy 共享函数库（R11-P2-10：从 deploy.sh / backup.sh 抽取共用，消两份拷贝漂移风险）。
# 注意：本文件是被 source 的，不要在这里写 `set -euo pipefail`、exit 或顶层副作用；
# 调用方在 source 前必须先定义好 $ENV_FILE（env_val 在调用时读取，不在 source 时）。
# 用法（deploy.sh / backup.sh 头部）：
#   # shellcheck disable=SC1091
#   source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# ===== R5-D-1/D-2：.env 提取器 =====
# 提取 $ENV_FILE 中 KEY 的值：
#   - 先去行尾注释再取值：`APP_ENV=prod  # 生产环境` → `prod`（R5-D-1）
#   - 支持 `export KEY=...` 前缀写法（R5-D-2）；但注意：systemd EnvironmentFile
#     会静默丢弃 export 行，所以校验阶段（R8-I-1）已把 export 前缀拦截报错，
#     这里的支持只是容错，不代表生产 .env 里可以写 export
#   - 值里含 # 时必须加引号（如 PASSWORD='a#b'）；引号包裹的值按引号边界取值
#   - R6-P2-6：重复键取最后一个，与 `source` 语义一致（重复键以后者为准）
#   - R6-P2-1：最终清理只去首尾（空白/CR/引号），值内空格原样保留
#     （旧 tr -d " '\"\t\r" 会吃掉引号内合法空格，如 SMTP_FROM="StockMon <noreply@glint.red>"）
env_val() {
  local key="$1" line val rest
  line=$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$ENV_FILE" | tail -n1) || return 0
  [ -z "$line" ] && return 0
  # 剥掉可选的 export 前缀与 KEY=（值里可能含 =，只剥第一个）
  val=$(printf '%s\n' "$line" | sed -E 's/^[[:space:]]*(export[[:space:]]+)?[^=[:space:]]+=//')
  val=$(printf '%s' "$val" | sed -E 's/^[[:space:]]+//;s/[[:space:]]+$//')
  case "$val" in
    \"*)
      rest="${val#\"}"
      val="${rest%%\"*}"
      ;;
    \'*)
      rest="${val#\'}"
      val="${rest%%\'*}"
      ;;
    *)
      # 未加引号：先去行尾注释（空白+# 开头）再取值
      val=$(printf '%s' "$val" | sed -E 's/[[:space:]]+#.*$//;s/[[:space:]]+$//')
      ;;
  esac
  # R6-P2-1：CR 直接去掉（Windows 行尾残留），首尾空白与残留引号剥掉，
  # 值内空格/tab 原样保留。
  val=$(printf '%s' "$val" | tr -d '\r')
  val="${val#"${val%%[![:space:]]*}"}"
  val="${val%"${val##*[![:space:]]}"}"
  val="${val#\"}"; val="${val%\"}"
  val="${val#\'}"; val="${val%\'}"
  printf '%s' "$val"
}
