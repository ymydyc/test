"""认证/账号/工作区相关出入参（阶段七）。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ---------- 请求 ----------
class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, description="登录用户名（唯一）")
    password: str = Field(..., min_length=6, max_length=128, description="登录密码")
    display_name: str | None = Field(None, max_length=128, description="显示名称（缺省取 username）")


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64, description="登录用户名")
    password: str = Field(..., min_length=1, max_length=128, description="登录密码")


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=128, description="原密码")
    new_password: str = Field(..., min_length=6, max_length=128, description="新密码（至少 6 位）")


class UpdateProfileRequest(BaseModel):
    display_name: str | None = Field(None, max_length=128, description="新的显示名称")


# ---------- 响应 ----------
class TokenResponse(BaseModel):
    access_token: str = Field(..., description="访问令牌（Bearer）")
    token_type: str = "bearer"
    expires_in: int = Field(..., description="有效期秒数")
    user: "UserInfo"
    workspace: "WorkspaceInfo"


class UserInfo(BaseModel):
    id: int
    username: str
    display_name: str | None = None
    status: int
    created_at: datetime | None = None


class WorkspaceInfo(BaseModel):
    id: int
    name: str
    type: str
    role: str | None = None


class SessionInfo(BaseModel):
    id: int
    label: str | None = None
    created_at: datetime | None = None
    last_seen: datetime | None = None
    expires_at: datetime | None = None
    revoked: int


class WorkspaceListResponse(BaseModel):
    workspaces: list[WorkspaceInfo]


# 允许前向引用
TokenResponse.model_rebuild()