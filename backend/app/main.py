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
    log.info("第二大脑后端启动于 http://%s:%s", settings.host, settings.port)
    yield
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