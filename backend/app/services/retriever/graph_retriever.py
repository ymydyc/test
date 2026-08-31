"""图谱检索器（FR-04）：基于实体名做图路径多跳召回。

流程：
1. 用查询 Embedding 在 `entities` collection 检索最相关实体（语义定位）。
2. 以命中的实体名做多跳邻域展开（Neo4j neighbor_search）。
3. 返回展开出的实体节点与其关系边，作为图路径证据；并顺带通过实体来源笔记关联回知识库。

依赖 Neo4j 可用；Neo4j 不可用时降级为空结果（不阻断混合检索）。
"""
from __future__ import annotations

from app.core.config import settings
from app.core.logging import get_logger
from app.graphdb.base import GraphStore
from app.services.llm.base import LLMClient
from app.services.retriever.base import Retriever
from app.vectorstore.base import VectorStore
from app.vectorstore.chroma_store import ENTITY_COLLECTION

log = get_logger("services.retriever.graph")


class GraphRetriever(Retriever):
    def __init__(
        self,
        llm: LLMClient,
        vs: VectorStore,
        graph: GraphStore | None,
        hops: int = 1,
        seed_entities: int = 3,
        limit: int = 60,
        workspace_id: int = 1,
    ) -> None:
        self.llm = llm
        self.vs = vs
        self.graph = graph
        self.hops = hops
        self.seed_entities = seed_entities
        self.limit = limit
        self.workspace_id = workspace_id  # 工作区隔离键（阶段七：按 workspace 隔离）

    def retrieve(self, query: str, top_k: int | None = None) -> list[dict]:
        k = top_k or 10
        if self.graph is None or not self._graph_ok():
            return []
        try:
            qemb = self.llm.embed([query])[0]
            # 实体向量检索按工作区隔离
            seed_hits = self.vs.query(
                ENTITY_COLLECTION, [qemb], top_k=self.seed_entities,
                where={"workspace_id": self.workspace_id},
            )
        except Exception as e:  # 语义定位失败 → 不能定位种子，返回空
            log.warning("图检索种子定位失败：%s", e)
            return []
        seed_names = [str(h["metadata"].get("name", "")) for h in seed_hits]
        seed_names = [n for n in seed_names if n]
        if not seed_names:
            return []
        try:
            # 图多跳邻域展开同样按工作区隔离（Neo4j 侧按 workspace_id 过滤）
            sub = self.graph.neighbor_search(
                seed_names, hops=self.hops, limit=self.limit, workspace_id=self.workspace_id,
            )
        except Exception as e:  # Neo4j 不可用/查询失败 → 降级
            log.warning("图多跳检索失败：%s", e)
            return []
        nodes = sub.get("nodes", []) or []
        edges = sub.get("edges", []) or []
        # 组织为结果条目：每个命中节点一条，携带相邻边供溯源
        results: list[dict] = []
        for n in nodes:
            results.append({
                "id": f"g:{n.get('name','')}",
                "title": n.get("name", ""),
                "text": f"{n.get('entity_type', '实体')}：{n.get('name', '')}",
                "score": 1.0 if n.get("name") in seed_names else 0.5,
                "retriever": "graph",
                "metadata": {
                    "entity_name": n.get("name", ""),
                    "entity_type": n.get("entity_type", ""),
                    "matched": n.get("name", "") in seed_names,
                    "edges": [e for e in edges if e.get("source") == n.get("name") or e.get("target") == n.get("name")],
                },
            })
        return results[:k]

    def _graph_ok(self) -> bool:
        if self.graph is None:
            return False
        try:
            return bool(self.graph.health().get("ok", False))
        except Exception:
            return False