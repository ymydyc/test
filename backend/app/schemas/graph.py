"""图谱与检索相关出入参（阶段三 FR-03/FR-04）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class GraphBuildRequest(BaseModel):
    note_ids: list[int] = Field(..., min_length=1, description="需要重建图谱的笔记 id 列表")


class GraphRelationDeleteRequest(BaseModel):
    source: str = Field(..., description="起点实体名")
    target: str = Field(..., description="终点实体名")
    relation_type: str = Field(..., description="关系类型")


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="检索查询文本")
    top_k: int = Field(8, ge=1, le=50, description="返回条数上限")