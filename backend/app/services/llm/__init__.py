"""LLM 抽象工厂：按配置/环境返回可用客户端。
"""
from __future__ import annotations

from app.core.config import settings
from app.services.llm.base import LLMClient
from app.services.llm.dashscope_client import DashScopeClient


def get_llm() -> LLMClient:
    """返回全局 LLM 客户端（当前仅 DashScope）。"""
    return DashScopeClient(
        api_key=settings.dashscope_api_key,
        model=settings.llm_model,
        embedding_model=settings.embedding_model,
    )