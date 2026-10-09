"""监控任务 CRUD + 批量生成 + 状态查询。

匿名体验：无会话时可用 X-Device-Id 创建 1 个任务（不计配额）。
"""

import hashlib
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import _client_ip, get_current_user, get_optional_user
from app.api.errors import APIError
from app.core.db import get_db
from app.core.logging import get_logger
from app.core.ratelimit import check_rate_limit
from app.core.tiers import effective_tier, effective_tier_of, tier_of
from app.core.timeutil import as_naive_utc as _as_naive_utc
from app.core.timeutil import utcnow
from app.models.models import IdempotencyRecord, MonitorTask, StockState, User
from app.schemas import TaskBatchIn, TaskCreateIn, TaskOut, TaskPatchIn
from app.services.lifecycle import null_notification_task_ids, purge_task_idempotency_records

router = APIRouter(prefix="/tasks", tags=["tasks"])
log = get_logger("tasks")

DISPLAY_STATE_ORDER = (
    "available",
    "unavailable",
    "unknown",
    "verifying",
    "cooling",
    "paused",
    "expired",
)

# 匿名体验任务最长存活 24h（服务端强制，不信任客户端传的 expires_at）
TRIAL_MAX_TTL = timedelta(hours=24)
# 任务创建接口 IP 维度限流：20 次/小时/IP
TASK_CREATE_LIMIT = 20
TASK_CREATE_WINDOW_SEC = 3600
# R9-O2：批量创建独立 IP 限流（第二层，档位 tasks_limit 之外；单次批量可建
# 多个任务，配额比单建高）
BATCH_CREATE_LIMIT = 10
BATCH_CREATE_WINDOW_SEC = 3600
# R10-P2-7：续期并发双击去重窗口（秒）——窗口内重复点击视为重复提交，
# 直接返回当前状态不再叠加 +30 天（保守方向：双击只 +30 天不叠加）
RENEW_DEDUP_WINDOW_SEC = 10


# R8-B0-3：归一化实现已提升到 app.core.timeutil（公共模块），此处保留
# _as_naive_utc 别名以兼容既有调用点与回归测试。

def _clamp_expires(expires_at: datetime | None, anonymous: bool) -> datetime | None:
    """匿名任务强制 24h 过期：为空或超过 24h 时一律 clamp 到 now+24h。"""
    if not anonymous:
        return _as_naive_utc(expires_at)
    expires_at = _as_naive_utc(expires_at)
    cap = utcnow() + TRIAL_MAX_TTL
    if expires_at is None or expires_at > cap:
        return cap
    return expires_at


def _is_expired(task: MonitorTask, now: datetime | None = None) -> bool:
    """任务是否已过期：expires_at < now 且未手动暂停（断裂-7）。"""
    if task.paused:
        return False
    if task.expires_at is None:
        return False
    return task.expires_at < (now or utcnow())


def _display_state(task: MonitorTask, row: StockState | None) -> str:
    if task.paused:
        return "paused"
    if _is_expired(task):
        return "expired"
    if row is None:
        return "unknown"
    if row.state == "cooling":
        return "cooling"
    if row.state == "available" and task.mode == "confirmed" and row.confirmed_count < 2:
        return "verifying"
    return row.state if row.state in DISPLAY_STATE_ORDER else "unknown"


def _task_out(task: MonitorTask, db: Session) -> TaskOut:
    # R4-P1-B5：list_tasks 用 selectinload 预加载 states，这里直接读关系，
    # 其他入口（create/get）走懒加载，不再每任务一次 StockState 查询
    rows = task.states
    by_store: dict[str, dict] = {}
    for r in rows:
        by_store.setdefault(r.store_number, {})[r.part_number] = {
            "state": _display_state(task, r),
            "pickup_display": r.pickup_display,
            "store_pick_eligible": r.store_pick_eligible,
            "pickup_search_quote": r.pickup_search_quote,
            "updated_at": r.updated_at.isoformat() + "Z" if r.updated_at else None,
        }
    available_n = sum(1 for r in rows if _display_state(task, r) == "available")
    return TaskOut(
        id=task.id,
        name=task.name,
        group=task.group,
        category=task.category,
        part_number=task.part_number,
        product_name=task.product_name,
        color=task.color,
        capacity=task.capacity,
        stores=task.stores or [],
        mode=task.mode,
        repeat_interval_sec=task.repeat_interval_sec,
        channels=task.channels or {},
        paused=task.paused,
        expires_at=task.expires_at,
        auto_retire=getattr(task, "auto_retire", True),
        created_at=task.created_at,
        latest={"stores": by_store, "available_count": available_n, "total": len(rows)},
    )


def _check_task_limit(db: Session, user: User | None, device_id: str | None) -> None:
    if user:
        # 过期付费档按 free 算（断裂-1），走 effective_tier
        tier = effective_tier_of(user)
        n = db.execute(
            select(func.count()).select_from(MonitorTask).where(MonitorTask.user_id == user.id)
        ).scalar()
        if n >= tier["tasks_limit"]:
            raise APIError(403, f"任务数已达上限（{tier['tasks_limit']}）", "task_limit")
    else:
        n = db.execute(
            select(func.count()).select_from(MonitorTask).where(MonitorTask.device_id == device_id)
        ).scalar()
        if n >= tier_of("trial")["tasks_limit"]:
            raise APIError(403, "体验版仅可创建 1 个任务，请登录后使用", "task_limit_trial")


def _conflict_key(task: MonitorTask) -> tuple[str, frozenset]:
    """任务的归一化冲突键：(part_number 大写去空格, 门店号集合)。

    N7：两侧门店号都做 strip().upper() 归一化再比较，大小写/空格不一致
    不产生重复任务。
    """
    return (
        (task.part_number or "").strip().upper(),
        frozenset((s or "").strip().upper() for s in (task.store_numbers or [])),
    )


def _find_conflict_in(
    tasks: list[MonitorTask], part_number: str, store_numbers: list[str]
) -> MonitorTask | None:
    """R10-P1-2：纯内存冲突比对（调用方先单次查询拿到候选任务，再逐条比对）。

    归一化口径与 _find_conflict 一致。
    """
    want = frozenset((s or "").strip().upper() for s in store_numbers)
    part_norm = (part_number or "").strip().upper()
    for t in tasks:
        p, have = _conflict_key(t)
        if p == part_norm and have == want:
            return t
    return None


def _find_conflict(
    db: Session,
    user_id: int | None,
    device_id: str | None,
    part_number: str,
    store_numbers: list[str],
) -> MonitorTask | None:
    """断裂-8：按 (归属, part_number, 门店集合) 查重，避免重复任务重复通知/扣配额。

    N7：两侧门店号都做 strip().upper() 归一化再比较，大小写/空格不一致不产生重复任务。
    """
    q = select(MonitorTask)
    if user_id is not None:
        q = q.where(MonitorTask.user_id == user_id)
    else:
        q = q.where(MonitorTask.device_id == device_id, MonitorTask.user_id.is_(None))
    return _find_conflict_in(db.execute(q).scalars().all(), part_number, store_numbers)


def _conflict_error(task: MonitorTask) -> APIError:
    return APIError(
        409,
        f"已存在相同监控任务（id={task.id}）：同型号 {task.part_number} + 同门店组合",
        "task_conflict",
    )


def _validate_task_in(data: TaskCreateIn) -> None:
    if data.mode not in ("instant", "confirmed"):
        raise APIError(400, "mode 必须为 instant 或 confirmed", "bad_mode")
    if data.category not in ("iphone", "ipad", "mac", "watch"):
        raise APIError(400, "category 非法", "bad_category")


def _validate_expires_at(expires_at: datetime | None) -> None:
    """R6-P2-12：expires_at 早于当前时间直接 400（过期任务建了也无意义）。

    R7 断裂 后-D-1：先归一化为 naive UTC 再比较（pydantic 解析出的 "…Z"
    是 tz-aware，与 utcnow() 直接比会 TypeError→500）。"""
    expires_at = _as_naive_utc(expires_at)
    if expires_at is not None and expires_at < utcnow():
        raise APIError(400, "expires_at 不能早于当前时间", "bad_expires_at")


def _channels_empty(channels: dict | None) -> bool:
    """渠道是否全空：无 bark_key/email 且 webhooks 为空。"""
    ch = channels or {}
    if (ch.get("bark_key") or "").strip() or (ch.get("email") or "").strip():
        return False
    webhooks = ch.get("webhooks") or []
    return not any(
        isinstance(w, dict) and (w.get("url") or "").strip() for w in webhooks
    )


def _fill_verified_email(channels: dict, user: User | None) -> dict:
    """P0：已验证邮箱自动算作邮件渠道。

    用户注册时已验证邮箱，逻辑上邮箱就是可用渠道——不再要求为每个任务
    重复填写一遍。仅当未显式配置 email 时回填用户已验证邮箱；显式填写的
    优先。匿名（user=None，走 trial 站内触达）不受影响。
    回填发生在入库前，因此 notifier 能真实发到该邮箱，且 _require_channels
    的空渠道检查自然通过（email 在 free/standard/pro 档位均开放）。
    """
    ch = dict(channels or {})
    if not (ch.get("email") or "").strip() and user is not None:
        if getattr(user, "email_verified", False) and (user.email or "").strip():
            ch["email"] = user.email.strip()
    return ch


# 后-D-2：渠道中文名映射（与 notify.py /notify/test 的 _names 口径一致，
# 另补 wecom/dingtalk/feishu 三个群机器人平台）
_CHANNEL_NAMES = {
    "page": "站内",
    "email": "邮件",
    "bark": "Bark",
    "sms": "短信",
    "wecom": "企微",
    "dingtalk": "钉钉",
    "feishu": "飞书",
}


def _require_channels(channels: dict, user: User | None) -> None:
    """后-D-2：按档位校验通知渠道——tiers.py 各档 channels 是唯一真源：
    trial=["page"]，free/standard/pro=["email"]，没有任何档位支持
    bark/webhook/sms。配了当前档位不支持的渠道 → 400 明示（配了但永远
    不响是误导）。保留空渠道检查：非 trial 必须配至少一个渠道，否则空
    渠道的到货边沿会被静默消费（零通知零记录），trial 走站内 page 触达
    不要求外部渠道。顺序：先做分渠道校验，再做空检查。"""
    tier = effective_tier_of(user)  # user 可为 None → trial
    allowed = tier["channels"]
    ch = channels or {}
    configured: list[str] = []
    if (ch.get("bark_key") or "").strip():
        configured.append("bark")
    if (ch.get("email") or "").strip():
        configured.append("email")
    for w in ch.get("webhooks") or []:
        if isinstance(w, dict) and (w.get("url") or "").strip():
            platform = w.get("platform")
            if platform and platform not in configured:
                configured.append(platform)
    for c in configured:
        if c not in allowed:
            allowed_names = "、".join(_CHANNEL_NAMES.get(a, a) for a in allowed)
            raise APIError(
                400,
                f"当前档位（{tier['name']}）不支持该渠道（{_CHANNEL_NAMES.get(c, c)}）；"
                f"当前档位仅支持{allowed_names}通知",
                "channel_not_supported",
            )
    if _channels_empty(channels) and effective_tier(user) != "trial":
        raise APIError(
            400,
            "请至少配置一个通知渠道（邮箱），否则到货时无法通知你",
            "channels_required",
        )


# ---- 幂等键（R6-P2-10） ----
IDEMPOTENCY_KEY_HEADER = "idempotency-key"
IDEMPOTENCY_KEY_MAXLEN = 128
# 占住 key 后超过此时长仍未写回结果：视为首个请求创建失败，允许接管
IDEMPOTENCY_CLAIM_TTL_MIN = 10


def _idempotency_scope(user: User | None, device_id: str | None) -> str:
    return f"u:{user.id}" if user else f"d:{device_id}"


def _lookup_idempotency(db: Session, scope: str, key: str) -> IdempotencyRecord | None:
    return db.execute(
        select(IdempotencyRecord).where(
            IdempotencyRecord.scope == scope, IdempotencyRecord.key == key
        )
    ).scalar_one_or_none()


def _claim_idempotency_key(
    db: Session, scope: str, key: str
) -> tuple[IdempotencyRecord, bool]:
    """占住幂等键。返回 (record, 是否由本次请求占住)。

    并发同 key 撞唯一约束时 IntegrityError → 回滚后取现存记录（不抛 500）。
    R24-P2-1：撞 SQLite 写锁（"database is locked"，另一并发请求持有写锁超过
    busy_timeout）时同样不抛 500——锁的持有者必然在创建相同内容（key 相同），
    回滚后按"没抢到"处理，上游走 _idempotent_replay（有结果直接返回）或 409
    idempotency_in_progress。
    首个请求占住 key 后若超过 IDEMPOTENCY_CLAIM_TTL_MIN 仍未写回结果
    （创建中途失败/崩溃），本次请求接管该 key；仍在创建中则返回 (记录, False)，
    调用方按 409 idempotency_in_progress 处理。
    """
    rec = IdempotencyRecord(scope=scope, key=key, task_ids=[])
    db.add(rec)
    try:
        db.flush()
        return rec, True
    except IntegrityError:
        db.rollback()
        rec = _lookup_idempotency(db, scope, key)
        if (
            rec is not None
            and not rec.task_ids
            and rec.created_at is not None
            and utcnow() - rec.created_at
            > timedelta(minutes=IDEMPOTENCY_CLAIM_TTL_MIN)
        ):
            db.delete(rec)
            db.flush()
            new_rec = IdempotencyRecord(scope=scope, key=key, task_ids=[])
            db.add(new_rec)
            # R22-P3-1：delete+insert 之间另一请求也可能接管同一 stale key，
            # 唯一约束撞车在这里是预期事件——回滚后按"没抢到"处理，上游
            # replay 看到赢家的记录（有结果直接返回创建结果，仍在创建中则
            # 409 idempotency_in_progress），不抛 500。
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                log.warning("idempotency_claim_takeover_lost", scope=scope)
                return None, False
            log.warning("idempotency_claim_takeover", scope=scope)
            return new_rec, True
        return rec, False
    except OperationalError as e:
        # R24-P2-1：见 docstring——只处理"database is locked"，其他
        # OperationalError（如磁盘故障）照常抛出，不掩盖真实 DB 故障
        if "database is locked" not in str(e):
            raise
        db.rollback()
        log.warning("idempotency_claim_lock_contention", scope=scope)
        return None, False


def _idempotent_replay(
    db: Session,
    user: User | None,
    device_id: str | None,
    scope: str,
    key: str,
) -> list[TaskOut] | None:
    """重复 key：直接返回首次创建的任务（R6-P2-10）。无记录返回 None。"""
    rec = _lookup_idempotency(db, scope, key)
    if not rec or not rec.task_ids:
        return None
    # R10-P1-2：单次查询 + selectinload 预加载 states（此前 db.get 逐个取
    # + _task_out 逐任务懒加载 states）。按 task_ids 原顺序返回。
    tasks = (
        db.execute(
            select(MonitorTask)
            .options(selectinload(MonitorTask.states))
            .where(MonitorTask.id.in_(rec.task_ids))
        )
        .scalars()
        .all()
    )
    by_id = {t.id: t for t in tasks}
    out = []
    for tid in rec.task_ids:
        task = by_id.get(tid)
        if task is None:
            # R7 诚实注释（此前写"按新请求处理"不成立）：这里返回 None 后，上游
            # _claim_idempotency_key 撞唯一约束会看到 task_ids 非空的记录——
            # TTL（IDEMPOTENCY_CLAIM_TTL_MIN=10 分钟）内不会接管，直接走 409
            # idempotency_in_progress；TTL 过期后才允许接管 key 重建。
            return None
        # scope 已隔离归属（u: / d: 前缀），这里只做兜底一致性检查
        if user and task.user_id != user.id:
            return None
        if not user and not (task.user_id is None and task.device_id == device_id):
            return None
        out.append(_task_out(task, db))
    return out


def _content_idempotency_key(parts: list[str]) -> str:
    """R24-P2-1：内容派生幂等键。

    同归属（scope 已在 (scope, key) 唯一约束中隔离）+ 同内容（part_number /
    排序后门店组合等归一化片段）→ 同一个 key。前端每次调用生成全新
    Idempotency-Key 时，同内容不同 key 的并发请求也会撞到这个键：复用现有
    uq_idempotency_scope_key 唯一约束做串行化，替代 _find_conflict 纯 SELECT
    的 check-then-insert，从根本上消灭并发重复建任务。
    """
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"content:{digest}"


def _release_owned_content_key(
    db: Session, rec: IdempotencyRecord | None, owned: bool
) -> None:
    """R25-P3-1：释放本次请求占住但未回写结果的内容键占位记录。

    只释放 owned 且 task_ids 仍为空的记录（占位中途无任何行创建）。
    确定性校验失败（批量冲突 409）时调用：冲突检查针对已提交行，重试
    同内容必然再次 409 task_conflict（带具体冲突组合信息），释放不重开
    并发建重复的安全口；否则占位残留 10 分钟，同内容修正重试会被 409
    idempotency_in_progress 挡住、丢了具体冲突信息。
    """
    if owned and rec is not None and not rec.task_ids:
        db.delete(rec)
        db.flush()


@router.get("", response_model=list[TaskOut])
def list_tasks(
    status: str = Query(default="all", description="active=监控中, expired=已过期, all=全部"),
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None),
):
    if status not in ("active", "expired", "all"):
        raise APIError(400, "status 必须为 active、expired 或 all", "bad_status")
    # R4-P1-B5：selectinload 预加载 states，_task_out 不再每任务查一次 StockState
    q = (
        select(MonitorTask)
        .options(selectinload(MonitorTask.states))
        .order_by(MonitorTask.created_at.desc())
    )
    if user:
        q = q.where(MonitorTask.user_id == user.id)
    elif x_device_id:
        q = q.where(MonitorTask.device_id == x_device_id)
    else:
        return []
    tasks = db.execute(q).scalars().all()
    if status == "active":
        tasks = [t for t in tasks if not _is_expired(t)]
    elif status == "expired":
        tasks = [t for t in tasks if _is_expired(t)]
    return [_task_out(t, db) for t in tasks]


@router.post("", response_model=TaskOut, status_code=201)
def create_task(
    data: TaskCreateIn,
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None),
):
    if not user and not x_device_id:
        raise APIError(401, "匿名创建任务需要 X-Device-Id 头", "device_required")
    ip = _client_ip(request)
    if not check_rate_limit(
        f"task_create:{ip}", limit=TASK_CREATE_LIMIT, window_sec=TASK_CREATE_WINDOW_SEC
    ):
        raise APIError(429, "创建任务过于频繁，请稍后再试", "rate_limited")
    # R6-P2-10：幂等键——重复 key 直接返回首次创建的任务（网络重试/重复提交
    # 不再 check-then-insert 建出重复任务）
    idem_key = (request.headers.get(IDEMPOTENCY_KEY_HEADER) or "").strip()[
        :IDEMPOTENCY_KEY_MAXLEN
    ]
    scope = _idempotency_scope(user, x_device_id)
    if idem_key:
        replayed = _idempotent_replay(db, user, x_device_id, scope, idem_key)
        if replayed:
            return replayed[0]
    _validate_task_in(data)
    _validate_expires_at(data.expires_at)
    _check_task_limit(db, user, x_device_id)
    stores = [s.model_dump() for s in data.stores]
    part_number = data.part_number.strip().upper()
    # N7：门店号归一化（strip+upper）后再查重/入库，大小写或空格不一致不产生重复任务
    store_numbers = [s["number"].strip().upper() for s in stores]
    channels = data.channels.model_dump(exclude_none=True)
    # P0：已验证邮箱自动算作邮件渠道（回填在 _require_channels 之前，
    # 空渠道检查自然通过；回填值随任务入库，notifier 真实可达）
    channels = _fill_verified_email(channels, user)
    _require_channels(channels, user)
    # R24-P2-1：在 _find_conflict 的纯 SELECT 之前先占住内容派生幂等键
    # （scope|part_number|排序后门店），复用 uq_idempotency_scope_key 唯一约束
    # 做并发串行化。前端每次调用生成全新 Idempotency-Key 时，同内容不同 key
    # 的并发请求也会撞到同一内容键：抢到的继续走创建，没抢到的走
    # _idempotent_replay（有 task_ids 直接返回已建任务）或 409
    # idempotency_in_progress，不再 check-then-insert 建出重复任务。
    content_key = _content_idempotency_key([part_number, *sorted(store_numbers)])
    content_rec, content_owned = _claim_idempotency_key(db, scope, content_key)
    if not content_owned:
        # 并发同内容：首个请求已占住内容键——有结果直接返回已建任务，
        # 否则说明首个请求还在创建中
        replayed = _idempotent_replay(db, user, x_device_id, scope, content_key)
        if replayed:
            return replayed[0]
        raise APIError(409, "相同内容的任务正在创建中，请稍后重试", "idempotency_in_progress")
    # 断裂-8：重复任务冲突检测（R24-P2-1 语义变化：同内容重复提交不再
    # 409 task_conflict，而是幂等返回已建任务，并把 task_ids 回写到内容键
    # 记录，后续同内容请求直接 replay）
    conflict = _find_conflict(
        db, user.id if user else None, x_device_id, part_number, store_numbers
    )
    if conflict:
        content_rec.task_ids = [conflict.id]
        db.add(content_rec)
        db.commit()
        log.info("task_create_content_replay", task_id=conflict.id)
        return _task_out(conflict, db)
    idem_rec = None
    if idem_key:
        idem_rec, owned = _claim_idempotency_key(db, scope, idem_key)
        if not owned:
            # 并发同 key：首个请求已占住——有结果直接返回，否则说明还在创建中
            replayed = _idempotent_replay(db, user, x_device_id, scope, idem_key)
            if replayed:
                return replayed[0]
            raise APIError(409, "相同的幂等键正在处理中，请稍后重试", "idempotency_in_progress")
    task_kwargs: dict = dict(
        user_id=user.id if user else None,
        device_id=None if user else x_device_id,
        name=data.name,
        group=data.group,
        category=data.category,
        part_number=part_number,
        product_name=data.product_name,
        color=data.color,
        capacity=data.capacity,
        store_numbers=store_numbers,
        stores=stores,
        mode=data.mode,
        repeat_interval_sec=data.repeat_interval_sec,
        channels=channels,
        expires_at=_clamp_expires(data.expires_at, anonymous=user is None),
        auto_retire=data.auto_retire,
    )
    task = MonitorTask(**task_kwargs)
    db.add(task)
    db.commit()
    db.refresh(task)
    # R24-P2-1：内容键回写 task_ids（后续同内容请求直接 replay）；用户幂等键
    # 回写保持不变
    content_rec.task_ids = [task.id]
    db.add(content_rec)
    if idem_rec is not None:
        idem_rec.task_ids = [task.id]
        db.add(idem_rec)
    db.commit()
    log.info("task_created", task_id=task.id, user_id=user.id if user else None)
    return _task_out(task, db)


@router.post("/batch", response_model=list[TaskOut], status_code=201)
def batch_create(
    data: TaskBatchIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """门店 × 型号批量生成任务。"""
    # R9-O2：独立 IP 限流（单建是 task_create:ip，这里另起 key 另计）
    if not check_rate_limit(
        f"batch_create:{_client_ip(request)}",
        limit=BATCH_CREATE_LIMIT,
        window_sec=BATCH_CREATE_WINDOW_SEC,
    ):
        raise APIError(429, "批量创建过于频繁，请稍后再试", "rate_limited")
    # R6-P2-10：幂等键——重复 key 直接返回首次批量创建的任务
    idem_key = (request.headers.get(IDEMPOTENCY_KEY_HEADER) or "").strip()[
        :IDEMPOTENCY_KEY_MAXLEN
    ]
    scope = _idempotency_scope(user, None)
    if idem_key:
        replayed = _idempotent_replay(db, user, None, scope, idem_key)
        if replayed:
            return replayed
    # R4-P1-B6：复用 _validate_task_in 校验 mode + category（此前只判 mode，
    # category 非法会直接入库）
    _validate_task_in(data)
    tier = effective_tier_of(user)
    existing = db.execute(
        select(func.count()).select_from(MonitorTask).where(MonitorTask.user_id == user.id)
    ).scalar()
    combos = [(p, s) for p in data.part_numbers for s in data.store_numbers]
    if existing + len(combos) > tier["tasks_limit"]:
        raise APIError(
            403,
            f"批量生成将超出任务上限（{tier['tasks_limit']}），本次 {len(combos)} 个",
            "task_limit",
        )
    # R24-P2-1：内容派生幂等键——在批量冲突检查之前先占住整批内容键
    # （scope|category|mode|排序后 part×store 组合），复用唯一约束串行化
    # 同内容不同 key 的并发批量请求。没抢到的走 replay（返回首次批量创建
    # 的任务）或 409 idempotency_in_progress，不再并发建出重复任务。
    combos_norm = [(part.strip().upper(), store.strip().upper()) for part, store in combos]
    batch_content_key = _content_idempotency_key(
        [data.category, data.mode] + [f"{pn}|{sn}" for pn, sn in sorted(combos_norm)]
    )
    batch_content_rec, batch_content_owned = _claim_idempotency_key(db, scope, batch_content_key)
    if not batch_content_owned:
        replayed = _idempotent_replay(db, user, None, scope, batch_content_key)
        if replayed:
            return replayed
        raise APIError(409, "相同内容的批量任务正在创建中，请稍后重试", "idempotency_in_progress")
    # 断裂-8：批量内去重 + 与已有任务查重（409）；N7：门店号归一化后再查重
    # R10-P1-2：用户已有任务单次查询后内存比对（此前每 combo 一次 _find_conflict，
    # 每次全表拉回逐条比对，最多几百次查询）
    owned = (
        db.execute(select(MonitorTask).where(MonitorTask.user_id == user.id)).scalars().all()
    )
    seen: set[tuple[str, frozenset]] = set()
    for pn, sn in combos_norm:
        key = (pn, frozenset([sn]))
        if key in seen:
            # R25-P3-1：确定性校验失败、无任何行创建，先释放内容键占位
            _release_owned_content_key(db, batch_content_rec, batch_content_owned)
            raise APIError(409, f"批量内重复：{pn} × {sn} 出现了多次", "task_conflict")
        seen.add(key)
        conflict = _find_conflict_in(owned, pn, [sn])
        if conflict:
            # R25-P3-1：同上——释放占位，让重试拿到真正的 409 task_conflict
            _release_owned_content_key(db, batch_content_rec, batch_content_owned)
            raise _conflict_error(conflict)
    channels = data.channels.model_dump(exclude_none=True)
    # P0：已验证邮箱自动算作邮件渠道（回填在 _require_channels 之前，
    # 空渠道检查自然通过；回填值随任务入库，notifier 真实可达）
    channels = _fill_verified_email(channels, user)
    # R6-D4：非 trial 档任务必须配通知渠道
    _require_channels(channels, user)
    idem_rec = None
    if idem_key:
        idem_rec, owned = _claim_idempotency_key(db, scope, idem_key)
        if not owned:
            replayed = _idempotent_replay(db, user, None, scope, idem_key)
            if replayed:
                return replayed
            raise APIError(409, "相同的幂等键正在处理中，请稍后重试", "idempotency_in_progress")
    created = []
    for pn, sn in combos_norm:
        name = data.name_template.replace("{part_number}", pn).replace("{store_number}", sn)
        task = MonitorTask(
            user_id=user.id,
            name=name,
            category=data.category,
            part_number=pn,
            store_numbers=[sn],
            stores=[{"number": sn, "name": "", "city": ""}],
            mode=data.mode,
            channels=channels,
        )
        db.add(task)
        created.append(task)
    # R10-P1-2：先 flush 拿到 PK（flush 不过期对象，取 t.id 不触发查询），再
    # commit，最后单次 select + selectinload 预加载 states。此前是逐任务
    # db.refresh（N 次查询）+ _task_out 逐任务懒加载 states（又是 N 次）。
    db.flush()
    created_ids = [t.id for t in created]
    db.commit()
    if created_ids:
        created = (
            db.execute(
                select(MonitorTask)
                .options(selectinload(MonitorTask.states))
                .where(MonitorTask.id.in_(created_ids))
                # id 按插入顺序递增，排序后即创建时间顺序（与此前 created 顺序一致）
                .order_by(MonitorTask.id)
            )
            .scalars()
            .all()
        )
    # R24-P2-1：内容键回写 task_ids（后续同内容批量请求直接 replay）；
    # 用户幂等键回写保持不变
    batch_content_rec.task_ids = [t.id for t in created]
    db.add(batch_content_rec)
    if idem_rec is not None:
        idem_rec.task_ids = [t.id for t in created]
        db.add(idem_rec)
    db.commit()
    log.info("task_batch_created", user_id=user.id, count=len(created))
    return [_task_out(t, db) for t in created]


def _get_owned(task_id: int, user: User | None, device_id: str | None, db: Session) -> MonitorTask:
    task = db.get(MonitorTask, task_id)
    if not task:
        raise APIError(404, "任务不存在", "not_found")
    if user and task.user_id == user.id:
        return task
    if not user and device_id and task.device_id == device_id and task.user_id is None:
        return task
    raise APIError(403, "无权操作该任务", "forbidden")


@router.patch("/{task_id}", response_model=TaskOut)
def patch_task(
    task_id: int,
    data: TaskPatchIn,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None),
):
    task = _get_owned(task_id, user, x_device_id, db)
    # R4-P2：先校验后 setattr（此前 mode 非法时已 setattr，commit 前才 400，
    # 虽未落库但对象状态已脏）
    if data.mode is not None and data.mode not in ("instant", "confirmed"):
        raise APIError(400, "mode 必须为 instant 或 confirmed", "bad_mode")
    if data.expires_at is not None:
        # R6-P2-12：拒绝过去时间
        _validate_expires_at(data.expires_at)
    if data.channels is not None:
        # R6-D4：非 trial 档任务不允许把渠道清空（空渠道的到货边沿会被静默消费）
        # P0：已验证邮箱自动算作邮件渠道，语义与创建一致（清掉显式邮箱后仍有
        # 注册邮箱可用，不会 400）；回填值直接入库，notifier 真实可达
        data_channels = _fill_verified_email(data.channels.model_dump(exclude_none=True), user)
        _require_channels(data_channels, user)
        task.channels = data_channels
    if data.paused is not None:
        # R6-I9：手动暂停/恢复同步暂停原因——恢复时清空原因，避免脏原因残留
        task.paused_reason = "manual" if data.paused else None
    fields = ("name", "group", "paused", "mode", "repeat_interval_sec")
    for field in fields:
        v = getattr(data, field)
        if v is not None:
            setattr(task, field, v)
    if data.auto_retire is not None:
        task.auto_retire = data.auto_retire
    if data.expires_at is not None:
        # 匿名任务同样强制 24h 上限
        task.expires_at = _clamp_expires(data.expires_at, anonymous=user is None)
    db.add(task)
    db.commit()
    db.refresh(task)
    return _task_out(task, db)


@router.get("/{task_id}", response_model=TaskOut)
def get_task(
    task_id: int,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None),
):
    """取单个任务详情（含 channels，供任务详情页通知渠道配置用）。"""
    task = _get_owned(task_id, user, x_device_id, db)
    return _task_out(task, db)


@router.post("/{task_id}/renew", response_model=TaskOut)
def renew_task(
    task_id: int,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None),
):
    """一键续期：expires_at = max(now, 原 expires_at) + 30 天（断裂-7）。

    R4-P2：提前续期不再丢剩余天数（此前一律 now+30d，剩 20 天时续期反而亏）。
    只延长时间，不改 paused/配额状态。匿名 trial 任务同样受 24h 上限钳制。
    """
    task = _get_owned(task_id, user, x_device_id, db)
    now = utcnow()
    # R13-P1-1：并发双击防护改用 last_renewed_at 专用列（仅续期成功时写入）。
    # 旧逻辑用 updated_at < 10s 去重，但 updated_at 带 onupdate=_utcnow，
    # 引擎每轮 poll（写 last_polled_at/last_poll_ok/last_poll_ms）都会推进它，
    # 导致续期被静默吞掉（pro 用户约 87% 概率续期无效，前端还显示"已续期至"
    # 假象）。专用列只有续期成功才写，引擎轮询不碰它，去重不再误触发。
    if (
        task.last_renewed_at is not None
        and (now - task.last_renewed_at).total_seconds() < RENEW_DEDUP_WINDOW_SEC
    ):
        log.info("task_renew_duplicate_click", task_id=task_id)
        return _task_out(task, db)
    base = max(now, task.expires_at) if task.expires_at else now
    task.expires_at = _clamp_expires(base + timedelta(days=30), anonymous=user is None)
    task.last_renewed_at = now
    db.add(task)
    db.commit()
    db.refresh(task)
    log.info("task_renewed", task_id=task_id, expires_at=task.expires_at)
    return _task_out(task, db)


@router.delete("/{task_id}", status_code=204)
def delete_task(
    task_id: int,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None),
):
    task = _get_owned(task_id, user, x_device_id, db)
    # R9-I1：删前置空抽成公共函数（lifecycle.null_notification_task_ids），
    # 与 task_expiry_sweep 的 trial 删除共用——db.py 未设 PRAGMA foreign_keys=ON，
    # SQLite 层 FK 的 ondelete="SET NULL" 不会触发。通知靠 part_number 快照列
    # 保留型号信息（见 B-N2）。（StockState 有 ORM cascade="all,delete" 兜底，
    # 无需显式处理。）
    null_notification_task_ids(db, task.id)
    # R25-P2-1：清理指向该任务的幂等记录（内容键+用户键），否则删任务后
    # 同内容立即重建（不同 key）会被旧记录挡 10 分钟（409
    # idempotency_in_progress）。并发在途的记录 task_ids 为空，不受影响。
    purge_task_idempotency_records(db, _idempotency_scope(user, x_device_id), task.id)
    db.delete(task)
    db.commit()
    log.info("task_deleted", task_id=task_id)
    return None


@router.get("/{task_id}/states")
def task_states(
    task_id: int,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None),
):
    """按门店 × 配置的状态列表（六态 + 原始字段）。"""
    task = _get_owned(task_id, user, x_device_id, db)
    rows = db.execute(select(StockState).where(StockState.task_id == task.id)).scalars().all()
    return [
        {
            "store_number": r.store_number,
            "part_number": r.part_number,
            "state": _display_state(task, r),
            "pickup_display": r.pickup_display,
            "store_pick_eligible": r.store_pick_eligible,
            "pickup_search_quote": r.pickup_search_quote,
            "confirmed_count": r.confirmed_count,
            "last_event_at": r.last_event_at.isoformat() + "Z" if r.last_event_at else None,
            "updated_at": r.updated_at.isoformat() + "Z" if r.updated_at else None,
        }
        for r in rows
    ]
