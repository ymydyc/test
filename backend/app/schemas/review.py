"""周期回顾相关 Pydantic 出入参（FR-09）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ReviewGenerateRequest(BaseModel):
    period_type: str = Field(..., description="周期类型（week/month）")
    period_key: str | None = Field(None, description="周期标识（如 2026-W34 / 2026-08），缺省为当前周期")


class ReviewDeleteRequest(BaseModel):
    review_id: int = Field(..., description="回顾记录 id")
