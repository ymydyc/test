"""增量同步调度（FR-05）：监听 kb/ 目录变更 + 轮询兜底。

设计考量：
- 经 API 的正常写库（write/save）已由 kb_service._index_note 事件驱动实时完成向量+图谱。
- 本调度器覆盖**未经 API**的外部变更（直接编辑 kb/*.md），用 watchdog 事件 + 定时轮询兜底，
  内容哈希比对（sync_state.update_state 的 content_hash）实现"变更局部更新"。
- raw/ 目录变更仅重置导入标记与哈希，不触发图谱（由导入区逻辑处理）。

实现为后台线程，随 FastAPI lifespan 启停；任何依赖不可用均不影响主流程运行。
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("scheduler.sync")

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    _WATCHDOG_OK = True
except Exception:  # pragma: no cover  环境无 watchdog 时退化为纯轮询
    _WATCHDOG_OK = False
    FileSystemEventHandler = object
    Observer = None


class _KbHandler(FileSystemEventHandler):
    """捕获 kb/ 下 .md 文件的外部变更，转交同步器。"""

    def __init__(self, syncer: "IncrementalSync") -> None:
        self.syncer = syncer

    def on_modified(self, event):  # noqa: N802  (watchdog 钩子命名)
        if not event.is_directory and str(event.src_path).endswith(".md"):
            self.syncer.mark_dirty(Path(event.src_path))

    on_created = on_modified