"""向量库抽象接口（可插拔：Chroma/FAISS/Qdrant…）。"""
from __future__ import annotations

from abc import ABC, abstractmethod


class VectorStore(ABC):
    """向量库最小接口：语义索引与检索。

    collection 命名约定：`chunks`（子块向量，供文档检索）、`entities`（实体名向量，供实体去重）。
    """

    @abstractmethod
    def add(
        self,
        collection: str,
        id: str,
        embedding: list[float],
        document: str,
        metadata: dict | None = None,
    ) -> None: ...

    @abstractmethod
    def add_many(
        self,
        collection: str,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict] | None = None,
    ) -> None: ...

    @abstractmethod
    def query(
        self,
        collection: str,
        query_embeddings: list[list[float]],
        top_k: int = 10,
        where: dict | None = None,
    ) -> list[dict]:
        """检索，返回 [{id, document, metadata, score}]（按相似度降序）。"""

    @abstractmethod
    def delete(self, collection: str, ids: list[str]) -> None: ...

    @abstractmethod
    def delete_where(self, collection: str, where: dict) -> None: ...

    @abstractmethod
    def count(self, collection: str) -> int: ...