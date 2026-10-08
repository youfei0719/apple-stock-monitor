#!/bin/bash
# 一键部署 / 重新部署（在服务器上执行，以 root 运行）
# 用法：/opt/stockmon/deploy/deploy.sh
# 注意：/opt/stockmon/deploy 是指向 $REPO/deploy 的软链接，git pull 后脚本自动跟随最新版。
set -euo pipefail

REPO=/opt/stockmon/repo
APP_DIR=/opt/stockmon/backend          # systemd WorkingDirectory/ExecStart 指向这里
ENV_FILE=/opt/stockmon/.env
DOMAIN=stock.glint.red

# ===== P0-24：.env 校验（最先执行，不满足直接报错退出） =====
[ -f "$ENV_FILE" ] || {
  echo "ERROR: $ENV_FILE 不存在。"
  echo "请先创建并填入真实值（可对照仓库 .env.example），然后重新运行本脚本。"
  exit 1
}
SECRET_VAL=$(sed -n 's/^APP_SECRET_KEY=//p' "$ENV_FILE" | head -n1 | tr -d " '\"\t\r")
if [ -z "$SECRET_VAL" ] || [ "$SECRET_VAL" = "change-me-64hex" ]; then
  echo "ERROR: $ENV_FILE 中 APP_SECRET_KEY 未设置或仍为默认值 change-me-64hex。"
  echo "请执行 openssl rand -hex 32 生成后填入，再重新运行。"
  exit 1
fi
echo "[deploy] .env 校验通过"
# D8：TRUSTED_PROXIES 空则登录限流按 IP 维度会把所有用户算成 127.0.0.1
# （nginx 反代场景）。缺省不致命，只 warning。
TP_VAL=$(sed -n 's/^TRUSTED_PROXIES=//p' "$ENV_FILE" | head -n1 | tr -d " '\"\t\r")
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

# ===== N12(b)：sqlite3 CLI（备份/排查 sqlite 真库用） =====
command -v sqlite3 >/dev/null 2>&1 || {
  echo "[deploy] 安装 sqlite3 CLI..."
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq sqlite3
  elif command -v yum >/dev/null 2>&1; then
    yum install -y sqlite3
  else
    echo "ERROR: 找不到 apt-get/yum，请手动安装 sqlite3 CLI 后再运行本脚本"
    exit 1
  fi
}

# 运行所需目录
# 注意：sqlite 真库在 $APP_DIR/data（DATABASE_URL=sqlite:///./data/app.db，相对 backend），
#       backend/logs 不用建——systemd 日志统一走 /opt/stockmon/logs
mkdir -p "$APP_DIR/data" /opt/stockmon/logs /opt/stockmon/frontend \
         /opt/stockmon/admin /opt/stockmon/backups

# systemd units：安装 + daemon-reload + enable（enable 幂等）
cp "$REPO/deploy/stockmon-api.service" "$REPO/deploy/stockmon-engine.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable stockmon-api.service stockmon-engine.service >/dev/null
echo "[deploy] systemd units 已安装并 enable"

# ===== P0-25：certbot 证书（nginx 配置引用 letsencrypt 真实路径） =====
if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
  command -v certbot >/dev/null 2>&1 || {
    echo "ERROR: certbot 未安装。请先安装：apt install -y certbot python3-certbot-nginx"
    exit 1
  }
  echo "[deploy] 为 $DOMAIN 申请证书..."
  certbot --nginx -d "$DOMAIN"
else
  echo "[deploy] 证书已存在，跳过 certbot"
fi
cp "$REPO/deploy/nginx-stock.glint.red.conf" /etc/nginx/conf.d/
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
command -v rsync >/dev/null 2>&1 || {
  echo "ERROR: rsync 未安装。请先安装：apt install -y rsync"
  exit 1
}
rsync -a --delete \
  --exclude='.venv' --exclude='__pycache__' --exclude='.ruff_cache' \
  --exclude='.pytest_cache' --exclude='data/' --exclude='logs/' \
  "$REPO/backend/" "$APP_DIR/"
echo "[deploy] 后端已同步 $REPO/backend/ -> $APP_DIR/"

# ===== 后端：venv / 依赖 / 检查（全部在 $APP_DIR 内做，与 systemd 一致） =====
cd "$APP_DIR"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
.venv/bin/ruff check app
echo "[deploy] 后端依赖安装 + ruff 检查通过"

# ===== P0-22：加载生产 .env 再 migrate =====
# 不加载的话 DATABASE_URL 会回退 config 默认值，migrate 会建野库
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
sleep 3
curl -sf http://127.0.0.1:8101/healthz | grep -q '"status":"ok"' \
  && echo "DEPLOY OK" || { echo "DEPLOY FAILED: healthz"; exit 1; }
