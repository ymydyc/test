"""AI 助手 API（阶段四 FR-07 / FR-08）。

- GET  /assistant/sessions                    会话列表
- POST /assistant/sessions                    新建会话
- GET  /assistant/sessions/{id}/messages      会话消息（重开历史）
- POST /assistant/chat/stream                 流式对话（SSE）
- POST /assistant/questions                   出题
- POST /assistant/retrieve-import             检索原始文件区
- POST /assistant/generate-md                 生成 md（默认 ./input/）
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.engine import get_db
from app.schemas.assistant import (
    ChatStreamRequest,
    GenerateMdRequest,
    QuestionsRequest,
    RetrieveImportRequest,
)
from app.services.assistant_service import AssistantService

router = APIRouter(prefix="/assistant", tags=["AI助手"])


def _svc() -> AssistantService:
    return AssistantService()


@router.get("/sessions", summary="会话列表")
def sessions(limit: int = 50, db: Session = Depends(get_db)):
    return {"sessions": _svc().list_sessions(db, limit)}


@router.post("/sessions", summary="新建会话")
def create_session(payload: dict | None = None, db: Session = Depends(get_db)):
    title = (payload or {}).get("title")
    return _svc().create_session(db, title)


@router.get("/sessions/{session_id}/messages", summary="会话历史消息")
def messages(session_id: int, db: Session = Depends(get_db)):
    return {"session_id": session_id, "messages": _svc().list_messages(db, session_id)}


@router.patch("/sessions/{session_id}", summary="重命名会话")
def rename_session(session_id: int, payload: dict | None = None, db: Session = Depends(get_db)):
    title = (payload or {}).get("title")
    updated = _svc().rename_session(db, session_id, title)
    if updated is None:
        raise HTTPException(status_code=404, detail="会话不存在或标题为空")
    return updated


@router.delete("/sessions/{session_id}", summary="删除会话（含消息）")
def delete_session(session_id: int, db: Session = Depends(get_db)):
    ok = _svc().delete_session(db, session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True, "session_id": session_id}


@router.post("/chat/stream", summary="SSE 流式对话（混合检索上下文 + 引用溯源）")
def chat_stream(payload: ChatStreamRequest, db: Session = Depends(get_db)):
    if not settings.dashscope_api_key:
        raise HTTPException(status_code=400, detail="未配置 DASHSCOPE_API_KEY，AI 助手不可用")

    def gen():
        for ev in _svc().stream_chat(db, payload.session_id, payload.message, payload.top_k):
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/questions", summary="根据知识库/图谱内容生成练习题")
def questions(payload: QuestionsRequest, db: Session = Depends(get_db)):
    res = _svc().generate_questions(db, payload.topic, payload.count)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("reason", "出题失败"))
    return res


@router.post("/retrieve-import", summary="检索原始文件区并读取内容")
def retrieve_import(payload: RetrieveImportRequest, db: Session = Depends(get_db)):
    return _svc().retrieve_import(db, payload.query, payload.limit)


@router.post("/generate-md", summary="把内容/对话总结为 md（默认 ./input/）")
def generate_md(payload: GenerateMdRequest, db: Session = Depends(get_db)):
    try:
        return _svc().generate_md(db, payload.content, payload.title, payload.target_subpath)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e