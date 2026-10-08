"""引擎边沿触发单元测试：evaluate_transition 纯函数。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.engine import evaluate_transition


def test_unavailable_to_available_instant_triggers():
    prev, notify, cc = evaluate_transition("unavailable", "available", 0, "instant")
    assert notify is True
    assert prev == "available"
    assert cc == 1


def test_sustained_available_no_repeat():
    prev, notify, cc = evaluate_transition("available", "available", 3, "instant")
    assert notify is False
    assert prev == "available"
    assert cc == 4  # 连续计数继续累加


def test_unknown_never_triggers_and_keeps_baseline():
    # 失败 -> unknown：不触发，prev_known 保持 unavailable，计数清零
    prev, notify, cc = evaluate_transition("unavailable", "unknown", 1, "instant")
    assert notify is False
    assert prev == "unavailable"
    assert cc == 0


def test_unknown_does_not_fake_available_edge():
    # unknown 之后恢复 available，应视为边沿（prev 仍是 unavailable）-> 触发
    prev1, n1, cc1 = evaluate_transition("unavailable", "unknown", 0, "instant")
    assert n1 is False
    prev2, n2, cc2 = evaluate_transition(prev1, "available", cc1, "instant")
    assert n2 is True
    assert prev2 == "available"


def test_first_seen_available_is_silent_baseline():
    prev, notify, cc = evaluate_transition(None, "available", 0, "instant")
    assert notify is False
    assert prev == "available"


def test_confirmed_mode_needs_two_rounds():
    # 第 1 轮 available：不通知
    prev, notify, cc = evaluate_transition("unavailable", "available", 0, "confirmed")
    assert notify is False
    assert cc == 1
    # 第 2 轮 available：通知
    prev, notify, cc = evaluate_transition(prev, "available", cc, "confirmed")
    assert notify is True
    assert cc == 2


def test_confirmed_mode_broken_by_unavailable():
    prev, notify, cc = evaluate_transition("unavailable", "available", 0, "confirmed")
    assert notify is False
    prev, notify, cc = evaluate_transition(prev, "unavailable", cc, "confirmed")
    assert notify is False
    assert cc == 0
    assert prev == "unavailable"
    # 重新开始计数
    prev, notify, cc = evaluate_transition(prev, "available", cc, "confirmed")
    assert notify is False
    assert cc == 1


def test_available_to_unavailable_resets():
    prev, notify, cc = evaluate_transition("available", "unavailable", 5, "instant")
    assert notify is False
    assert prev == "unavailable"
    assert cc == 0
