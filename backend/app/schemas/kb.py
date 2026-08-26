"""知识库相关 Pydantic 出入参（阶段二 FR-02 / FR-11）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class WriteRequest(BaseModel):
    paths: list[str] = Field(..., min_length=1, description="选中的导入区文件/文件夹相对路径集合")


class SaveNoteRequest(BaseModel):
    content_md: str = Field(..., description="编辑后的笔记 Markdown 正文")


class SaveFileRequest(BaseModel):
    rel_path: str = Field(..., description="导入区文件相对路径")
    content: str = Field(..., description="编辑后的文本内容")


class BulkNotesDeleteRequest(BaseModel):
    note_ids: list[int] = Field(..., min_length=1, description="待批量删除的笔记 id 集合")
