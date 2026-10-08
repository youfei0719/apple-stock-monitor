# Apple 直营店库存监控 SaaS

前台（用户监控+通知）+ 后台（流量/会员/付费/系统状态）+ 付费墙，部署于 `stock.glint.red`。

## 目录结构

```
backend/    FastAPI + SQLAlchemy + Alembic（API + 监控引擎 + 通知）
frontend/   React + Vite + Tailwind（A·零售式明亮克制）
admin/      Shadcn Admin（A 风格主题重制）
deploy/     systemd / nginx / 部署脚本 / 备份脚本
docs/       API 契约、运维手册
```

## 本地开发

```bash
cp .env.example .env            # 填入本地值
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload   # http://localhost:8000 ，文档 /docs
cd ../frontend && npm ci && npm run dev
```

## 部署

见 `docs/运维手册.md`；一键重部署：在服务器上以 root 执行 `/opt/stockmon/deploy/deploy.sh`（bootstrap + systemd + nginx + 备份 cron 全自动）。

## 工程规范（验收线）

- 提交信息：`feat|fix|docs|chore|refactor|test:` 前缀
- 后端 `ruff check` 通过；依赖 pinned（`requirements.txt` / `package-lock.json` 入库）
- 密钥/阈值/开关全部走 `.env`，`.env.example` 保持同步；硬编码密钥视为缺陷
- schema 变更必须走 Alembic migration，不许手动改库
- 关键路径（轮询/通知/支付回调）结构化日志落盘+轮转
- `/healthz` 健康检查；`deploy/backup.sh` 每日备份用户数据
