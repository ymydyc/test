"""增量同步器实现（FR-05）：对 kb/ 变更做内容哈希比对 + 局部图谱/向量更新。

核心：以 kb_dir 内实际 .md 文件为准，比对 SyncState.content_hash；变了才重建该笔记的图谱+向量。
- 增量主体是"局部更新"（只重建变更的笔记，不重建全部）。
- raw/ 变更重置导入哈希/标记，由 ImportService.sync_from_fs 在 API 侧处理——此处不重复做。
"""
from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.engine import session_scope
from app.db.models import KbNote

from .base import _WATCHDOG_OK, _KbHandler, Observer

log = get_logger("scheduler.sync")


class IncrementalSync:
    POLL_INTERVAL = 30  # 轮询兜底间隔（秒）

    def __init__(self, kb_dir: Path | str | None = None) -> None:
        self.kb_dir = Path(kb_dir or settings.kb_dir)
        self._dirty: set[Path] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._observer: "Observer | None" = None
        self._graph = None  # 懒加载

    @property
    def _graph_service(self):
        if self._graph is None:
            from app.services.graph_service import GraphService
            self._graph = GraphService()
        return self._graph

    def mark_dirty(self, path: Path) -> None:
        with self._lock:
            self._dirty.add(Path(path))

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="kb-sync", daemon=True)
        self._thread.start()
        # 可选 watchdog（异步事件 → 缩短探测间隔）
        if _WATCHDOG_OK and Observer is not None:
            try:
                self.kb_dir.mkdir(parents=True, exist_ok=True)
                self._observer = Observer()
                self._observer.schedule(_KbHandler(self), str(self.kb_dir), recursive=False)
                self._observer.daemon = True
                self._observer.start()
                log.info("kb/ 增量同步已启动（watchdog + 轮询）")
            except Exception as e:  # pragma: no cover
                log.warning("watchdog 启动失败（仅轮询兜底）：%s", e)
                self._observer = None

    def stop(self) -> None:
        self._stop.set()
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=5)
            except Exception:  # pragma: no cover
                pass
            self._observer = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    # ---------- 主循环 ----------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sync_once()
            except Exception as e:  # 同步失败不影响循环
                log.warning("增量同步异常：%s", e)
            self._stop.wait(self.POLL_INTERVAL)

    def sync_once(self) -> int:
        """扫描一次 kb/ 全部 .md，哈希比对应重建变更笔记。返回处理条数。"""
        if not settings.dashscope_api_key:
            return 0
        # 合并 watchdog 事件 + 轮询扫描，统一去重
        targets: set[Path] = set()
        with self._lock:
            targets |= self._dirty
            self._dirty.clear()
        if not self.kb_dir.exists():
            return 0
        for p in self.kb_dir.rglob("*.md"):
            targets.add(p)
        if not targets:
            return 0
        changed = 0
        with session_scope() as db:
            for p in sorted(targets):
                rel = p.relative_to(self.kb_dir).as_posix()
                try:
                    content = p.read_text(encoding="utf-8")
                except OSError:  # 读取失败跳过
                    continue
                if self._hash_changed(db, rel, content):
                    self._rebuild_note(db, rel, content)
                    changed += 1
        if changed:
            log.info("增量同步：%s 个笔记已更新", changed)
        return changed

    # ---------- 工具 ----------
    @staticmethod
    def _sha(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _hash_changed(self, db: Session, rel: str, content: str) -> bool:
        note = db.query(KbNote).filter(KbNote.note_path == rel).first()
        if note is None:
            return False  # kb/ 下无对应笔记记录，不属于可同步范围，忽略
        current = self._sha(content)
        if note.content_hash != current:
            # 更新目标哈希并标记变化（下方重建后更新）
            return True
        return False

    def _rebuild_note(self, db: Session, rel: str, content: str) -> None:
        """外部变更了笔记文件 → 更新 MySQL 内容 + 重建向量/图谱（局部更新）。"""
        note = db.query(KbNote).filter(KbNote.note_path == rel).first()
        if note is None:
            return
        note.content_md = content
        note.content_hash = self._sha(content)
        # 重建向量块（复用 kb_service 分块 + 落库，保留父子块结构）
        from app.services.kb_service import chunk_text
        from app.db.models import DocChunk
        db.query(DocChunk).filter(DocChunk.note_id == note.id).delete()
        raw = chunk_text(content)
        rows: list[DocChunk] = []
        for i, c in enumerate(raw):
            row = DocChunk(note_id=note.id, chunk_index=i, chunk_text=c["text"],
                           char_start=c["start"], char_end=c["end"],
                           parent_chunk_id=None, chroma_id=None)
            rows.append(row)
            db.add(row)
        db.flush()  # 生成自增 id，用于父子块回填
        id_by_idx = {r.chunk_index: r.id for r in rows}
        for i, c in enumerate(raw):
            pid = c.get("parent_chunk_id")
            if pid is not None:
                rows[i].parent_chunk_id = id_by_idx[pid]
        db.flush()
        # 同步向量 + 图谱（异常不影响主流程）
        try:
            self._graph_service.build_note_vectors(db, note.id)
            self._graph_service.build_note_graph(db, note.id)
        except Exception as e:
            log.warning("增量重建失败（note=%s）：%s", note.id, e)
        log.info("外部变更已同步：%s", rel)