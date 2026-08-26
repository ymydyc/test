"""AI 助手相关出入参（阶段四 FR-07 / FR-08）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChatStreamRequest(BaseModel):
    """SSE 流式对话请求。

    session_id 提供则续接既有会话，否则自动新建会话。
    """

    session_id: int | None = Field(None, ge=0, description="会话 chat_sessions.id；为空则新建")
    message: str = Field(..., min_length=1, max_length=8000, description="用户消息")
    top_k: int = Field(6, ge=1, le=20, description="检索上下文条数")


class QuestionsRequest(BaseModel):
    topic: str | None = Field(None, max_length=500, description="出题主题/范围，可为空取自当前知识库")
    count: int = Field(5, ge=1, le=20, description="生成题目数量")


class RetrieveImportRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="检索关键词")
    limit: int = Field(10, ge=1, le=50, description="返回文件数上限")


class GenerateMdRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=200_000, description="要落盘的 Markdown 正文")
    title: str | None = Field(None, max_length=200, description="生成文件主名（缺省用时间戳）")
    target_subpath: str | None = Field(None, max_length=300, description="相对导入区(raw/)的子路径；位置需已存在，缺省或位置不存在时写入默认 output/")