"""Pydantic schemas（请求/响应）。"""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# ---------- auth ----------
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    tier: str
    totp_enabled: bool


class MeOut(BaseModel):
    id: int
    email: str
    tier: str
    quota: dict
    totp_enabled: bool


class PasswordChangeIn(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class TotpVerifyIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)


# ---------- tasks ----------
class StoreIn(BaseModel):
    number: str
    name: str = ""
    city: str = ""


class TaskCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    group: str = ""
    category: str = "iphone"
    part_number: str = Field(min_length=3, max_length=64)
    product_name: str = ""
    color: str = ""
    capacity: str = ""
    stores: list[StoreIn] = Field(min_length=1)
    mode: str = "instant"
    repeat_interval_sec: int | None = None
    channels: dict = Field(default_factory=dict)
    expires_at: datetime | None = None


class TaskBatchIn(BaseModel):
    part_numbers: list[str] = Field(min_length=1)
    store_numbers: list[str] = Field(min_length=1)
    name_template: str = "{part_number} × {store_number}"
    category: str = "iphone"
    mode: str = "instant"
    channels: dict = Field(default_factory=dict)


class TaskPatchIn(BaseModel):
    name: str | None = None
    group: str | None = None
    paused: bool | None = None
    expires_at: datetime | None = None
    channels: dict | None = None
    mode: str | None = None
    repeat_interval_sec: int | None = None


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
    created_at: datetime
    latest: dict = Field(default_factory=dict)  # 最新状态摘要


# ---------- notify ----------
class NotifyTestIn(BaseModel):
    channel: str  # bark | wecom | dingtalk | feishu | email | sms
    target: str


# ---------- admin ----------
class AdminUserPatchIn(BaseModel):
    tier: str | None = None
    is_admin: bool | None = None
    paused_tasks: bool | None = None  # 预留：批量暂停该用户任务


class ErrorOut(BaseModel):
    detail: str
    code: str = ""
