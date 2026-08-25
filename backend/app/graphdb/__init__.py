"""图数据库封装导出。"""
from __future__ import annotations

from app.graphdb.base import GraphStore
from app.graphdb.neo4j_driver import Neo4jGraphStore


def get_graphstore() -> Neo4jGraphStore:
    """返回全局 Neo4j 图存储实例。"""
    return Neo4jGraphStore()