"""扩展点：全局能力注册表（供新增模块挂载，预留）。"""
from __future__ import annotations


class ExtensionRegistry:
    """极简能力注册表。后续可在此登记 LLM / 向量库 / 图库 / 解析器实现。"""

    def __init__(self) -> None:
        self._registry: dict[str, dict] = {}

    def register(self, group: str, name: str, impl) -> None:
        self._registry.setdefault(group, {})[name] = impl

    def get(self, group: str, name: str):
        return self._registry.get(group, {}).get(name)

    def all(self, group: str) -> dict:
        return self._registry.get(group, {})


registry = ExtensionRegistry()