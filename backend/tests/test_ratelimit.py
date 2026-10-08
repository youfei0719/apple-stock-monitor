"""登录失败锁 IP：失败计数不能在未锁定时被清掉（回归测试）。"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import ratelimit
from app.core.config import get_settings


def _fresh_ip(tag: str) -> str:
    return f"10.99.0.{abs(hash(tag)) % 250 + 1}"


def test_five_failures_lock_ip():
    settings = get_settings()
    ip = _fresh_ip("lock")
    for _ in range(settings.LOGIN_FAIL_LOCK):
        ratelimit.record_login_failure(ip)
    assert ratelimit.login_locked(ip) > 0


def test_failures_accumulate_without_premature_clear():
    # 回归：login_locked 在未锁定时不能清掉已累计的失败次数
    settings = get_settings()
    ip = _fresh_ip("accumulate")
    for _ in range(settings.LOGIN_FAIL_LOCK - 1):
        assert ratelimit.login_locked(ip) == 0
        ratelimit.record_login_failure(ip)
    assert ratelimit.login_locked(ip) == 0  # 还差 1 次，不应锁
    ratelimit.record_login_failure(ip)
    assert ratelimit.login_locked(ip) > 0  # 第 5 次后锁定


def test_success_resets():
    ip = _fresh_ip("reset")
    ratelimit.record_login_failure(ip)
    ratelimit.record_login_success(ip)
    assert ratelimit.login_locked(ip) == 0
