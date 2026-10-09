"""Pydantic schemas（请求/响应）。"""

from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)


# ---------- auth ----------
def _validate_password_strength(v: str) -> str:
    """P1：密码必须含字母+数字，防纯字母弱口令（如 abcdefgh）"""
    if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
        raise ValueError("密码必须包含字母和数字")
    return v


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _pwd_strength(cls, v: str) -> str:
        return _validate_password_strength(v)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    id: int
    email: str
    tier: str
    totp_enabled: bool
    # R6-I4/I9：注册时认领 device 任务的提示（如"密钥已清空请重新配置"）
    notice: str | None = None
    # R9-I15：注册时验证码邮件是否首发成功（失败只记日志不阻断注册，
    # 前端据此提示用户去"我"页重发）
    email_sent: bool = False


class MeOut(BaseModel):
    id: int
    email: str
    tier: str
    quota: dict
    totp_enabled: bool
    # UX：前端据此直接置灰"在线刷新门店目录"（管理员功能），不等点了转圈再 403
    is_admin: bool = False
    # P0：前端通知渠道无填写时提示"将使用注册邮箱"，以此判断邮箱是否可用
    email_verified: bool = False


class PasswordChangeIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _pwd_strength(cls, v: str) -> str:
        return _validate_password_strength(v)


class TotpVerifyIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)


# ---------- tasks ----------
class StoreIn(BaseModel):
    number: str
    name: str = ""
    city: str = ""


class WebhookIn(BaseModel):
    """单个 webhook 渠道配置。"""

    url: str = Field(min_length=1, max_length=2048)
    platform: Literal["wecom", "dingtalk", "feishu"]

    @field_validator("url")
    @classmethod
    def _must_be_http_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("webhook url 必须以 http:// 或 https:// 开头")
        return v


class ChannelsIn(BaseModel):
    """任务通知渠道配置（与 tiers.py 的 channels 键名对齐）。

    未知键一律拒绝（extra="forbid"），避免前后端类型打架导致运行时
    AttributeError（不自洽-12）。非法结构由 FastAPI 返回 422。
    """

    model_config = ConfigDict(extra="forbid")

    bark_key: str | None = Field(default=None, max_length=256)
    # R17-P3-2：邮箱格式校验（typo 邮箱创建时即 422 被拦）；email-validator
    # 已在 requirements.txt，EmailStr 已导入。
    email: EmailStr | None = Field(default=None, max_length=255)
    webhooks: list[WebhookIn] | None = None

    @model_validator(mode="before")
    @classmethod
    def _drop_empty_webhooks(cls, data):
        # N13：前端可能提交未填完的 webhook 空行（url 为空/空白）。
        # 在子模型校验前过滤，避免整单 422 导致用户保存失败，也避免空行入库。
        if isinstance(data, dict) and isinstance(data.get("webhooks"), list):

            def _url(w) -> str:
                u = w.get("url") if isinstance(w, dict) else getattr(w, "url", "")
                return u if isinstance(u, str) else ""

            data["webhooks"] = [w for w in data["webhooks"] if _url(w).strip()]
        return data


class TaskCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    group: str = ""
    category: str = "iphone"
    part_number: str = Field(min_length=3, max_length=64)
    product_name: str = ""
    color: str = ""
    capacity: str = ""
    stores: list[StoreIn] = Field(min_length=1, max_length=20)
    mode: str = "instant"
    repeat_interval_sec: int | None = Field(default=None, ge=60)
    channels: ChannelsIn = Field(default_factory=ChannelsIn)
    expires_at: datetime | None = None
    # 僵尸任务自动结束开关（断裂-11）：连续 90 天无货自动暂停。DB 列需 models.py
    # 加 auto_retire 列（另见 migration）；列不存在时仅接受默认值 True。
    auto_retire: bool = True


class TaskBatchIn(BaseModel):
    # R4-P1-B2：两边各上限 30（笛卡尔积 ≤900），防 1万×1万物化 OOM worker
    part_numbers: list[str] = Field(min_length=1, max_length=30)
    store_numbers: list[str] = Field(min_length=1, max_length=30)
    name_template: str = "{part_name} × {store_name}"
    category: str = "iphone"
    mode: str = "instant"
    channels: ChannelsIn = Field(default_factory=ChannelsIn)


class TaskPatchIn(BaseModel):
    name: str | None = None
    group: str | None = None
    paused: bool | None = None
    expires_at: datetime | None = None
    channels: ChannelsIn | None = None
    mode: str | None = None
    repeat_interval_sec: int | None = Field(default=None, ge=60)
    auto_retire: bool | None = None


class TaskOut(BaseModel):
    id: int
    name: str
    group: str
    category: str
    part_number: str
    product_name: str
    color: str
    capacity: str
    stores: list[dict]
    mode: str
    repeat_interval_sec: int | None
    channels: dict
    paused: bool
    expires_at: datetime | None
    auto_retire: bool = True  # DB 列缺失时按 True 处理（见 tasks.py）
    created_at: datetime
    latest: dict = Field(default_factory=dict)  # 最新状态摘要

    @field_serializer("expires_at", "created_at")
    def _ser_naive_dt_z(self, v: datetime | None) -> str | None:
        """R6-P2-13：naive UTC 时间统一带 Z 后缀，与手写端口
        .isoformat()+"Z" 惯例一致。"""
        if v is None:
            return None
        if v.tzinfo is None:
            return v.isoformat() + "Z"
        return v.isoformat()


# ---------- notify ----------
class NotifyTestIn(BaseModel):
    channel: str  # bark | wecom | dingtalk | feishu | email | sms
    target: str


# ---------- admin ----------
class AdminUserPatchIn(BaseModel):
    tier: str | None = None
    is_admin: bool | None = None
    paused_tasks: bool | None = None  # 预留：批量暂停该用户任务
