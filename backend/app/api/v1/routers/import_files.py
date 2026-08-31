"""导入区管理 API（FR-01）。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_workspace
from app.core.config import settings
from app.db.engine import get_db
from app.db.models import Workspace
from app.schemas.import_files import (
    ClipRequest,
    CreateFolderRequest,
    MoveRequest,
    OperationResponse,
    RenameRequest,
)
from app.services.clip_service import ClipError, ClipService
from app.services.import_service import ImportService, PathSafetyError

router = APIRouter(prefix="/import-files", tags=["导入区"])


def _service(workspace_id: int) -> ImportService:
    return ImportService(settings.raw_dir, workspace_id=workspace_id)


@router.get("/tree", summary="获取导入区文件树（含导入标记）")
def get_tree(db: Session = Depends(get_db), ws: Workspace = Depends(get_current_workspace)):
    try:
        return _service(ws.id).build_tree(db)
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"获取文件树失败：{e}") from e


@router.post("/upload", summary="上传文件/文件夹（保留结构），path 列表与文件一一对应")
async def upload_files(
    files: list[UploadFile] = File(...),
    paths: str = Form("[]", description="相对路径 JSON 数组，与 files 顺序一致"),
    target_dir: str = Form("", description="公共目标父目录（空=根目录）"),
    db: Session = Depends(get_db),
    ws: Workspace = Depends(get_current_workspace),
):
    try:
        rel_paths: list[str] = json.loads(paths)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=422, detail=f"paths 参数非法：{e}") from e
    if len(rel_paths) != len(files):
        raise HTTPException(status_code=422, detail="files 与 paths 数量不一致")

    contents = [await f.read() for f in files]
    try:
        results = _service(ws.id).save_upload(db, rel_paths, contents, target_dir)
    except PathSafetyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return OperationResponse(ok=True, message="上传成功", data={"saved": results}).model_dump()


@router.post("/clip", summary="网页剪藏到导入区（FR-12：URL → Markdown）", response_model=OperationResponse)
def clip(payload: ClipRequest, db: Session = Depends(get_db),
         ws: Workspace = Depends(get_current_workspace)):
    try:
        data = ClipService(settings.raw_dir, workspace_id=ws.id).clip_url(db, payload.url, payload.target_dir)
    except (ClipError, PathSafetyError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"网页剪藏失败：{e}") from e
    return OperationResponse(ok=True, message="网页剪藏成功", data=data).model_dump()


@router.post("/folders", summary="新建文件夹", response_model=OperationResponse)
def create_folder(payload: CreateFolderRequest, db: Session = Depends(get_db),
                  ws: Workspace = Depends(get_current_workspace)):
    try:
        data = _service(ws.id).create_folder(db, payload.target_dir, payload.name)
    except (PathSafetyError, FileNotFoundError, FileExistsError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return OperationResponse(ok=True, message="新建文件夹成功", data=data).model_dump()


@router.put("/rename", summary="重命名文件/文件夹", response_model=OperationResponse)
def rename(payload: RenameRequest, db: Session = Depends(get_db),
           ws: Workspace = Depends(get_current_workspace)):
    try:
        data = _service(ws.id).rename(db, payload.rel_path, payload.new_name)
    except (PathSafetyError, FileNotFoundError, FileExistsError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return OperationResponse(ok=True, message="重命名成功", data=data).model_dump()


@router.put("/move", summary="移动文件/文件夹到目标目录", response_model=OperationResponse)
def move(payload: MoveRequest, db: Session = Depends(get_db),
         ws: Workspace = Depends(get_current_workspace)):
    try:
        data = _service(ws.id).move(db, payload.rel_path, payload.target_dir)
    except (PathSafetyError, FileNotFoundError, FileExistsError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return OperationResponse(ok=True, message="移动成功", data=data).model_dump()


@router.delete("/{rel_path:path}", summary="删除文件/文件夹（递归）", response_model=OperationResponse)
def delete(rel_path: str, db: Session = Depends(get_db),
           ws: Workspace = Depends(get_current_workspace)):
    try:
        data = _service(ws.id).delete(db, rel_path)
    except (PathSafetyError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return OperationResponse(ok=True, message="删除成功", data=data).model_dump()