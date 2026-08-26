"""周期回顾 API（FR-09：手动/定时生成周记月报，入库 review_records + 知识库 reviews/）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.schemas.review import ReviewDeleteRequest, ReviewGenerateRequest
from app.services.review_service import ReviewService

router = APIRouter(prefix="/review", tags=["周期回顾"])


def _service() -> ReviewService:
    return ReviewService()


def _as_http(e: Exception, code: int = 400) -> HTTPException:
    return HTTPException(status_code=code, detail=str(e))


@router.post("/generate", summary="生成周期回顾摘要（周/月，入库 + 写入知识库）")
def generate(payload: ReviewGenerateRequest, db: Session = Depends(get_db)):
    try:
        return _service().generate_review(db, payload.period_type, payload.period_key)
    except ValueError as e:
        raise _as_http(e) from e
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"生成回顾失败：{e}") from e


@router.get("/list", summary="回顾记录列表")
def list_reviews(db: Session = Depends(get_db)):
    try:
        return {"reviews": _service().list_reviews(db)}
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"获取回顾列表失败：{e}") from e


@router.get("/{review_id}", summary="回顾记录详情（含摘要 Markdown）")
def get_review(review_id: int, db: Session = Depends(get_db)):
    try:
        return _service().get_review(db, review_id)
    except FileNotFoundError as e:
        raise _as_http(e, 404) from e
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{review_id}", summary="删除回顾记录（同步删除知识库回顾笔记）")
def delete_review(review_id: int, db: Session = Depends(get_db)):
    try:
        return _service().delete_review(db, review_id)
    except FileNotFoundError as e:
        raise _as_http(e, 404) from e
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(e)) from e
