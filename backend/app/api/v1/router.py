"""路由聚合注册（v1）。后续新增域路由在此挂载。"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routers import import_files, kb

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(import_files.router, prefix="", tags=["导入区"])
api_router.include_router(kb.router, prefix="", tags=["知识库"])
# 阶段三起挂载：graph.py / search.py / assistant.py