"""Chroma 向量库实现（本地持久化 + 维度配平校验）。

对齐 FR-03/FR-05：`chunks`（子块入向量、父块作上下文元数据）、`entities`（实体名向量做语义去重）。
Embedding 维度需与 collection 固定维度校验配平（text-embedding-v4 = 1024，见 config）。
"""
from __future__ import annotations

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.core.config import settings
from app.core.logging import get_logger
from app.vectorstore.base import VectorStore

log = get_logger("vectorstore.chroma")

CHUNK_COLLECTION = "chunks"
ENTITY_COLLECTION = "entities"


def _where_and(filters: dict | None) -> dict | None:
    """把多条件 where 字典归一为 Chroma 的 `$and` 表达式。

    （阶段七）检索/删除常需 `{"note_id": N, "workspace_id": M}` 双条件过滤；
    Chroma>=1.x 顶层只接受**单个**操作符，多键平铺会被校验为
    "Expected where to have exactly one operator"，故显式拆成 `{"$and": [...]}`。
    单键时原样返回，避免不必要的转换。
    """
    if not filters:
        return filters
    if len(filters) <= 1:
        return filters
    return {"$and": [{k: v} for k, v in filters.items()]}


class ChromaVectorStore(VectorStore):
    def __init__(self, path: str | None = None, dim: int | None = None) -> None:
        self.persist_dir = path or str(settings.chroma_dir)
        self.embedding_dim = dim or settings.embedding_dim
        # chromadb>=1.x 用 PersistentClient + 匿名 telemetry，避免默认收集
        self._client = chromadb.PersistentClient(
            path=self.persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collections: dict[str, "chromadb.Collection"] = {}

    def _collection(self, name: str):
        if name not in self._collections:
            self._collections[name] = self._client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine", "dim": self.embedding_dim},
            )
        return self._collections[name]

    def _check_dim(self, embeddings) -> None:
        for emb in embeddings:
            if len(emb) != self.embedding_dim:
                raise ValueError(
                    f"Embedding 维度 {len(emb)} 与配置维度 {self.embedding_dim} 不配平"
                )

    # ---------- 写入 ----------
    def add(self, collection: str, id: str, embedding: list[float], document: str, metadata: dict | None = None) -> None:
        self.add_many(collection, [id], [embedding], [document], [metadata] if metadata else None)

    def add_many(self, collection: str, ids: list[str], embeddings: list[list[float]], documents: list[str], metadatas: list[dict] | None = None) -> None:
        if not ids:
            return
        self._check_dim(embeddings)
        c = self._collection(collection)
        if len({len(e) for e in embeddings}) != 1:
            raise ValueError("同一批次 Embedding 维度必须一致")
        try:
            c.upsert(ids=ids, embeddings=embeddings, documents=documents,
                     metadatas=metadatas or [{}] * len(ids))
        except Exception:
            # 若 collection 已有不同维度数据，upsert 报错 → 删除该批后重试（维度不符按不配平处理）
            log.warning("Chroma upsert 失败，尝试删除后重写：collection=%s", collection)
            c.delete(ids=ids)
            try:
                c.add(ids=ids, embeddings=embeddings, documents=documents,
                      metadatas=metadatas or [{}] * len(ids))
            except Exception as e:  # pragma: no cover
                raise ValueError(f"Chroma 写入失败（维度不配平或数据异常）：{e}") from e

    # ---------- 检索 ----------
    def query(self, collection: str, query_embeddings: list[list[float]], top_k: int = 10, where: dict | None = None) -> list[dict]:
        if not query_embeddings:
            return []
        c = self._collection(collection)
        kwargs: dict = {"query_embeddings": query_embeddings, "n_results": top_k}
        if where:
            kwargs["where"] = _where_and(where)
        if c.count() == 0:
            return []
        result = c.query(**kwargs)
        out: list[dict] = []
        for i in range(len(query_embeddings)):
            ids = result["ids"][i]
            docs = result["documents"][i]
            metas = result["metadatas"][i]
            dists = result["distances"][i]  # cosine distance（越小越相似）
            for j, rid in enumerate(ids):
                out.append({
                    "id": rid,
                    "document": docs[j],
                    "metadata": metas[j] or {},
                    "score": 1.0 - float(dists[j]),  # 转为相似度（越大越相关）
                })
        return out

    # ---------- 删除 / 统计 ----------
    def delete(self, collection: str, ids: list[str]) -> None:
        if ids:
            self._collection(collection).delete(ids=ids)

    def delete_where(self, collection: str, where: dict) -> None:
        c = self._collection(collection)
        existing = c.get(where=_where_and(where))
        ids = existing.get("ids") or []
        if ids:
            c.delete(ids=ids)

    def count(self, collection: str) -> int:
        return self._collection(collection).count()