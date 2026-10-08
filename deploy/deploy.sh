#!/bin/bash
# 一键部署 / 重新部署（在服务器上执行，以 root 运行）
# 用法：/opt/stockmon/deploy/deploy.sh
# 注意：/opt/stockmon/deploy 是指向 $REPO/deploy 的软链接，git pull 后脚本自动跟随最新版。
set -euo pipefail

# ===== R5-D-N7：root 检查（脚本多处写 /opt、/etc/systemd、操作 systemctl，非 root 直接报错退出） =====
if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "ERROR: 本脚本必须以 root 运行（需写 /opt、/etc/systemd、操作 systemctl）。"
  echo "请用 sudo -i 切换到 root 后重新运行。"
  exit 1
fi

REPO=/opt/stockmon/repo
APP_DIR=/opt/stockmon/backend          # systemd WorkingDirectory/ExecStart 指向这里
ENV_FILE=/opt/stockmon/.env
DOMAIN=stock.glint.red

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

# ===== R5-D-4：前置软件检查（缺哪个报哪个，一次列完再退出） =====
MISSING_SW=""
_need_cmd() {  # _need_cmd <说明名> <命令...>
  local label="$1"; shift
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 && return 0
  done
  MISSING_SW="$MISSING_SW $label"
}
_need_cmd git git
python3 -c 'import venv' >/dev/null 2>&1 || MISSING_SW="$MISSING_SW python3-venv"
_need_cmd node node
_need_cmd npm npm
_need_cmd nginx nginx
_need_cmd curl curl
_need_cmd sqlite3 sqlite3
_need_cmd rsync rsync
_need_cmd certbot certbot
_need_cmd crontab crontab   # R7：本脚本末尾用 crontab 安装 backup 定时任务，缺 crontab 部署到最后才报错
if [ -n "$MISSING_SW" ]; then
  echo "ERROR: 缺少以下前置软件:$MISSING_SW"
  echo "请先安装（如 Debian/Ubuntu：apt install -y git python3-venv nodejs npm nginx curl sqlite3 rsync certbot cron），再重新运行本脚本。"
  exit 1
fi
# ===== R6-D8：node 主版本号 ≥18 校验（vite 6 硬要求） =====
# Ubuntu 22.04 的 apt 默认 node 是 v12，装了但 build 必失败；这里提前拦截。
NODE_MAJOR=$(node --version 2>/dev/null | sed -E 's/^[vV]?([0-9]+).*/\1/')
if [ -z "$NODE_MAJOR" ] || [ "$NODE_MAJOR" -lt 18 ]; then
  echo "ERROR: node 版本过低（$(node --version 2>/dev/null || echo 未知)），vite 6 要求 node ≥ 18。"
  echo "Ubuntu 22.04 的 apt 默认 node 是 v12，不可用。请用 nodesource 安装 node 20 LTS 后重试："
  echo "  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs"
  exit 1
fi
echo "[deploy] 前置软件检查通过（node $(node --version)）"

# ===== P0-24：.env 校验（root/软件检查之后，不满足直接报错退出） =====
[ -f "$ENV_FILE" ] || {
  echo "ERROR: $ENV_FILE 不存在。"
  echo "请先创建并填入真实值（可对照仓库 .env.example），然后重新运行本脚本。"
  exit 1
}
# ===== R8-I-1：拦截 `export KEY=` 前缀行（fail-fast） =====
# systemd EnvironmentFile 会静默丢弃带 export 前缀的整行（真机实测：服务里读不到）。
# env_val 为了容错支持 export 前缀（R5-D-2），不拦截的话 deploy.sh 校验能通过、
# 但服务实际读不到该变量（APP_SECRET_KEY 等会回退默认值/报错）。直接拦死。
if grep -nE '^[[:space:]]*export[[:space:]]' "$ENV_FILE" >/dev/null 2>&1; then
  echo "ERROR: $ENV_FILE 里有 export 前缀行（systemd EnvironmentFile 会静默丢弃整行，服务读不到这些变量）："
  grep -nE '^[[:space:]]*export[[:space:]]' "$ENV_FILE" | sed 's/^/  行 /'
  echo "请去掉 export 前缀（直接写 KEY=value）后重新运行。"
  exit 1
fi
SECRET_VAL=$(env_val APP_SECRET_KEY)
if [ -z "$SECRET_VAL" ] || [ "$SECRET_VAL" = "change-me-64hex" ]; then
  echo "ERROR: $ENV_FILE 中 APP_SECRET_KEY 未设置或仍为默认值 change-me-64hex。"
  echo "请执行 openssl rand -hex 32 生成后填入，再重新运行。"
  exit 1
fi
echo "[deploy] .env 校验通过"

# ===== R4-P0-7：prod 硬门槛预检（早于任何重启/安装步骤） =====
# main.py lifespan 在 APP_ENV=prod 时要求以下 6 个变量缺一不可，缺则 API 直接
# RuntimeError 拒绝启动；这里提前报错退出，避免部署到最后才发现服务起不来。
APP_ENV_VAL=$(env_val APP_ENV)
if [ "$APP_ENV_VAL" = "prod" ]; then
  MISSING_PROD=""
  for var in AFDIAN_TOKEN AFDIAN_PLAN_STANDARD AFDIAN_PLAN_PRO SMTP_HOST SMTP_USER SMTP_PASSWORD; do
    V=$(env_val "$var")
    [ -z "$V" ] && MISSING_PROD="$MISSING_PROD $var"
  done
  if [ -n "$MISSING_PROD" ]; then
    echo "ERROR: prod 模式下 $ENV_FILE 缺少必需配置:$MISSING_PROD"
    echo "（API 在 prod 启动时会直接 RuntimeError 拒绝启动，请补齐后再部署）"
    exit 1
  fi
  echo "[deploy] prod 硬门槛预检通过"
else
  echo "[deploy] APP_ENV=$APP_ENV_VAL（非 prod），跳过 prod 硬门槛预检"
fi

# D8：TRUSTED_PROXIES 空则登录限流按 IP 维度会把所有用户算成 127.0.0.1
# （nginx 反代场景）。缺省不致命，只 warning。
TP_VAL=$(env_val TRUSTED_PROXIES)
if [ -z "$TP_VAL" ]; then
  echo "WARNING: $ENV_FILE 中 TRUSTED_PROXIES 为空。"
  echo "  nginx 反代场景下 _client_ip 只取直连 IP（127.0.0.1），"
  echo "  登录失败锁 IP / 注册限流会把所有用户算成同一个 IP。"
  echo "  建议填入可信代理地址（如 TRUSTED_PROXIES=127.0.0.1）。"
fi

# ===== P0-23：bootstrap（幂等，跑两遍不坏） =====
if [ ! -d "$REPO/.git" ]; then
  echo "[deploy] 首次部署：克隆仓库..."
  mkdir -p /opt/stockmon
  git clone https://github.com/youfei0719/apple-stock-monitor.git "$REPO"
fi
cd "$REPO"
# N12(a)：pull 前检查工作区是否干净，防止覆盖服务器上的本地改动
if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: 仓库工作区有未提交的本地改动，git pull 已中止（防止覆盖）。"
  echo "请先到 $REPO 手动处理（commit 或 git stash），再重新运行本脚本。"
  git status --short
  exit 1
fi
git pull --ff-only

# deploy/ 脚本目录软链接：cron 与用法里的 /opt/stockmon/deploy 始终指向仓库最新脚本
ln -sfn "$REPO/deploy" /opt/stockmon/deploy

# ===== N12(b)：sqlite3 CLI（备份/排查 sqlite 真库用；前置软件检查已覆盖） =====

# 运行所需目录
# 注意：sqlite 真库在 $APP_DIR/data（DATABASE_URL=sqlite:///./data/app.db，相对 backend）。
#       backend/logs/ 不用手动建：logging.py 默认 log_dir="logs"（相对 systemd
#       WorkingDirectory，即 $APP_DIR/logs），configure_logging 里 os.makedirs
#       自动创建。structlog JSON 应用日志（stockmon-api.log / stockmon-engine.log）
#       真实路径是 $APP_DIR/logs/，由 TimedRotatingFileHandler 自带轮转（30 天），
#       logrotate 不覆盖它——copytruncate 与 TimedRotatingFileHandler 混用有截断风险，
#       别把 backend/logs 加进 logrotate 配置。
#       /opt/stockmon/logs/ 只放 systemd 捕获的 stdout/stderr（*.out.log/*.err.log）
#       和 backup.sh 的 cron 输出（backup.log），logrotate 覆盖的是这些。
mkdir -p "$APP_DIR/data" /opt/stockmon/logs /opt/stockmon/frontend \
         /opt/stockmon/admin /opt/stockmon/backups

# systemd units：安装 + daemon-reload + enable（enable 幂等）
cp "$REPO/deploy/stockmon-api.service" "$REPO/deploy/stockmon-engine.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable stockmon-api.service stockmon-engine.service >/dev/null
echo "[deploy] systemd units 已安装并 enable"

# ===== P0-25：certbot 证书（nginx 配置自带 SSL stanza，引用 letsencrypt 真实路径） =====
# R5-D-3：顺序固定——先装 conf（含 80 端口 server 块）→ 申请 certonly 证书 → nginx -t → reload。
#   禁止 `certbot --nginx`（会改写我们手写的 conf）；统一用 certonly。
#   注意：conf 的 443 块引用 letsencrypt 真实路径，证书不存在时 `nginx -t` 必失败，
#   所以首次部署用 `--standalone` 拿证（需先停 nginx 释放 80 端口），拿证后再 -t/reload。
#   R6-D7：certonly 追加 --deploy-hook（写入 renewal 配置，60-90 天后的自动续期
#   才会 reload nginx，否则证书续了但 nginx 仍用旧证书，HTTPS 中断）。
#   R9-D6：renewal 配置记录 authenticator=standalone；certbot timer 续期时
#   standalone 必须独占 :80，但 nginx 常驻监听 :80 → bind 失败 → 续期失败 →
#   证书到期后 HTTPS 中断（--deploy-hook 只在续期成功后触发，救不了失败的续期）。
#   修复（方案 a）：certonly 追加 --pre-hook "systemctl stop nginx"
#   --post-hook "systemctl start nginx"（同样写入 renewal 配置，续期时先停
#   nginx 拿证再启动）。
#   R9-D6-实测：certbot hook 执行顺序是 pre-hook → deploy-hook → post-hook，
#   即 deploy-hook 运行时 nginx 还处于 pre-hook 停掉的状态；实测
#   `systemctl reload` 对 inactive 的 unit 直接报错退出（"not active, cannot
#   reload"，exit=1），会导致每次续期都被 certbot 报告为失败。所以 deploy-hook
#   用 `reload-or-restart`：nginx 在跑就 reload（用上新证书），被停了就 start。
#   注意：续期时 nginx 会短暂重启（数秒不可用），运维手册已按此诚实描述，
#   不再写"自动续期无需人工"。
#   待联调：certbot 与自带 SSL stanza 的 conf 在真机首次部署可能交互异常，
#   首次部署请人工盯一次 certbot 输出（见运维手册"首次部署完整步骤"）。
SSL_EMAIL_VAL=$(env_val SSL_EMAIL)
cp "$REPO/deploy/nginx-stock.glint.red.conf" /etc/nginx/conf.d/
if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
  [ -n "$SSL_EMAIL_VAL" ] || {
    echo "ERROR: 证书不存在，首次部署需申请证书，但 $ENV_FILE 中 SSL_EMAIL 未设置。"
    echo "请填入 certbot 注册邮箱（如 SSL_EMAIL=youfei8722@gmail.com）后重新运行。"
    exit 1
  }
  echo "[deploy] 为 $DOMAIN 申请证书（certonly --standalone，不改写 nginx conf）..."
  systemctl stop nginx || true
  # R7：certbot 失败时 set -e 会直接退出，此前已 stop 了 nginx；用 ERR trap 保证
  # 失败分支也恢复 nginx，不会留一个停掉的 nginx。成功后取消 trap。
  trap 'systemctl start nginx || true' ERR
  certbot certonly --non-interactive --agree-tos -m "$SSL_EMAIL_VAL" \
    --standalone -d "$DOMAIN" \
    --pre-hook "systemctl stop nginx" --post-hook "systemctl start nginx" \
    --deploy-hook "systemctl reload-or-restart nginx"
  trap - ERR
  systemctl start nginx
  echo "[deploy] 证书申请完成"
else
  echo "[deploy] 证书已存在，跳过 certbot"
fi
systemctl enable --now nginx   # R5-D-4：保证 nginx 在跑，再 reload
nginx -t && systemctl reload nginx
echo "[deploy] nginx 配置已生效"

# logrotate：/opt/stockmon/logs/*.log 每日轮转保留 14 天（P1）
cp "$REPO/deploy/logrotate-stockmon.conf" /etc/logrotate.d/stockmon
echo "[deploy] logrotate 已安装"

# backup cron：每天 3:10（先去重旧条目再加，保证幂等）
( crontab -l 2>/dev/null | grep -v 'deploy/backup.sh' || true
  echo "10 3 * * * /opt/stockmon/deploy/backup.sh >> /opt/stockmon/logs/backup.log 2>&1"
) | crontab -
echo "[deploy] backup cron 已设置（每天 3:10）"
echo "[deploy] bootstrap 完成"

# ===== P0-21：后端同步到 systemd 指向的目录 =====
# 关键排除项（缺一不可）：
#   data/  —— sqlite 真库（DATABASE_URL 相对 backend）；--delete 会把生产库删掉
#   logs/  —— 运行时日志，不随代码同步
#   .venv  —— 在 $APP_DIR 内重建，不从仓库拷（避免架构/路径污染）
# rsync 缺失已在前置软件检查拦截（R5-D-4）
rsync -a --delete \
  --exclude='.venv' --exclude='__pycache__' --exclude='.ruff_cache' \
  --exclude='.pytest_cache' --exclude='data/' --exclude='logs/' \
  "$REPO/backend/" "$APP_DIR/"
echo "[deploy] 后端已同步 $REPO/backend/ -> $APP_DIR/"

# P2：ruff.toml 在仓库根，rsync 不覆盖它；不拷的话 `ruff check app` 在 $APP_DIR
# 里按默认规则跑（line-length=100 等配置不生效）。同步一份过去。
if [ -f "$REPO/ruff.toml" ]; then
  cp "$REPO/ruff.toml" "$APP_DIR/ruff.toml"
  echo "[deploy] ruff.toml 已同步到 $APP_DIR/"
fi

# ===== 后端：venv / 依赖 / 检查（全部在 $APP_DIR 内做，与 systemd 一致） =====
cd "$APP_DIR"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
.venv/bin/ruff check app
echo "[deploy] 后端依赖安装 + ruff 检查通过"

# ===== P0-22：加载生产 .env 再 migrate =====
# 不加载的话 DATABASE_URL 会回退 config 默认值，migrate 会建野库
# 安全注意（R5-D-N8）：`source .env` 会执行命令替换/变量展开，
# .env 里不要写 $(...)、反引号；本脚本以 root 执行，恶意内容会被执行。
[ -f "$ENV_FILE" ] || { echo "ERROR: $ENV_FILE 不存在，无法 migrate"; exit 1; }
set -a; source "$ENV_FILE"; set +a
.venv/bin/alembic upgrade head
echo "[deploy] alembic migrate 完成"

# --- 前台 ---
cd "$REPO/frontend"
npm ci --no-audit --no-fund
npm run build
rm -rf /opt/stockmon/frontend/dist && cp -r dist /opt/stockmon/frontend/dist
echo "[deploy] 前台构建部署完成"

# --- 后台 ---
cd "$REPO/admin"
npm ci --no-audit --no-fund
npm run build
rm -rf /opt/stockmon/admin/dist && cp -r dist /opt/stockmon/admin/dist
echo "[deploy] 后台构建部署完成"

# --- 静态文件权限（防 403 旧坑） ---
chmod -R 755 /opt/stockmon/frontend/dist /opt/stockmon/admin/dist
find /opt/stockmon/frontend/dist /opt/stockmon/admin/dist -type f -exec chmod 644 {} \;

# --- 重启服务 ---
systemctl restart stockmon-api stockmon-engine
# ===== R4-P1-D4：健康检查轮询最多 60 秒等引擎心跳就绪 =====
# healthz 的 status:ok 是硬编码的；引擎的心跳由独立进程写进 DB，
# sleep 3 就判大概率引擎还没启动，形同虚设。轮询等 engine=="running"。
HEALTH_OK=0
HEALTHZ=""
for _ in $(seq 1 60); do
  HEALTHZ=$(curl -sf http://127.0.0.1:8101/healthz 2>/dev/null || true)
  if echo "$HEALTHZ" | grep -q '"status":"ok"' && echo "$HEALTHZ" | grep -q '"engine":"running"'; then
    HEALTH_OK=1
    break
  fi
  sleep 1
done
if [ "$HEALTH_OK" = "1" ]; then
  echo "DEPLOY OK"
else
  echo "DEPLOY FAILED: 60 秒内 healthz 未报告 engine=running"
  echo "--- healthz 最后一次输出 ---"
  echo "${HEALTHZ:-<无响应>}"
  echo "--- stockmon-engine 日志尾部 ---"
  journalctl -u stockmon-engine -n 30 --no-pager || true
  echo "--- stockmon-api 日志尾部 ---"
  journalctl -u stockmon-api -n 30 --no-pager || true
  exit 1
fi
