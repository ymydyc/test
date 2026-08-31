"""组工作区邀请码表（阶段八批次3）。

- 仅组创建者（creator=Workspace.creator_id）可生成邀请码。
- code：安全随机短码，进程内唯一；同一组可重复生成（旧的作废）。
- expires_at：到期时间（默认 5 分钟有效），校验时先比对当前时间。
- 用后即失效：redeem 时原子标记 used （used_by / used_at），防止复用。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkspaceInvite(Base):
    """组工作区邀请码。"""

    __tablename__ = "workspace_invites"
    __table_args__ = (
        UniqueConstraint("code", name="uq_ws_invite_code"),
        Index("idx_ws_invite_ws", "workspace_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    workspace_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workspaces.id"), nullable=False, comment="目标组 workspaces.id")
    creator_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False, comment="生成邀请码的创建者 users.id")
    code: Mapped[str] = mapped_column(String(64), nullable=False, comment="安全随机短码")
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, comment="过期时间（默认 5 分钟）")
    used_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True, comment="用码加入的用户 users.id")
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="使用时间")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, comment="生成时间")