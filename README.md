# Apple 直营店库存监控 SaaS

前台（用户监控+通知）+ 后台（流量/会员/付费/系统状态）+ 付费墙，部署于 `stock.glint.red`。

## 功能（19 项，用户 2026-10-09 拍板）

1. 机型/容量/颜色/门店多维选择
2. 全国门店目录在线刷新
3. 门店×型号批量生成
4. iPhone/iPad/Mac/Watch 四品类（一次开发一起开放）
5. part number 专家模式
6. 监控分组/改名/有效期
7. 六态库存状态（available/unavailable/unknown/verifying/cooling/paused）
8. 合并请求/抖动/冷却（Apple 反限流）
9. 查询统计
10. 代理池轮换（只支持 `http(s)://`；Clash 本地 HTTP 代理地址填入 PROXY_POOL 即可）
11. Bark 推送
12. 企微/钉钉/飞书/邮件通知
13. 可选即时/连续确认触发 + 可配重复间隔
14. 通知链路测试
15. 通知直达到货 SKU 页/购物袋
16. 活动日志与有货历史
17. 放货记录/数据分析/全国榜单
18. 到货购买指南
19. 会员期限/刷新频率/历史数据分级（标准 ¥19/月、Pro ¥39/月；爱发电自动回调开通）

> 注：原第 20 项"预计送货日期"已正式砍掉（用户 2026-10-09 02:28 亲口确认）。砍掉依据：生产走的 Apple 自提库存接口（pickup-message）200 响应里根本没有送货日期字段；另一路配送接口（fulfillment-messages）长期 541 不可依赖。调研结论见 `docs/reviews/delivery-date-plan.md`。

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
cd backend                       # 先进 backend：config.py 按进程 CWD 读 backend/.env，
cp ../.env.example .env          # 根目录那份不会被读到；.env 必须放在 backend/ 下
python -m venv .venv && source .venv/bin/activate
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
