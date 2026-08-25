"""review_records 周期回顾表。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReviewRecord(Base):
    """周期回顾表——周记/月报回顾摘要。"""

    __tablename__ = "review_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    period_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="周期类型（week/month）")
    period_key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, comment="周期标识（如 2026-W34 / 2026-08）")
    summary_md: Mapped[str] = mapped_column(Text, nullable=False, comment="回顾摘要 Markdown")
    note_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("kb_notes.id"), nullable=True, comment="若已入库，对应 kb_notes.id")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")