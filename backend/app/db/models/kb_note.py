"""kb_notes 知识库笔记表。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CHAR, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class KbNote(Base):
    """知识库笔记表——从导入文件整理生成的 Markdown 笔记。"""

    __tablename__ = "kb_notes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    note_path: Mapped[str] = mapped_column(String(1024), nullable=False, comment="知识库内相对路径（对应笔记文件）")
    note_path_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="note_path 的 SHA256（utf8mb4 索引长度限制，用哈希列做唯一约束）")
    title: Mapped[str] = mapped_column(String(512), nullable=False, comment="笔记标题")
    content_md: Mapped[str] = mapped_column(LONGTEXT, nullable=False, comment="笔记 Markdown 正文（长文档用 LONGTEXT）")
    content_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, comment="笔记内容哈希（变更检测/增量更新）")
    origin_import_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("import_files.id"), nullable=True, comment="来源导入文件的 import_files.id（手动新建时为空）")
    frontmatter_json: Mapped[str | None] = mapped_column(Text, nullable=True, comment="Frontmatter 元数据（JSON 字符串）")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False, comment="更新时间")