"""DashScope LLM 实现（qwen-flash 文本生成 + text-embedding-v4 向量化）。

接入要点（对齐《需求.md》技术栈与开发.md 风险应对）：
- 官方 DSL 对话模型标识经实测为 `qwen-flash`（非 `qwen3.7-flash`）；
- `text-embedding-v4` 实测返回 **1024 维**，向量库 collection 维度需与之配平（见 vectorstore）。
"""
from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.services.llm.base import LLMClient

log = get_logger("services.llm.dashscope")


class DashScopeClient(LLMClient):
    """基于 dashscope SDK 的 LLM 客户端（非流式路径，供抽取/检索/工具使用）。"""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        embedding_model: str | None = None,
    ) -> None:
        self.api_key = api_key or settings.dashscope_api_key
        self.model = model or settings.llm_model
        self.embedding_model = embedding_model or settings.embedding_model

    # ---- 文本生成 ----
    def generate(self, messages: list[dict], *, json_mode: bool = False, **kwargs) -> str:
        import dashscope

        params: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "result_format": "message",
            "temperature": kwargs.get("temperature", 0.3),
        }
        response_format = kwargs.get("response_format")
        if json_mode or response_format == {"type": "json_object"}:
            params["response_format"] = {"type": "json_object"}
        resp = dashscope.Generation.call(api_key=self.api_key, **params)
        return self._extract_text(resp)

    @staticmethod
    def _extract_text(resp: Any) -> str:
        if resp.status_code != 200:
            raise RuntimeError(f"DashScope 生成失败[{resp.code}]: {getattr(resp, 'message', resp)}")
        out = resp.output
        choices = getattr(out, "choices", None) or out.get("choices") if isinstance(out, dict) else None
        if choices:
            msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else getattr(choices[0], "message", {})
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            return content or ""
        return out.get("text") if isinstance(out, dict) else (getattr(out, "text", "") or "")

    # ---- 向量化 ----
    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        import dashscope

        # text-embedding-v4 单次支持批量，分批以控制包大小
        batch = 8
        out: list[list[float]] = []
        for i in range(0, len(texts), batch):
            chunk = texts[i : i + batch]
            resp = dashscope.TextEmbedding.call(
                api_key=self.api_key, model=self.embedding_model, input=chunk,
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"DashScope Embedding 失败[{resp.code}]: {getattr(resp, 'message', resp)}"
                )
            # 实测 text-embedding-v4 返回 [{embedding:[...]}, ...]（无 index 字段）→ 按序对应输入
            for entry in resp.output["embeddings"]:
                emb = entry.get("embedding") if isinstance(entry, dict) else getattr(entry, "embedding", None)
                if not emb:
                    raise RuntimeError(f"DashScope Embedding 返回缺失向量：{entry}")
                out.append(emb)
        return out

    def availability(self) -> dict[str, Any]:
        return {
            "provider": "dashscope",
            "model": self.model,
            "embedding_model": self.embedding_model,
            "embedding_dim": settings.embedding_dim,
            "api_key_set": bool(self.api_key),
        }