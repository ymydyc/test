"""auth_sessions 鉴权会话表（阶段七：会话管理 / 吊销）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuthSession(Base):
    """登录会话记录表——登记签发过的 JWT（按 jti 吊销），支持活动会话列表与主动登出。

    - 每次成功登录创建一条；`token_jti` 存 JWT 的唯一标识（简化为访问令牌本身亦可，此处用 jti 便于吊销）。
    - 登出 / 吊销时置 revoked=1。
    """

    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("idx_auth_session_user", "user_id"),
        Index("idx_auth_session_jti", "token_jti"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False, comment="所属用户 users.id")
    token_jti: Mapped[str] = mapped_column(String(64), nullable=False, comment="JWT 的唯一标识（sha256(jti) 去敏存储）")
    label: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="会话标签（如设备/来源）")
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=True, comment="令牌过期时间")
    revoked: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False, comment="是否已吊销：1=是，0=否")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, comment="登录时间")
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最近活跃时间")