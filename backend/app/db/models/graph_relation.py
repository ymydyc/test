"""graph_relations 图谱关系表。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GraphRelation(Base):
    """图谱关系表——实体间的关系边，对应 Neo4j 关系。"""

    __tablename__ = "graph_relations"
    __table_args__ = (
        Index("idx_src", "source_entity_id"),
        Index("idx_tgt", "target_entity_id"),
        Index("idx_rel_note", "source_note_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    source_entity_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("graph_entities.id"), nullable=False, comment="起点实体 graph_entities.id")
    target_entity_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("graph_entities.id"), nullable=False, comment="终点实体 graph_entities.id")
    relation_type: Mapped[str] = mapped_column(String(128), nullable=False, comment="关系类型（如 包含/依赖/相关）")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="关系描述（LLM 撰写）")
    source_note_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("kb_notes.id"), nullable=True, comment="来源笔记 kb_notes.id")
    neo4j_rel_id: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="对应 Neo4j 关系 id")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")