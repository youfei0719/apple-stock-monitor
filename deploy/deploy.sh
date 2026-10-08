#!/bin/bash
# 一键重新部署（在服务器上执行）
# 用法：/opt/stockmon/deploy/deploy.sh
set -euo pipefail
REPO=/opt/stockmon/repo
cd "$REPO" && git pull --ff-only

# --- 后端 ---
cd "$REPO/backend"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
.venv/bin/ruff check app
.venv/bin/alembic upgrade head

# --- 前台 ---
cd "$REPO/frontend"
npm ci --no-audit --no-fund
npm run build
rm -rf /opt/stockmon/frontend/dist && cp -r dist /opt/stockmon/frontend/dist

# --- 后台 ---
cd "$REPO/admin"
npm ci --no-audit --no-fund
npm run build
rm -rf /opt/stockmon/admin/dist && cp -r dist /opt/stockmon/admin/dist

# --- 静态文件权限（防 403 旧坑） ---
chmod -R 755 /opt/stockmon/frontend/dist /opt/stockmon/admin/dist
find /opt/stockmon/frontend/dist /opt/stockmon/admin/dist -type f -exec chmod 644 {} \;

# --- 重启服务 ---
systemctl restart stockmon-api stockmon-engine
sleep 3
curl -sf http://127.0.0.1:8101/healthz | grep -q '"status":"ok"' \
  && echo "DEPLOY OK" || { echo "DEPLOY FAILED: healthz"; exit 1; }
