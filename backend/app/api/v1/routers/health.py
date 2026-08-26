"""知识健康检查 API（FR-10：孤立节点 / 过时信息 / 双链缺失检测报告）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.services.health_service import HealthService

router = APIRouter(prefix="/health", tags=["健康检查"])


@router.get("/report", summary="运行知识健康检查并输出报告")
def report(db: Session = Depends(get_db)):
    try:
        return HealthService().run_report(db)
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"健康检查失败：{e}") from e
