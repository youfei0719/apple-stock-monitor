import { useCallback, useEffect, useState } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate, useOutletContext } from 'react-router-dom';
import { api, isTaskExpired, type Me, type Task, type TaskStatusFilter } from '../lib/api';
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

  const refreshTasks = useCallback(async (status: TaskStatusFilter = 'all') => {
    try {
      setTasks(await api.tasks(status));
    } catch {
      /* 未登录时 /tasks 会 401，忽略 */
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const m = await api.me();
        setMe(m);
        setTasks(await api.tasks());
      } catch {
        // F4：未登录不再强制跳 /login——匿名体验可用（localStorage device id + X-Device-Id，
        // 后端 tasks.py 匿名链路支持；登录/注册时后端自动认领同 device 的任务）。
        setMe(null);
        try {
          setTasks(await api.tasks());
        } catch {
          /* 匿名拉任务也失败则留空列表 */
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
  const activeCount = tasks.filter((t) => !t.paused && !isTaskExpired(t)).length;

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
          onTap={refreshTasks}
          onStockTap={hotTask ? () => navigate(`/tasks/${hotTask.id}`) : undefined}
        />
      )}
      {/* F4：匿名体验横幅——trial 档仅限未登录匿名体验（后端 tiers.py），
          注册/登录后同 device 的匿名任务自动迁移到账号下（auth.py _claim_device_tasks） */}
      {me === null &&
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
        <Outlet context={{ me, tasks, refreshTasks }} />
        {location.pathname !== '/login' && location.pathname !== '/verify' && (
          <footer className="px-4 pt-2 pb-6 text-center text-[11px] text-faint">
            页面内所有时间为本地时间
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
}

export function useApp(): AppContext {
  return useOutletContext<AppContext>();
}
