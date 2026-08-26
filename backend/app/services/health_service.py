"""知识健康检查服务（FR-10）：检测孤立节点 / 过时信息 / 双链缺失，输出健康报告。

检查项：
1. 孤立实体（isolated_entities）——图谱中没有任何关系边（入度=出度=0）的实体节点。
2. 过时笔记（stale_notes）——知识库笔记内容哈希与磁盘文件不一致（需重新索引/写库）。
3. 缺失笔记文件（missing_note_files）——DB 有记录但 kb/ 下文件不存在。
4. 缺失导入文件（missing_import_files）——导入区记录存在但 raw/ 下文件不存在。
5. 未登记文件（unregistered_files）——磁盘存在但 DB 无记录（潜在孤儿，通常自动对齐后无）。
6. 双链缺失（no_backlink_notes）——笔记正文不含任何 [[双链]] 交叉引用。

报告为结构化 dict：{generated_at, summary, healthy, 各检查项清单}。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import GraphEntity, GraphRelation, ImportFile, KbNote

log = get_logger("services.health")

WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")


class HealthService:
    def __init__(self, raw_dir: Path | str | None = None, kb_dir: Path | str | None = None) -> None:
        self.raw_dir = Path(raw_dir or settings.raw_dir)
        self.kb_dir = Path(kb_dir or settings.kb_dir)

    # ---------- 检查项 ----------
    def _isolated_entities(self, db: Session) -> list[dict]:
        """没有任何关系边的孤立实体。"""
        related: set[int] = set()
        for r in db.query(GraphRelation.source_entity_id, GraphRelation.target_entity_id).all():
            related.add(r[0])
            related.add(r[1])
        out: list[dict] = []
        for e in db.query(GraphEntity).order_by(GraphEntity.id).all():
            if e.id not in related:
                out.append({"id": e.id, "name": e.name, "entity_type": e.entity_type,
                            "description": e.description})
        return out

    def _stale_notes(self, db: Session) -> list[dict]:
        """内容哈希与磁盘文件不一致的笔记（过时信息）。"""
        out: list[dict] = []
        for n in db.query(KbNote).order_by(KbNote.id).all():
            f = (self.kb_dir / n.note_path).resolve()
            if not f.is_file():
                continue  # 文件缺失单独报告
            try:
                disk_hash = hashlib.sha256(f.read_bytes()).hexdigest()
            except OSError:
                continue
            if disk_hash != n.content_hash:
                out.append({"id": n.id, "note_path": n.note_path, "title": n.title})
        return out

    def _missing_note_files(self, db: Session) -> list[dict]:
        out: list[dict] = []
        for n in db.query(KbNote).order_by(KbNote.id).all():
            f = (self.kb_dir / n.note_path).resolve()
            if not f.is_file():
                out.append({"id": n.id, "note_path": n.note_path, "title": n.title})
        return out

    def _missing_import_files(self, db: Session) -> list[dict]:
        out: list[dict] = []
        for rec in db.query(ImportFile).order_by(ImportFile.id).all():
            f = (self.raw_dir / rec.rel_path).resolve()
            if not f.exists():
                out.append({"id": rec.id, "rel_path": rec.rel_path, "file_name": rec.file_name})
        return out

    def _unregistered_files(self, db: Session) -> list[dict]:
        """磁盘存在但 DB 无导入记录的文件（孤儿）。"""
        registered = {r[0] for r in db.query(ImportFile.rel_path).all()}
        out: list[dict] = []
        if not self.raw_dir.exists():
            return out
        for f in self.raw_dir.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(self.raw_dir).as_posix()
            if rel not in registered:
                out.append({"rel_path": rel, "file_size": f.stat().st_size})
        return out

    def _no_backlink_notes(self, db: Session) -> list[dict]:
        """笔记正文不含任何 [[双链]] 交叉引用（双链缺失）。"""
        out: list[dict] = []
        for n in db.query(KbNote).order_by(KbNote.id).all():
            if not WIKILINK_RE.search(n.content_md or ""):
                out.append({"id": n.id, "note_path": n.note_path, "title": n.title})
        return out

    # ---------- 报告 ----------
    def run_report(self, db: Session) -> dict:
        checks = {
            "isolated_entities": self._isolated_entities(db),
            "stale_notes": self._stale_notes(db),
            "missing_note_files": self._missing_note_files(db),
            "missing_import_files": self._missing_import_files(db),
            "unregistered_files": self._unregistered_files(db),
            "no_backlink_notes": self._no_backlink_notes(db),
        }
        summary = {k: len(v) for k, v in checks.items()}
        total = sum(summary.values())
        return {
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "summary": summary,
            "total_issues": total,
            "healthy": total == 0,
            **checks,
        }
