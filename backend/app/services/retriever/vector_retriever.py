"""向量检索器（FR-04）：子块精排 + 父块上下文增强。

流程：
1. 把查询文本 Embedding，在 `chunks` collection 检索 top_k 子块（语义精排）。
2. 命中的独立子块数量不足 top_k 时，用连续 chunk_index 的相邻子块补足（保持段落连贯）。
3. 对每个命中子块，按其 parent_chunk_id 回溯父块——父块是标题章节大段，提供**完整章节上下文增强**；
   若命中子块缺失父块，则退化为仅该子块，不阻塞。
4. 每条结果附 note_id/chunk_id/parent_chunk_id 供溯源与图谱联动。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import DocChunk
from app.db.scoping import workspace_scope
from app.services.llm.base import LLMClient
from app.services.retriever.base import Retriever
from app.vectorstore.base import VectorStore
from app.vectorstore.chroma_store import CHUNK_COLLECTION

log = get_logger("services.retriever.vector")


class VectorRetriever(Retriever):
    def __init__(
        self,
        llm: LLMClient,
        vs: VectorStore,
        db: Session,
        top_k: int = 8,
        neighbors: int = 2,
        exhaustive_parent_trackback: bool = True,
        workspace_id: int = 1,
    ) -> None:
        self.llm = llm
        self.vs = vs
        self.db = db
        self.top_k = top_k
        self.neighbors = neighbors  # 命中子块两侧补充的相邻子块数（语境连续）
        # 父块回溯增强开关（FR-04：向量命中子块后回溯父块提供完整章节上下文）
        self.exhaustive_parent_trackback = exhaustive_parent_trackback
        self.workspace_id = workspace_id  # 工作区隔离键（阶段七：按 workspace 隔离）

    def retrieve(self, query: str, top_k: int | None = None) -> list[dict]:
        k = top_k or self.top_k
        query_emb = self.llm.embed([query])[0]
        # 向量检索按工作区隔离，防止跨空间取到他人子块
        hits = self.vs.query(
            CHUNK_COLLECTION, [query_emb], top_k=k,
            where={"workspace_id": self.workspace_id},
        )
        if not hits:
            return []

        # 去重（同 chunk 只留一次）+ 记录命中子块
        chunk_ids: list[int] = []
        seen: set[int] = set()
        for h in hits:
            cid = h["metadata"].get("chunk_id")
            if cid is not None and cid not in seen:
                seen.add(int(cid))
                chunk_ids.append(int(cid))
        if not chunk_ids:
            return []

        # 命中子块也按工作区过滤，防止跨空间取到他人子块
        rows = (
            self.db.query(DocChunk)
            .filter(DocChunk.id.in_(chunk_ids), workspace_scope(DocChunk, self.workspace_id))
            .all()
        )
        rows_by_id = {r.id: r for r in rows}

        # 权威 note_id（全部命中来自同一批子块，取首个有值者）
        note_id = next((r.note_id for r in rows if r.note_id), None)

        results: list[dict] = []
        used: set[int] = set()
        for cid in chunk_ids:
            row = rows_by_id.get(cid)
            if row is None:
                continue
            text = row.chunk_text
            parent_text = None
            if self.exhaustive_parent_trackback and row.parent_chunk_id:
                parent = (
                    self.db.query(DocChunk)
                    .filter(DocChunk.id == row.parent_chunk_id,
                            workspace_scope(DocChunk, self.workspace_id))
                    .first()
                )
                parent_text = parent.chunk_text if parent else None
            used.add(row.id)
            results.append({
                "id": f"c{row.id}",
                "title": f"段落{row.chunk_index}",
                "text": text,
                "parent_text": parent_text,  # 父块上下文增强（FR-04）
                "score": float(next((h["score"] for h in hits if h["metadata"].get("chunk_id") == row.id), 0.0)),
                "retriever": "vector",
                "metadata": {
                    "note_id": row.note_id,
                    "chunk_id": row.id,
                    "parent_chunk_id": row.parent_chunk_id or 0,
                    "chunk_index": row.chunk_index,
                },
            })

        # 语境连续增强：对每个命中子块，补其同一章节内相邻子块（未命中但也可能相关）
        if note_id and self.neighbors > 0:
            # 一次性取该笔记全部子块，按 (parent_chunk_id, chunk_index) 定位相邻
            all_childs = (
                self.db.query(DocChunk)
                .filter(DocChunk.note_id == note_id,
                        workspace_scope(DocChunk, self.workspace_id),
                        DocChunk.parent_chunk_id.isnot(None))
                .order_by(DocChunk.parent_chunk_id, DocChunk.chunk_index)
                .all()
            )
            grouped: dict[int, list[DocChunk]] = {}
            for c in all_childs:
                grouped.setdefault(c.parent_chunk_id, []).append(c)

            for res in list(results):
                pid = res["metadata"]["parent_chunk_id"] or 0
                siblings = [c for c in grouped.get(pid, []) if c.id not in used]
                if not siblings:
                    continue
                order = [c.chunk_index for c in siblings]
                nid = res["metadata"]["chunk_id"]
                pos = order.index(res["metadata"]["chunk_index"]) if res["metadata"]["chunk_index"] in order else 0
                lo = max(0, pos - self.neighbors)
                hi = min(len(siblings), pos + self.neighbors + 1)
                for n in siblings[lo:hi]:
                    if n.id in used or n.id == nid:
                        continue
                    used.add(n.id)
                    parent = None
                    if self.exhaustive_parent_trackback and n.parent_chunk_id:
                        parent = self.db.query(DocChunk).filter(
                            DocChunk.id == n.parent_chunk_id,
                            workspace_scope(DocChunk, self.workspace_id),
                        ).first()
                    results.append({
                        "id": f"c{n.id}",
                        "title": f"段落{n.chunk_index}",
                        "text": n.chunk_text,
                        "parent_text": parent.chunk_text if parent else None,
                        "score": 0.0,  # 非直接命中，得分低置；RRF 按排序而非分值
                        "retriever": "vector",
                        "metadata": {
                            "note_id": n.note_id,
                            "chunk_id": n.id,
                            "parent_chunk_id": n.parent_chunk_id or 0,
                            "chunk_index": n.chunk_index,
                        },
                    })
        return results if not top_k else results[:top_k]