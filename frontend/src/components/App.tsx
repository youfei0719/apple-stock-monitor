import { useCallback, useEffect, useState } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate, useOutletContext } from 'react-router-dom';
import { api, ApiError, isTaskExpired, type Me, type Task, type TaskStatusFilter } from '../lib/api';
import IslandStatus from './IslandStatus';

const TABS = [
  { to: '/', label: 'DING', icon: '◉' },
  { to: '/history', label: '历史', icon: '≡' },
  { to: '/add', label: '', icon: '+', fab: true },
  { to: '/guide', label: '指南', icon: '?' },
  { to: '/me', label: '我的', icon: '○' },
];

function BottomNav() {
  const location = useLocation();
  if (location.pathname === '/login' || location.pathname === '/verify') return null;
  return (
    <nav className="fixed bottom-0 left-0 right-0 z-40 bg-white/90 backdrop-blur border-t border-line safe-bottom">
      <div className="mx-auto max-w-lg grid grid-cols-5 h-[64px]">
        {TABS.map((t) =>
          t.fab ? (
            <NavLink key={t.to} to={t.to} className="flex items-center justify-center">
              <span className="w-12 h-12 -mt-6 rounded-full bg-accent text-white text-2xl shadow-card-lg flex items-center justify-center active:scale-95 transition">
                {t.icon}
              </span>
            </NavLink>
          ) : (
            <NavLink
              key={t.to}
              to={t.to}
              end={t.to === '/'}
              className={({ isActive }) =>
                `flex flex-col items-center justify-center gap-0.5 text-[11px] ${
                  isActive ? 'text-accent font-medium' : 'text-faint'
                }`
              }
            >
              <span className="text-lg leading-none">{t.icon}</span>
              <span>{t.label}</span>
            </NavLink>
          ),
        )}
      </div>
    </nav>
  );
}

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [tasks, setTasks] = useState<Task[]>([]);
  // R6-D5：App 层维护任务加载错误态（首屏 /me+tasks 加载失败 → 子页面渲染 ErrorState 而非误导成"还没有监控任务"）
  const [tasksError, setTasksError] = useState<string | null>(null);

  // R7：登录成功后刷新 /me（代替 window.location.href 全页刷新），
  // 配合 navigate('/', {state:{notice}}) 把认领提示带到首页只展示一次
  const refreshMe = useCallback(async () => {
    const m = await api.me();
    setMe(m);
  }, []);

  // R8-B0-4：退出登录时先清 App 层身份/任务状态，再调后端注销，
  // 避免后退回 /me 或 / 看到旧用户数据（旧代码只 navigate 到 /login，state 残留）
  const logout = useCallback(async () => {
    setMe(null);
    setTasks([]);
    setTasksError(null);
    await api.logout();
  }, []);

  const refreshTasks = useCallback(async (status: TaskStatusFilter = 'all') => {
    // R6-D5：异常向上抛（调用方 Home.reload 的 catch 才能感知并渲染 ErrorState），
    // 不再吞掉所有异常误导成"还没有监控任务"
    try {
      setTasks(await api.tasks(status));
      setTasksError(null);
    } catch (e) {
      setTasksError(e instanceof Error ? e.message : '任务加载失败');
      throw e;
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const m = await api.me();
        setMe(m);
        try {
          setTasks(await api.tasks());
          setTasksError(null);
        } catch (e) {
          // R6-I13：/me 成功但 /tasks 失败（500/网络抖动）→ 错误态，不是静默空列表
          setTasksError(e instanceof Error ? e.message : '任务加载失败');
        }
      } catch (e) {
        // F4：未登录不再强制跳 /login——匿名体验可用（localStorage device id + X-Device-Id，
        // 后端 tasks.py 匿名链路支持；登录/注册时后端自动认领同 device 的任务）。
        if (e instanceof ApiError && e.status === 401) {
          // 真匿名：/me 401 → 走匿名体验链路
          setMe(null);
          try {
            setTasks(await api.tasks());
            setTasksError(null);
          } catch {
            /* 匿名拉任务也失败则留空列表 */
          }
        } else {
          // R6-I13：500/网络抖动 → 错误态+重试，不冒充"匿名体验中"
          setMe(null);
          setTasksError(e instanceof Error ? e.message : '登录状态获取失败，请重试');
        }
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 有货时点击灵动岛 → 跳到该任务详情页（D9）
  // hotTask 已是第一个有货且未暂停未过期的任务
  const hotTask = tasks.find(
    (t) => !t.paused && !isTaskExpired(t) && (t.latest?.available_count ?? 0) > 0,
  );
  // R7：口径与后端 GET /tasks?status=active 对齐（tasks.py：status=active 只剔除 _is_expired
  // 为 True 的，而 _is_expired 在 task.paused 时直接返回 False——paused 任务同样出现在
  // "监控中" tab 里）。前端 isTaskExpired 与后端 _is_expired 同形（paused 优先于 expired），
  // 因此灵动岛计数 = paused || !expired，不再剔除 paused 任务
  const activeCount = tasks.filter((t) => t.paused || !isTaskExpired(t)).length;

  if (me === undefined) {
    return (
      <div className="min-h-screen bg-bg flex items-center justify-center">
        <div className="w-10 h-10 rounded-full bg-island flex items-center justify-center">
          <span className="w-3 h-3 rounded-full bg-ok island-dot" />
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-bg text-ink font-sans">
      {location.pathname !== '/login' && location.pathname !== '/verify' && (
        <IslandStatus
          taskCount={activeCount}
          hasStock={!!hotTask}
          // R6-D5：refreshTasks 会抛异常，灵动岛点开只做静默刷新（失败不打断页面），
          // 页面内错误态走 Home 的 ErrorState + 重试
          onTap={() => {
            refreshTasks().catch(() => {
              /* 静默刷新失败：Home 已通过 tasksError 渲染错误态 */
            });
          }}
          onStockTap={hotTask ? () => navigate(`/tasks/${hotTask.id}`) : undefined}
        />
      )}
      {/* F4：匿名体验横幅——trial 档仅限未登录匿名体验（后端 tiers.py），
          注册/登录后同 device 的匿名任务自动迁移到账号下（auth.py _claim_device_tasks）；
          R6-I13：tasksError 时（/me 500/网络抖动）不冒充"匿名体验中"，错误态交给 Home 的 ErrorState */}
      {me === null &&
        tasksError === null &&
        location.pathname !== '/login' &&
        location.pathname !== '/verify' && (
          <div className="mx-auto max-w-lg px-4 pt-3">
            <div className="rounded-card-sm bg-island text-white px-4 py-2.5 text-[13px] flex items-center justify-between gap-3 rise-in">
              <span>
                匿名体验中：可创建 1 个任务 · 每月 1 次推送
              </span>
              <Link to="/login" className="shrink-0 underline font-medium">
                注册 / 登录
              </Link>
            </div>
          </div>
        )}
      <main className="mx-auto max-w-lg pb-24">
        <Outlet context={{ me, tasks, refreshTasks, tasksError, refreshMe, logout }} />
        {location.pathname !== '/login' && location.pathname !== '/verify' && (
          <footer className="px-4 pt-2 pb-6 text-center text-[11px] text-faint">
            {/* R9-I7：全站显式按北京时间渲染（UTC+8），不走设备本地时区 */}
            页面内所有时间为北京时间（UTC+8）
          </footer>
        )}
      </main>
      <BottomNav />
    </div>
  );
}

export interface AppContext {
  me: Me | null;
  tasks: Task[];
  refreshTasks: (status?: TaskStatusFilter) => Promise<void>;
  /** R6-D5：App 层维护的任务加载错误态（null = 无错误） */
  tasksError: string | null;
  /** R7：重新拉取 /me（登录成功后刷新身份，代替全页刷新） */
  refreshMe: () => Promise<void>;
  /** R8-B0-4：退出登录——先清 App 层 me/tasks state，再调后端 /auth/logout */
  logout: () => Promise<void>;
}

export function useApp(): AppContext {
  return useOutletContext<AppContext>();
}
