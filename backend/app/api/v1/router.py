"""路由聚合注册（v1）。后续新增域路由在此挂载。"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routers import assistant, graph, health, import_files, kb, review, search

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(import_files.router, prefix="", tags=["导入区"])
api_router.include_router(kb.router, prefix="", tags=["知识库"])
api_router.include_router(graph.router, prefix="", tags=["图谱"])
api_router.include_router(search.router, prefix="", tags=["检索"])
api_router.include_router(assistant.router, prefix="", tags=["AI助手"])
api_router.include_router(review.router, prefix="", tags=["周期回顾"])
api_router.include_router(health.router, prefix="", tags=["健康检查"])