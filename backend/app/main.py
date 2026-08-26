"""FastAPI 应用入口。

Lifespan：确保目录 → 自动建库建表 → 就绪。
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.middleware import register_middleware
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import get_logger, setup_logging

setup_logging()
log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动初始化：目录 + 自动建库建表（阶段一）
    from app.db import init_db
    init_db.init_all()
    # 阶段三：启动增量同步（kb/ watchdog + 轮询兜底，FR-05）
    _syncer = None
    try:
        from app.scheduler.incremental_sync import IncrementalSync
        _syncer = IncrementalSync(settings.kb_dir)
        _syncer.start()
    except Exception as e:  # pragma: no cover  调度启动失败不阻断
        log.warning("增量同步调度启动失败：%s", e)
    # 阶段五：周期回顾调度（周记/月报，FR-09）
    _review_scheduler = None
    try:
        from app.scheduler.review_scheduler import ReviewScheduler
        _review_scheduler = ReviewScheduler()
        _review_scheduler.start()
    except Exception as e:  # pragma: no cover
        log.warning("周期回顾调度启动失败：%s", e)
    log.info("第二大脑后端启动于 http://%s:%s", settings.host, settings.port)
    yield
    if _syncer is not None:
        try:
            _syncer.stop()
        except Exception:  # pragma: no cover
            pass
    if _review_scheduler is not None:
        try:
            _review_scheduler.stop()
        except Exception:  # pragma: no cover
            pass
    log.info("第二大脑后端已关闭")


app = FastAPI(
    title="第二大脑·后端",
    description="基于 RAG 的第二大脑程序（阶段一：项目骨架 + 原始文件导入区）",
    version="0.1.0",
    lifespan=lifespan,
)

register_middleware(app)
app.include_router(api_router)


@app.get("/", tags=["健康"])
def root():
    return {"service": "second-brain", "stage": "stage1", "status": "ok"}


@app.get("/healthz", tags=["健康"])
def healthz():
    return {"status": "ok"}