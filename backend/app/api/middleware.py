"""统一中间件（CORS / 异常 / 访问日志）。

Fixme：为控制阶段一规模，此处聚焦 CORS 与统一异常处理；日志中间件简短实现。
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("api.middleware")


def register_middleware(app: FastAPI) -> None:
    # CORS：前端 Vite dev server 默认 5173
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 本地开发放开
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception as exc:  # 兜底，避免请求体读取后仍抛异常
            log.exception("未处理的异常：%s", exc)
            response = JSONResponse(status_code=500, content={"detail": "服务器内部错误"})
        if settings.debug:
            log.info("%s %s -> %s", request.method, request.url.path, response.status_code)
        return response