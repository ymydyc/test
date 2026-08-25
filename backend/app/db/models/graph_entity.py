"""graph_entities 图谱实体表。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GraphEntity(Base):
    """图谱实体表——知识图谱中的实体节点，与 Neo4j 节点保持同步。"""

    __tablename__ = "graph_entities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    name: Mapped[str] = mapped_column(String(512), nullable=False, comment="实体名称（去重合并后的规范名）")
    name_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="name 的 SHA256（utf8mb4 索引长度限制，用哈希列做唯一约束）")
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False, comment="实体类型/标签（如 概念/人物/文档）")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="实体描述（LLM 撰写）")
    source_note_ids: Mapped[str | None] = mapped_column(Text, nullable=True, comment="关联来源笔记 ID 列表（JSON）")
    neo4j_id: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="对应 Neo4j 节点 id（用于图谱与图库对齐）")
    embedding_snapshot: Mapped[int] = mapped_column(TINYINT, default=0, nullable=False, comment="是否已生成 Embedding（1=是，0=否）")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False, comment="更新时间")