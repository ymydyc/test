"""混合检索协调器（FR-04）：向量 + 图路径并行 → RRF 融合。

- 注入多个 Retriever（至少向量；图检索可选，Neo4j 不可用时自动降级）。
- RRF(Reciprocal Rank Fusion)：score = Σ 1/(k + rank)，k=60 常用。
- 输出去重（保留最高 RRF 分）并按融合分降序；附每个子结果出处与父块上下文增强。
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.services.retriever.base import Retriever

log = get_logger("services.retriever.coordinator")

RRF_K = 60  # RRF 平滑常数（标准取 60）


class SearchCoordinator:
    def __init__(
        self,
        retrievers: list[Retriever],
        vector_retriever: Retriever | None = None,
    ) -> None:
        # 兼容旧式：单独传 vector_retriever 也并入
        self.retrievers = list(retrievers)
        if vector_retriever is not None and all(r is not vector_retriever for r in self.retrievers):
            self.retrievers.append(vector_retriever)

    def search(self, query: str, top_k: int = 8) -> dict:
        """执行混合检索，返回 {query, results:[...], sources}。

        results 每项：{id,title,text,parent_text,score,retriever,metadata}
        其中 score 为 RRF 融合分；sources['vector']/['graph'] 保留各自原始列表以便前端展示。
        """
        sources: dict[str, list] = {}
        rank_lists: list[list[tuple[int, dict]]] = []  # 每个 retriever 的 (原始rank, result)

        for ret in self.retrievers:
            name = getattr(ret, "name", type(ret).__name__)
            try:
                items = ret.retrieve(query, top_k=top_k * 2)
            except Exception as e:  # 单个检索器失败不阻断整体
                log.warning("检索器 %s 失败：%s", name, e)
                items = []
            sources[name] = items
            rank_lists.append([(idx, it) for idx, it in enumerate(items)])

        # RRF 融合
        fused: dict[str, dict] = {}
        for ranking in rank_lists:
            for idx, item in ranking:
                key = item["id"]
                rrf = 1.0 / (RRF_K + idx)
                if key in fused:
                    fused[key]["score"] += rrf
                else:
                    clone = dict(item)
                    clone["score"] = rrf
                    fused[key] = clone

        results = sorted(fused.values(), key=lambda x: x["score"], reverse=True)
        if top_k and len(results) > top_k:
            results = results[:top_k]
        return {"query": query, "results": results, "sources": sources}