"""内存限流：登录失败锁 IP、公开接口 60 req/min/IP。"""

import time
from collections import defaultdict

from app.core.config import get_settings

_settings = get_settings()

# ip -> {"fails": int, "lock_until": float}
_login_state: dict[str, dict] = {}
# ip -> [timestamps]
_hit_windows: dict[str, list[float]] = defaultdict(list)


def _now() -> float:
    return time.time()


def login_locked(ip: str) -> float:
    """返回剩余锁定秒数，0 表示未锁定。"""
    st = _login_state.get(ip)
    if not st:
        return 0.0
    lock_until = st.get("lock_until", 0)
    if not lock_until:
        return 0.0  # 从未被锁定：不能清掉累计的失败次数
    remaining = lock_until - _now()
    if remaining <= 0:
        _login_state.pop(ip, None)
        return 0.0
    return remaining


def record_login_failure(ip: str) -> int:
    st = _login_state.setdefault(ip, {"fails": 0, "lock_until": 0.0})
    st["fails"] += 1
    if st["fails"] >= _settings.LOGIN_FAIL_LOCK:
        st["lock_until"] = _now() + _settings.LOGIN_LOCK_MINUTES * 60
    return st["fails"]


def record_login_success(ip: str) -> None:
    _login_state.pop(ip, None)


def check_rate_limit(ip: str, limit: int = 60, window_sec: int = 60) -> bool:
    """True=允许，False=超限。"""
    now = _now()
    win = _hit_windows[ip]
    cutoff = now - window_sec
    while win and win[0] < cutoff:
        win.pop(0)
    if len(win) >= limit:
        return False
    win.append(now)
    # 防止内存无限增长：定期清理空闲 ip
    if len(_hit_windows) > 10000:
        for k in [k for k, v in _hit_windows.items() if not v or v[-1] < cutoff]:
            _hit_windows.pop(k, None)
    return True
