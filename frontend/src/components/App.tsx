import { useCallback, useEffect, useState, type MouseEvent } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate, useOutletContext } from 'react-router-dom';
import { api, ApiError, isTaskExpired, type Me, type Task, type TaskStatusFilter } from '../lib/api';
import IslandStatus from './IslandStatus';

const TABS = [
  { to: '/', label: '监控', icon: '◉' },
  { to: '/history', label: '历史', icon: '≡' },
  { to: '/add', label: '', icon: '+', fab: true },
  { to: '/guide', label: '指南', icon: 'ⓘ' },
  { to: '/me', label: '我的', icon: '○' },
];

/** R10-I2：App 层记住"曾经有会话"（localStorage 标记位）——区分"真匿名"与
 * "会话页内过期"。曾经登录过但 /me 401 时，渲染"登录已过期"横幅而非"匿名体验"横幅。 */
const HAD_SESSION_KEY = 'stockmon.had_session';

function readHadSession(): boolean {
  try {
    return localStorage.getItem(HAD_SESSION_KEY) === '1';
  } catch {
    return false;
  }
}

function writeHadSession(had: boolean) {
  try {
    if (had) localStorage.setItem(HAD_SESSION_KEY, '1');
    else localStorage.removeItem(HAD_SESSION_KEY);
  } catch {
    /* 存储不可用则退化为真匿名 */
  }
}

function BottomNav() {
  const location = useLocation();
  if (location.pathname === '/login' || location.pathname === '/verify') return null;
  // UX：已在当前 tab 时再点一次不做任何事——避免重复导航导致页面重载、
  // /add 已选的机型/门店/搜索词被清空（操作丢失）
  const noopIfActive = (to: string) => (e: MouseEvent) => {
    if (location.pathname === to) e.preventDefault();
  };
  return (
    <nav className="fixed bottom-0 left-0 right-0 z-40 bg-white/90 backdrop-blur border-t border-line safe-bottom">
      <div className="mx-auto max-w-lg grid grid-cols-5 h-[64px]">
        {TABS.map((t) =>
          t.fab ? (
            <NavLink key={t.to} to={t.to} onClick={noopIfActive(t.to)} className="flex items-center justify-center">
              <span className="w-12 h-12 -mt-6 rounded-full bg-accent text-white text-2xl shadow-card-lg flex items-center justify-center active:scale-95 transition">
                {t.icon}
              </span>
            </NavLink>
          ) : (
            <NavLink
              key={t.to}
              to={t.to}
              end={t.to === '/'}
              onClick={noopIfActive(t.to)}
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
  // R10-I2：会话页内过期标记（曾经有会话但 /me 401）——渲染"登录已过期"横幅，
  // 不与"匿名体验"横幅混淆
  const [sessionExpired, setSessionExpired] = useState(false);
  // R10-P2-2：灵动岛计数专用列表——独立于当前 tab 的过滤列表维护，
  // 否则切到"已过期" tab 时"监控中 · N 个任务"失真
  const [activeTasks, setActiveTasks] = useState<Task[]>([]);

  // R7：登录成功后刷新 /me（代替 window.location.href 全页刷新），
  // 配合 navigate('/', {state:{notice}}) 把认领提示带到首页只展示一次
  const refreshMe = useCallback(async () => {
    const m = await api.me();
    setMe(m);
    if (m !== null) {
      // R10-I2：拿到身份即记"曾经有会话"
      writeHadSession(true);
      setSessionExpired(false);
    }
  }, []);

  // R8-B0-4：退出登录时先清 App 层身份/任务状态，再调后端注销，
  // 避免后退回 /me 或 / 看到旧用户数据（旧代码只 navigate 到 /login，state 残留）
  const logout = useCallback(async () => {
    setMe(null);
    setTasks([]);
    setActiveTasks([]);
    setTasksError(null);
    // R10-I2：主动退出 → 回到真匿名，不再弹"登录已过期"
    writeHadSession(false);
    setSessionExpired(false);
    await api.logout();
  }, []);

  // R10-P2-2：灵动岛计数走独立的 status=active 拉取，不随 tab 过滤；失败静默降级用旧值
  const refreshActiveTasks = useCallback(async () => {
    try {
      setActiveTasks(await api.tasks('active'));
    } catch {
      /* 灵动岛计数刷新失败：保留旧值，不打断页面 */
    }
  }, []);

  const refreshTasks = useCallback(
    async (status: TaskStatusFilter = 'all') => {
      // R6-D5：异常向上抛（调用方 Home.reload 的 catch 才能感知并渲染 ErrorState），
      // 不再吞掉所有异常误导成"还没有监控任务"
      try {
        const list = await api.tasks(status);
        setTasks(list);
        setTasksError(null);
        // R10-P2-2：tab 列表刷新时同步维护灵动岛计数专用列表
        if (status === 'active') setActiveTasks(list);
        else void refreshActiveTasks();
      } catch (e) {
        setTasksError(e instanceof Error ? e.message : '任务加载失败');
        throw e;
      }
    },
    [refreshActiveTasks],
  );

  useEffect(() => {
    (async () => {
      try {
        const m = await api.me();
        setMe(m);
        // R10-I2：拿到身份即记"曾经有会话"
        writeHadSession(true);
        setSessionExpired(false);
        try {
          setTasks(await api.tasks());
          setTasksError(null);
        } catch (e) {
          // R6-I13：/me 成功但 /tasks 失败（500/网络抖动）→ 错误态，不是静默空列表
          setTasksError(e instanceof Error ? e.message : '任务加载失败');
        }
        // R10-P2-2：首屏即拉一份独立的 active 列表供灵动岛计数
        void refreshActiveTasks();
      } catch (e) {
        // F4：未登录不再强制跳 /login——匿名体验可用（localStorage device id + X-Device-Id，
        // 后端 tasks.py 匿名链路支持；登录/注册时后端自动认领同 device 的任务）。
        if (e instanceof ApiError && e.status === 401) {
          // R10-I2：401 时查"曾经有会话"标记——有则渲染"登录已过期"横幅，
          // 别混淆成"匿名体验中"（如别处改密被登出）
          setSessionExpired(readHadSession());
          setMe(null);
          try {
            setTasks(await api.tasks());
            setTasksError(null);
          } catch {
            /* 匿名拉任务也失败则留空列表 */
          }
          void refreshActiveTasks();
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
  // R10-P2-2：hotTask 也走灵动岛专用 active 列表，不随 tab 过滤失真
  const hotTask = activeTasks.find(
    (t) => !t.paused && !isTaskExpired(t) && (t.latest?.available_count ?? 0) > 0,
  );
  // R7：口径与后端 GET /tasks?status=active 对齐（tasks.py：status=active 只剔除 _is_expired
  // 为 True 的，而 _is_expired 在 task.paused 时直接返回 False——paused 任务同样出现在
  // "监控中" tab 里）。前端 isTaskExpired 与后端 _is_expired 同形（paused 优先于 expired），
  // 因此灵动岛计数 = paused || !expired，不再剔除 paused 任务
  // R10-P2-2：计数走独立的 activeTasks 列表，不随 Home tab 过滤变化
  const activeCount = activeTasks.filter((t) => t.paused || !isTaskExpired(t)).length;

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
          R6-I13：tasksError 时（/me 500/网络抖动）不冒充"匿名体验中"，错误态交给 Home 的 ErrorState；
          R10-I2：曾经有会话但页内过期（sessionExpired）→ 渲染"登录已过期"横幅，
          不混淆成"匿名体验中"（如别处改密被登出） */}
      {me === null &&
        tasksError === null &&
        location.pathname !== '/login' &&
        location.pathname !== '/verify' && (
          /* UX：pt-16 给顶部 fixed 灵动岛让位（岛高 40px + mt-12px），避免胶囊条
             与"暂无监控任务" pill 重叠遮挡；阴影落在浅色背景上不再显脏 */
          <div className="mx-auto max-w-lg px-4 pt-16">
            {sessionExpired ? (
              <div className="rounded-card-sm bg-island text-white px-4 py-2.5 text-[13px] flex items-center justify-between gap-3 rise-in">
                <span>登录已过期，请重新登录</span>
                <Link to="/login" className="shrink-0 underline font-medium">
                  重新登录
                </Link>
              </div>
            ) : (
              <div className="rounded-card-sm bg-island text-white px-4 py-2.5 text-[13px] flex items-center justify-between gap-3 rise-in">
                <span>匿名体验中：可创建 1 个任务 · 每月 1 次推送</span>
                <Link to="/login" className="shrink-0 underline font-medium">
                  注册 / 登录
                </Link>
              </div>
            )}
          </div>
        )}
      <main className="mx-auto max-w-lg pb-24">
        <Outlet context={{ me, tasks, refreshTasks, tasksError, refreshMe, logout, sessionExpired }} />
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
  /** R10-I2：曾经有会话但页内过期（/me 401）——子页面可据此渲染"重新登录"引导，
   * 而不是把 401 误导成匿名体验 */
  sessionExpired: boolean;
}

export function useApp(): AppContext {
  return useOutletContext<AppContext>();
}
