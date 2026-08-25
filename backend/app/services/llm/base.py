"""LLM 抽象接口（可插拔：替换供应商实现即可）。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMClient(ABC):
    """统一 LLM 接口：文本生成 + 向量化。

    - generate(): 结构化/非结构化文本生成。
    - embed():    文本向量化（Embedding）。
    任何供应商（DashScope/OpenAI/…）实现本接口后可被工厂替换，不影响上层。
    """

    @abstractmethod
    def generate(self, messages: list[dict], *, json_mode: bool = False, **kwargs) -> str:
        """调用聊天补全。messages=[{role,content}, ...]，返回回复字符串。"""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """对文本列表做 Embedding，返回与输入等长的向量列表。"""

    @abstractmethod
    def availability(self) -> dict[str, Any]:
        """返回供应商可用性与配置摘要（供启动校验/状态接口）。"""