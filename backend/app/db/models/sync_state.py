"""sync_state 同步状态表。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import CHAR, BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SyncState(Base):
    """同步状态表——支撑增量同步：记录哈希、上次同步时间。"""

    __tablename__ = "sync_state"

    target_type: Mapped[str] = mapped_column(String(32), primary_key=True, comment="同步对象类型（import_file/kb_note 等）")
    target_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="同步对象主键")
    content_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, comment="上次成功同步的内容哈希")
    last_sync_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, comment="上次同步时间")
    sync_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, comment="同步状态（pending/syncing/success/error）")