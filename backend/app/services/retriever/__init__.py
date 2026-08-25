"""检索器插件化出口：统一构建向量/图/混合协调器。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.graphdb.neo4j_driver import Neo4jGraphStore
from app.services.llm.dashscope_client import DashScopeClient
from app.services.retriever import coordinator
from app.services.retriever.base import Retriever  # noqa: F401  (类型复用)
from app.services.retriever.coordinator import SearchCoordinator
from app.services.retriever.graph_retriever import GraphRetriever
from app.services.retriever.vector_retriever import VectorRetriever
from app.vectorstore.chroma_store import ChromaVectorStore


def build_coordinator(db: Session, top_k: int = 8) -> SearchCoordinator:
    """构建混合检索协调器（向量 + 图；图依赖 Neo4j，失败自动降级）。"""
    llm = DashScopeClient()
    vs = ChromaVectorStore()
    vector = VectorRetriever(llm=llm, vs=vs, db=db, top_k=top_k)
    graph_store: Neo4jGraphStore | None = None
    try:
        gs = Neo4jGraphStore()
        if gs.health().get("ok"):
            gs.ensure_schema()
            graph_store = gs
    except Exception:
        graph_store = None
    graph = GraphRetriever(llm=llm, vs=vs, graph=graph_store) if graph_store else None
    retrievers: list[Retriever] = [vector]
    if graph is not None:
        retrievers.append(graph)
    return SearchCoordinator(retrievers=retrievers)