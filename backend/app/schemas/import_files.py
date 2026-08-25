"""导入区相关 Pydantic 出入参。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CreateFolderRequest(BaseModel):
    target_dir: str = Field("", description="在其下新建文件夹的父目录相对路径（空=根目录）")
    name: str = Field(..., min_length=1, description="新建文件夹名称")


class RenameRequest(BaseModel):
    rel_path: str = Field(..., description="当前相对路径")
    new_name: str = Field(..., min_length=1, description="新名称（含扩展名）")


class DeleteRequest(BaseModel):
    rel_path: str = Field(..., description="要删除的文件/文件夹相对路径")


class MoveRequest(BaseModel):
    rel_path: str = Field(..., description="要移动的文件/文件夹相对路径")
    target_dir: str = Field(..., description="目标父目录相对路径（空=移动到根目录）")


class OperationResponse(BaseModel):
    ok: bool = True
    message: str = ""
    data: dict = {}