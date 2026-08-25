"""路由聚合注册（v1）。后续新增域路由在此挂载。"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routers import graph, import_files, kb, search

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(import_files.router, prefix="", tags=["导入区"])
api_router.include_router(kb.router, prefix="", tags=["知识库"])
api_router.include_router(graph.router, prefix="", tags=["图谱"])
api_router.include_router(search.router, prefix="", tags=["检索"])
# 阶段四起挂载：assistant.py