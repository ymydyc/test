"""memos 备忘录表。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Memo(Base):
    """备忘录表——用户在 AI 助手中保存的备忘录。"""

    __tablename__ = "memos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="备忘录内容")
    tags: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="标签（可逗号分隔）")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False, comment="更新时间")