# 库存监控后台（admin）

基于 [shadcn-admin](https://github.com/satnaing/shadcn-admin)（MIT，commit
`e16c87f213a5ba5e45964d9b67c792105ec74d26`，v2.2.1）裁剪定制。

主题：**A·零售式明亮克制**（Apple 零售风）
- 主色 `#0071e3`（Apple 蓝），浅底 `#f5f5f7`，卡片纯白，大圆角（`--radius: 1rem`）
- 正文：系统字体栈（`-apple-system / PingFang SC / …`），无外部字体 CDN
- 数字/金额：Paper Mono（OFL，本地 `src/assets/fonts/PaperMono-Variable.woff2`
  经 `@font-face` 引入；加载失败自动 fallback `ui-monospace`）
- 设计语言参考用户 glint-vault 收藏的 Dashboard 设计系统卡片（BoardUI）

## 页面（全部对接 `docs/API_CONTRACT.md` 的 `/api/admin/*`）

| 路由 | 页面 | 接口 |
|---|---|---|
| `/` | 总览：KPI（用户数/会员分布/今日推送/收入） | `GET /api/admin/overview` |
| `/traffic` | 流量：PV/UV 日粒度折线（Recharts） | `GET /api/admin/traffic?days=` |
| `/members` | 会员：搜索/tier 筛选/改级（二次确认） | `GET /api/admin/users`，`PATCH /api/admin/users/{id}` |
| `/payments` | 付费：爱发电订单记录 | `GET /api/admin/payments` |
| `/system` | 系统：引擎/队列/限流/日志 tail/审计 | `GET /api/admin/system`，`GET /api/admin/audit` |

登录：`/sign-in`，密码 + TOTP（校验全在后端）；失败计数 + 限流提示
（连续 5 次失败锁 IP 15 分钟，按契约）；未登录访问后台路由自动跳登录页。

## Mock / 真实切换

`src/lib/admin-api.ts` 顶部 `USE_MOCK`：
- `true`（当前）：全部走内置 mock 数据，页面可独立跑通
- `false`：走同源 `/api/admin/*`（`VITE_API_BASE` 可配开发后端地址），
  会话靠 HttpOnly Cookie，前端不存任何 token/密钥

TODO（后端联调）：会员改级接口契约待后端确认（当前假设
`PATCH /api/admin/users/{id}` `{tier}`）；mock 下验证码填 `000000`
可模拟登录失败，用于验证限流提示 UI。

## 构建与部署

```bash
npm ci && npm run build   # 产物在 admin/dist
```

- `vite.config.ts` 的 `base: '/admin/'` + router `basepath: '/admin'`：
  构建产物为独立目录，部署时挂到 `/admin/` 路径即可
- `verify/`：dev 模式 mock 数据渲染截图（10/10 自动化检查通过）
