"""workspaces / workspace_members 工作区（空间）表（阶段七/八）。

- 隔离键：全站数据（导入区、知识库、向量、图谱、会话等）统一按 `workspace_id` 隔离。
- type：`personal`（个人空间，仅 owner 成员）/ `group`（组空间，阶段八，多成员共享）。
- 每个用户注册时自动创建其 `personal` 工作区。
- workspace_members：成员关系；creator（owner）在注册时即成为其个人空间成员，组空间的成员由阶段八邀请码管理。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Workspace(Base):
    """工作区表——统一租户/隔离键。"""

    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="工作区名称")
    type: Mapped[str] = mapped_column(String(16), default="personal", nullable=False, comment="类型：personal=个人空间，group=组空间")
    creator_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False, comment="创建者 users.id")
    description: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="工作区描述")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False, comment="更新时间")


class WorkspaceMember(Base):
    """工作区成员表——用户与工作区的成员关系。"""

    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_ws_member"),
        Index("idx_ws_member_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    workspace_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workspaces.id"), nullable=False, comment="工作区 workspaces.id")
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False, comment="成员 users.id")
    role: Mapped[str] = mapped_column(String(16), default="member", nullable=False, comment="角色：owner=创建者/管理员，member=普通成员")
    joined_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, comment="加入时间")

    # 便于读取成员信息（阶段八：展示用户名/昵称）
    user: Mapped["User"] = relationship()
    workspace: Mapped["Workspace"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WorkspaceMember ws={self.workspace_id} user={self.user_id} role={self.role}>"