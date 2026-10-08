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
# R11-P2-10：实现抽到 deploy/lib.sh（与 backup.sh 共用），此处只 source，消两份拷贝漂移风险。
# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

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
# R10-P2-2：`python3 -c 'import venv'` 挡不住缺 python3-venv——Debian 系把
# ensurepip 拆到 python3-venv 包里，venv 模块本体在基础包里就有；
# `python3 -m venv` 实际失败在 ensurepip。所以改查 ensurepip。
python3 -c 'import ensurepip' >/dev/null 2>&1 || MISSING_SW="$MISSING_SW python3-venv"
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
# ===== R10-P2-3：CRLF（Windows 行尾）清洗（source 之前） =====
# env_val 会剥 \r，但 bash `set -a; source .env` 不剥：
# `APP_ENV=prod\r` → APP_ENV="prod\r" → is_prod 为 False（systemd 侧同理污染）。
# .env 必须保持 LF；这里检测到 CRLF 就原地清洗并告警（原文件会被改写）。
if grep -qU $'\r' "$ENV_FILE" 2>/dev/null; then
  echo "WARNING: $ENV_FILE 含 CRLF（Windows 行尾）：bash source 不剥 \\r，"
  echo "  值会带上 \\r（如 APP_ENV 读成 \"prod\\r\"，is_prod 误判）。"
  echo "  已自动清洗为 LF（原文件已原地改写）。后续请用 LF 行尾编辑 .env。"
  sed -i 's/\r$//' "$ENV_FILE"
fi
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
# ===== R10-P0-1 / R11-P1-7：拦截行尾注释污染（fail-fast，扩展到所有 key） =====
# systemd EnvironmentFile 不剥行尾注释：`APP_ENV=prod  # 生产` → 服务读到
# "prod  # 生产" → is_prod 为 False → **生产跑 dev 语义**
# （lifespan prod 硬门槛永久失效、生产 HTTPS cookie 丢 Secure 标志）。
# 而 env_val 会剥、bash source 会剥 → 不拦截的话 deploy.sh 校验全绿但服务
# 实际跑的是污染值。模板曾自带行尾注释（.env.example:15/16，R10 已移到独立行），
# 老机器的手工 .env 很可能还留着，直接拦死。
# R11-P1-7：从"只拦 APP_ENV/APP_SECRET_KEY"扩展到所有 key——其它 key
# （如 SMTP_PASSWORD=abc  # 注释）此前校验全绿但 systemd 读到污染值，
# 运行时静默故障。引号感知：值里引号内的 # 合法（如 PASSWORD='a#b'，
# env_val 按引号边界取值、systemd 读到的也是引号内原文），不误伤；
# 引号外的 #（行尾注释或裸 #）一律拦截。
_env_polluted=""
_env_lineno=0
while IFS= read -r _env_line || [ -n "$_env_line" ]; do
  _env_lineno=$((_env_lineno + 1))
  _env_s="${_env_line%$'\r'}"
  # 跳过空行 / 纯空白行 / 整行注释 / 非赋值行（export 前缀行已在上游拦截）
  case "$_env_s" in
    ''|*[![:space:]]*) ;;
    *) continue ;;
  esac
  case "$_env_s" in
    \#*|[[:space:]]\#*) continue ;;
    *=*) ;;
    *) continue ;;
  esac
  _env_key="${_env_s%%=*}"
  _env_key="$(printf '%s' "$_env_key" | sed -E 's/^[[:space:]]+//;s/[[:space:]]+$//')"
  # 值部分逐字符扫描：引号外的 # 即污染（引号内的 # 合法）
  _env_val="${_env_s#*=}"
  _env_q=""; _env_i=0; _env_n=${#_env_val}; _env_hit=0
  while [ "$_env_i" -lt "$_env_n" ]; do
    _env_c="${_env_val:$_env_i:1}"
    _env_i=$((_env_i + 1))
    if [ -n "$_env_q" ]; then
      [ "$_env_c" = "$_env_q" ] && _env_q=""
    else
      case "$_env_c" in
        "'"|'"') _env_q="$_env_c" ;;
        '#') _env_hit=1 ;;
      esac
    fi
  done
  if [ "$_env_hit" = 1 ]; then
    _env_polluted="${_env_polluted}  行 ${_env_lineno}：${_env_key}\n"
  fi
done < "$ENV_FILE"
if [ -n "$_env_polluted" ]; then
  echo "ERROR: $ENV_FILE 存在行尾注释污染（systemd EnvironmentFile 不剥行尾注释，服务会读到污染值）："
  printf '%b' "$_env_polluted"
  echo "请把行尾注释移到独立行；值本身若需含 # 请加引号（如 PASSWORD='a#b'），再重新运行。"
  exit 1
fi
unset _env_polluted _env_lineno _env_line _env_s _env_key _env_val _env_q _env_i _env_n _env_c _env_hit
# APP_ENV 精确值校验：模板只允许 dev|prod，其它值（手误如 production/staging）
# 会静默跑 dev 语义，同样 fail-fast。
if [ -n "$(env_val APP_ENV)" ] && [ "$(env_val APP_ENV)" != "dev" ] && [ "$(env_val APP_ENV)" != "prod" ]; then
  echo "ERROR: $ENV_FILE 中 APP_ENV=\"$(env_val APP_ENV)\" 非法，只允许 dev | prod。"
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
# R10-P2-7：/opt/stockmon/deploy 若已是真实目录（非链接），`ln -sfn` 不会替换它，
# 而是把新链接建到目录里面（/opt/stockmon/deploy/deploy），cron 仍跑旧脚本。
# 先判断：是真实目录就直接删掉重建（该路径文档约定"始终指向仓库最新脚本"，
# 里面只应是旧 deploy 脚本副本）。
if [ -e /opt/stockmon/deploy ] && [ ! -L /opt/stockmon/deploy ]; then
  echo "[deploy] /opt/stockmon/deploy 是真实目录（非软链接），清理后重建链接"
  rm -rf /opt/stockmon/deploy
fi
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
  # ===== R10-P2-1：已有证书的老机器，renewal conf 可能无 hooks =====
  # 旧版 deploy.sh（或手工 certonly）建的证书，renewal 配置里没有 pre/post/
  # deploy-hook：60-90 天后 certbot timer 自动续期时，standalone 拿证要独占
  # :80，但 nginx 常驻监听 → bind 失败 → 续期失败 → 证书到期后 HTTPS 中断。
  # 这里只做检查+提示，不自动改（certbot reconfigure 需要 certbot ≥ 2.0，
  # 老版本不支持，报错会误伤部署流程；且这是运维手工可补的一步）。
  RENEWAL_CONF="/etc/letsencrypt/renewal/${DOMAIN}.conf"
  if [ -f "$RENEWAL_CONF" ] && ! grep -qiE 'pre_hook' "$RENEWAL_CONF"; then
    echo "WARNING: $RENEWAL_CONF 里没有 pre_hook（旧版脚本/手工建的证书）。"
    echo "  60-90 天后的自动续期会因 nginx 占住 80 端口而失败（standalone 拿证需独占 :80）。"
    echo "  请手工回填 hooks（certbot ≥ 2.0）："
    echo "    certbot reconfigure --cert-name $DOMAIN \\"
    echo "      --pre-hook \"systemctl stop nginx\" \\"
    echo "      --post-hook \"systemctl start nginx\" \\"
    echo "      --deploy-hook \"systemctl reload-or-restart nginx\""
    echo "  回填后可用 certbot renew --dry-run 验证续期链路。"
  fi
  unset RENEWAL_CONF
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
# R11-P2-8：坏 venv 完整性探针——目录存在但解释器已坏（磁盘异常/手动误删）时
# 直接删掉重建，否则 pip/ruff 会报莫名其妙的错
if [ -d .venv ] && ! .venv/bin/python -c 'import sys' >/dev/null 2>&1; then
  echo "[deploy] 检测到坏 venv（.venv/bin/python 不可用），删掉重建"
  rm -rf .venv
fi
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
.venv/bin/ruff check app
echo "[deploy] 后端依赖安装 + ruff 检查通过"

# --- 前台 ---
cd "$REPO/frontend"
npm ci --no-audit --no-fund
npm run build
# R10-P2-4：原子替换 —— 先 build 到 dist.new，再 rename 两步换入（亚毫秒级），
# 避免 `rm -rf dist && cp -r` 秒级窗口期 nginx 404。
rm -rf /opt/stockmon/frontend/dist.new
cp -r dist /opt/stockmon/frontend/dist.new
[ -d /opt/stockmon/frontend/dist ] && mv /opt/stockmon/frontend/dist /opt/stockmon/frontend/dist.old
mv /opt/stockmon/frontend/dist.new /opt/stockmon/frontend/dist
rm -rf /opt/stockmon/frontend/dist.old
echo "[deploy] 前台构建部署完成"

# --- 后台 ---
cd "$REPO/admin"
npm ci --no-audit --no-fund
npm run build
# R10-P2-4：同上，原子替换
rm -rf /opt/stockmon/admin/dist.new
cp -r dist /opt/stockmon/admin/dist.new
[ -d /opt/stockmon/admin/dist ] && mv /opt/stockmon/admin/dist /opt/stockmon/admin/dist.old
mv /opt/stockmon/admin/dist.new /opt/stockmon/admin/dist
rm -rf /opt/stockmon/admin/dist.old
echo "[deploy] 后台构建部署完成"

# --- 静态文件权限（防 403 旧坑） ---
chmod -R 755 /opt/stockmon/frontend/dist /opt/stockmon/admin/dist
find /opt/stockmon/frontend/dist /opt/stockmon/admin/dist -type f -exec chmod 644 {} \;

# ===== P0-22：加载生产 .env 再 migrate（R10-P1-D1 顺序：两次 npm 构建成功之后、systemctl restart 之前） =====
# 旧顺序 migrate 在前端构建之前：npm 构建失败（npm ci 网络抖动常见）→ set -e 退出时
# DB 已是新 schema、服务仍跑旧代码，migration 非向后兼容时旧服务崩溃循环。
# 不加载的话 DATABASE_URL 会回退 config 默认值，migrate 会建野库。
# 安全注意（R5-D-N8）：`source .env` 会执行命令替换/变量展开，
# .env 里不要写 $(...)、反引号；本脚本以 root 执行，恶意内容会被执行。
# R10-P2-3：CRLF 已在校验段清洗为 LF，这里 source 到的值是干净的。
[ -f "$ENV_FILE" ] || { echo "ERROR: $ENV_FILE 不存在，无法 migrate"; exit 1; }
set -a; source "$ENV_FILE"; set +a
cd "$APP_DIR" && .venv/bin/alembic upgrade head
echo "[deploy] alembic migrate 完成"

# --- 重启服务 ---
# R11-P2-9：restart 失败直接兜底打日志诊断再退出——此前 set -e 退出时
# 只留一句命令失败，看不到 systemd 侧的真实错误
systemctl restart stockmon-api stockmon-engine || {
  echo "ERROR: systemctl restart stockmon-api stockmon-engine 失败，最近日志："
  journalctl -u stockmon-api -u stockmon-engine --no-pager -n 50 || true
  exit 1
}
# ===== R4-P1-D4：健康检查轮询最多 60 秒等引擎心跳就绪 =====
# healthz 的 status:ok 是硬编码的；引擎的心跳由独立进程写进 DB，
# sleep 3 就判大概率引擎还没启动，形同虚设。轮询等 engine=="running"。
HEALTH_OK=0
HEALTHZ=""
for _ in $(seq 1 60); do
  HEALTHZ=$(curl -sf http://127.0.0.1:8101/healthz 2>/dev/null || true)
  # R11-P2-7：JSON 空白容忍——healthz 若输出格式化 JSON（含空格/换行），
  # 旧的 '"status":"ok"' 精确匹配会漏判
  if echo "$HEALTHZ" | grep -qE '"status"[[:space:]]*:[[:space:]]*"ok"' \
    && echo "$HEALTHZ" | grep -qE '"engine"[[:space:]]*:[[:space:]]*"running"'; then
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
