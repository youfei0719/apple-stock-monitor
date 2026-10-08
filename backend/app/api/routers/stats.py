"""查询统计：上次查询 / 成功率 / 平均响应（按用户任务聚合）。"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models.models import MonitorTask, User
from app.services.engine import engine

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/poll")
def poll_stats(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    tasks = db.execute(select(MonitorTask).where(MonitorTask.user_id == user.id)).scalars().all()
    polled = [t for t in tasks if t.last_polled_at]
    ok_n = sum(1 for t in polled if t.last_poll_ok)
    ms_vals = [t.last_poll_ms for t in polled if t.last_poll_ms]
    return {
        "tasks": len(tasks),
        "polled_tasks": len(polled),
        "success_rate": round(ok_n / len(polled), 4) if polled else None,
        "avg_response_ms": round(sum(ms_vals) / len(ms_vals), 1) if ms_vals else None,
        "last_poll_at": max((t.last_polled_at for t in polled), default=None),
        "engine": engine.status(),
    }
