"""向量库封装导出。"""
from __future__ import annotations

from app.vectorstore.base import VectorStore
from app.vectorstore.chroma_store import CHUNK_COLLECTION, ENTITY_COLLECTION, ChromaVectorStore


def get_vectorstore() -> ChromaVectorStore:
    """返回全局 Chroma 向量库实例。"""
    return ChromaVectorStore()