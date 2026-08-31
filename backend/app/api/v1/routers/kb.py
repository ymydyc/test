"""知识库 API（阶段二：选择性写入 / 笔记 CRUD / 编辑标记联动 / 级联清理）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_workspace
from app.core.config import settings
from app.db.engine import get_db
from app.db.models import Workspace
from app.schemas.kb import BulkNotesDeleteRequest, SaveFileRequest, SaveNoteRequest, WriteRequest
from app.services.import_service import PathSafetyError
from app.services.kb_service import KbService
from app.parsers.base import ParseError

router = APIRouter(prefix="/kb", tags=["知识库"])


def _service(workspace_id: int) -> KbService:
    return KbService(settings.kb_dir, settings.raw_dir, workspace_id=workspace_id)


def _as_http(e: Exception, code: int = 400) -> HTTPException:
    return HTTPException(status_code=code, detail=str(e))


@router.post("/write", summary="选择性写入知识库（已导入自动跳过，返回逐项结果）")
def write(payload: WriteRequest, db: Session = Depends(get_db),
          ws: Workspace = Depends(get_current_workspace)):
    try:
        return _service(ws.id).write_selected(db, payload.paths)
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"写入失败：{e}") from e


@router.get("/notes", summary="知识库笔记列表")
def list_notes(db: Session = Depends(get_db), ws: Workspace = Depends(get_current_workspace)):
    try:
        return {"notes": _service(ws.id).list_notes(db)}
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"获取笔记列表失败：{e}") from e


@router.get("/notes/{note_id}", summary="笔记详情（含正文）")
def get_note(note_id: int, db: Session = Depends(get_db), ws: Workspace = Depends(get_current_workspace)):
    try:
        return _service(ws.id).get_note(db, note_id)
    except FileNotFoundError as e:
        raise _as_http(e, 404) from e
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put("/notes/{note_id}", summary="保存笔记编辑（来源导入文件标记联动置 0）")
def save_note(note_id: int, payload: SaveNoteRequest, db: Session = Depends(get_db),
              ws: Workspace = Depends(get_current_workspace)):
    try:
        return _service(ws.id).save_note_edit(db, note_id, payload.content_md)
    except FileNotFoundError as e:
        raise _as_http(e, 404) from e
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/notes/{note_id}", summary="删除笔记（级联清理向量块/孤立实体，来源标记置 0）")
def delete_note(note_id: int, db: Session = Depends(get_db), ws: Workspace = Depends(get_current_workspace)):
    try:
        return _service(ws.id).delete_note(db, note_id)
    except FileNotFoundError as e:
        raise _as_http(e, 404) from e
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/notes/bulk-delete", summary="批量删除笔记（逐条级联清理，缺失跳过）")
def bulk_delete_notes(payload: BulkNotesDeleteRequest, db: Session = Depends(get_db),
                      ws: Workspace = Depends(get_current_workspace)):
    try:
        return _service(ws.id).bulk_delete_notes(db, payload.note_ids)
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"批量删除失败：{e}") from e


@router.get("/preview", summary="解析导入区文件为 Markdown（内容区预览/编辑底稿）")
def preview(rel_path: str, db: Session = Depends(get_db), ws: Workspace = Depends(get_current_workspace)):
    try:
        return _service(ws.id).preview(db, rel_path)
    except (FileNotFoundError, PathSafetyError, ParseError) as e:
        raise _as_http(e) from e
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put("/file-content", summary="保存导入区文本文件编辑（标记置 0）")
def save_file(payload: SaveFileRequest, db: Session = Depends(get_db),
              ws: Workspace = Depends(get_current_workspace)):
    try:
        return _service(ws.id).save_file_edit(db, payload.rel_path, payload.content)
    except (FileNotFoundError, PathSafetyError, ValueError) as e:
        raise _as_http(e) from e
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(e)) from e
