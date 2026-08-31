"""chat_sessions / chat_messages AI 会话与消息表。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChatSession(Base):
    """AI 会话表——AI 助手的一次对话会话。"""

    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True, comment="所属用户 users.id（阶段七：会话按用户区分）")
    title: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="会话标题（默认取首条消息或自动命名）")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False, comment="最近更新时间")


class ChatMessage(Base):
    """AI 消息表——会话内的每条消息。"""

    __tablename__ = "chat_messages"
    __table_args__ = (Index("idx_msg_session", "session_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    session_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chat_sessions.id"), nullable=False, comment="所属会话 chat_sessions.id")
    role: Mapped[str] = mapped_column(String(16), nullable=False, comment="消息角色（user/assistant）")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="消息内容")
    citations_json: Mapped[str | None] = mapped_column(Text, nullable=True, comment="引用来源（知识库/导入区 溯源，JSON）")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")