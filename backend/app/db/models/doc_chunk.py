"""doc_chunks 文档分块表。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DocChunk(Base):
    """文档分块表——笔记切分后的语义块，用于向量命中的原文段落溯源。"""

    __tablename__ = "doc_chunks"
    __table_args__ = (
        Index("idx_chunk_note", "note_id"),
        Index("idx_parent_chunk", "parent_chunk_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    note_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("kb_notes.id"), nullable=False, comment="所属笔记 kb_notes.id")
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, comment="分块序号（在笔记内从 0 开始）")
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False, comment="分块文本内容")
    char_start: Mapped[int] = mapped_column(Integer, nullable=False, comment="在原文中的起始字符位置")
    char_end: Mapped[int] = mapped_column(Integer, nullable=False, comment="在原文中的结束字符位置")
    parent_chunk_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="父块 doc_chunks.id（父子块关联；父块自身为空）")
    chroma_id: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="对应 Chroma 向量记录 id（向量本体存 Chroma）")
    workspace_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="所属工作区 workspaces.id（阶段七：隔离键）")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")