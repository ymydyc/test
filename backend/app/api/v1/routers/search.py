"""混合检索 API（阶段三 FR-04：向量 + 图路径 RRF 融合）。

POST /search  → {query, results, sources}
其中 results 已 RRF 融合，含父块上下文增强(parent_text)；sources 保留各检索器原始结果。
无 DASHSCOPE_KEY 或无相关数据时优雅降级（返回空结果 + 说明）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.engine import get_db
from app.schemas.graph import SearchRequest
from app.services.retriever import build_coordinator

router = APIRouter(prefix="/search", tags=["检索"])


@router.post("", summary="混合检索（向量 + 图路径，RRF 融合）")
def search(payload: SearchRequest, db: Session = Depends(get_db)):
    if not settings.dashscope_api_key:
        raise HTTPException(status_code=400, detail="未配置 DASHSCOPE_API_KEY，检索不可用")
    try:
        coord = build_coordinator(db, top_k=payload.top_k)
        return coord.search(payload.query, top_k=payload.top_k)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"检索失败：{e}") from e