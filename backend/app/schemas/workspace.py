"""工作区/组成员/邀请码出入参（阶段八批次3）。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class GroupCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="组名称")
    description: str | None = Field(None, max_length=255, description="组描述")


class GroupUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128, description="组名称（省略=不改）")
    description: str | None = Field(None, max_length=255, description="组描述（省略=不改）")


class TransferRequest(BaseModel):
    new_owner_id: int = Field(..., gt=0, description="组内目标成员 users.id")


class InviteResponse(BaseModel):
    workspace_id: int
    code: str
    expires_at: datetime


class JoinRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=64, description="邀请码")


class MemberInfo(BaseModel):
    user_id: int
    username: str | None = None
    display_name: str | None = None
    role: str


class WorkspaceDetailResponse(BaseModel):
    id: int
    name: str
    type: str
    creator_id: int
    description: str | None = None
    created_at: datetime | None = None
    members: list[MemberInfo] = []
    role: str | None = None  # 当前用户在该工作区的角色