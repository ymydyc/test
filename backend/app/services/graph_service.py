"""图谱增量构建服务（FR-03）与向量索引（FR-04/FR-05）。

职责（以 MySQL 为权威，Neo4j 可由 MySQL 重建）：
- build_note_vectors：把笔记子块向量化写入 Chroma（父块作上下文，子块 `parent_chunk_id` 关联）。
- build_note_graph：LLM 抽取实体/关系 → 向量相似度全局去重合并 → 写 MySQL → 同步 Neo4j。
- cleanup_note：删笔记时移除其向量与"孤立实体"对应 Neo4j 资源（共享实体保留）。

任何外部依赖（DashScope/Neo4j）不可用时均做优雅降级：不阻断写库主流程（NFR-04）。
"""
from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import DocChunk, GraphEntity, GraphRelation, KbNote
from app.db.scoping import workspace_scope
from app.graphdb.base import GraphStore
from app.graphdb.neo4j_driver import Neo4jGraphStore
from app.services.llm.base import LLMClient
from app.services.llm.dashscope_client import DashScopeClient
from app.services.llm.prompt_templates import extract_entities_user_prompt
from app.vectorstore.base import VectorStore
from app.vectorstore.chroma_store import CHUNK_COLLECTION, ENTITY_COLLECTION, ChromaVectorStore

log = get_logger("services.graph")

# 实体去重相似度阈值（向量匹配全局实体，相同概念合并，避免节点膨胀）
ENTITY_MERGE_THRESHOLD = 0.86
# 单次传给 LLM 抽取的内容上限（超过则分块抽取再合并，保证长笔记实体/关系不被截断遗漏）
EXTRACT_MAX_CHARS = 6000
# 分块间重叠字符数，保证跨块边界处的实体间关系不会被截断漏抽
EXTRACT_CHUNK_OVERLAP = 400


class GraphService:
    def __init__(
        self,
        llm: LLMClient | None = None,
        vs: VectorStore | None = None,
        graph: GraphStore | None = None,
        workspace_id: int = 1,
    ) -> None:
        self.llm = llm or DashScopeClient()
        self.vs = vs or ChromaVectorStore()
        self.graph = graph or Neo4jGraphStore()
        self.workspace_id = workspace_id  # 工作区隔离键（阶段七：按 workspace 隔离）
        self._graph_ok: bool | None = None

    def graph_available(self) -> bool:
        """是否可写图/向量（外部依赖探测，带缓存）。"""
        if self._graph_ok is None:
            self._graph_ok = self.graph.health().get("ok", False)
        return bool(settings.dashscope_api_key) and self._graph_ok

    # ================= 向量索引（子块入向量 + 父块上下文） =================
    def build_note_vectors(self, db: Session, note_id: int) -> dict:
        """把笔记全部子块嵌入 Chroma；父块作为上下文（通过 parent_chunk_id 关联），不入向量本体。"""
        if not settings.dashscope_api_key:
            return {"indexed": 0, "reason": "缺少 DASHSCOPE_API_KEY"}
        chunks = (
            db.query(DocChunk)
            .filter(DocChunk.note_id == note_id, DocChunk.parent_chunk_id.isnot(None))
            .order_by(DocChunk.chunk_index)
            .all()
        )
        if not chunks:
            return {"indexed": 0, "reason": "无子块"}
        texts = [c.chunk_text for c in chunks]
        embs = self.llm.embed(texts)
        # 向量删除与写入均带工作区隔离键，防止跨空间误删/混入他人子块
        self.vs.delete_where(CHUNK_COLLECTION, {"note_id": note_id, "workspace_id": self.workspace_id})
        ids = [f"n{note_id}c{c.id}" for c in chunks]
        metas = [{"note_id": note_id, "chunk_id": c.id, "parent_chunk_id": c.parent_chunk_id or 0,
                  "chunk_index": c.chunk_index, "workspace_id": self.workspace_id} for c in chunks]
        self.vs.add_many(CHUNK_COLLECTION, ids, embs, texts, metas)
        for c, cid in zip(chunks, ids):
            c.chroma_id = cid
        db.commit()
        log.info("已向量化笔记 %s：%s 个子块", note_id, len(chunks))
        return {"indexed": len(chunks)}

    def remove_note_vectors(self, db: Session, note_id: int) -> None:
        self.vs.delete_where(CHUNK_COLLECTION, {"note_id": note_id, "workspace_id": self.workspace_id})

    # ================= 图谱增量构建 =================
    def build_note_graph(self, db: Session, note_id: int) -> dict:
        """LLM 抽取实体/关系 → 去重合并 → 写 MySQL + 同步 Neo4j。"""
        if not settings.dashscope_api_key:
            return {"status": "skipped", "reason": "缺少 DASHSCOPE_API_KEY"}
        note = db.query(KbNote).filter(
            KbNote.id == note_id, workspace_scope(KbNote, self.workspace_id)).first()
        if note is None:
            return {"status": "error", "reason": "笔记不存在"}
        content = note.content_md or ""
        extracted = self._extract_all(content)
        if not extracted or (not extracted["entities"] and not extracted["relations"]):
            return {"status": "skipped", "reason": "无可抽取实体"}

        # 先移除该笔记旧的独占关系（MySQL+Neo4j），再做增量重建
        old_rels = db.query(GraphRelation).filter(
            GraphRelation.source_note_id == note_id,
            workspace_scope(GraphRelation, self.workspace_id)).all()
        self.remove_relations_for_note(db, note_id)

        entity_id_by_name: dict[str, int] = {}
        for ent in extracted["entities"]:
            name = (ent.get("name") or "").strip()
            if not name:
                continue
            eid = self._upsert_entity(db, note_id, name, ent.get("entity_type"), ent.get("description"))
            entity_id_by_name[name] = eid

        rel_count = 0
        for rel in extracted["relations"]:
            s = (rel.get("source") or "").strip()
            t = (rel.get("target") or "").strip()
            rt = (rel.get("relation_type") or "相关").strip()
            sid = entity_id_by_name.get(s)
            tid = entity_id_by_name.get(t)
            if sid is None or tid is None:
                continue
            self._upsert_relation(db, note_id, sid, tid, rt, rel.get("description"))
            rel_count += 1

        self._sync_to_neo4j(db, note_id)
        db.commit()
        log.info("图谱增量完成：note=%s entities=%s relations=%s", note_id, len(entity_id_by_name), rel_count)
        return {"status": "ok", "entities": len(entity_id_by_name), "relations": rel_count}

    def _extract(self, content: str) -> dict | None:
        if not content.strip():
            return None
        try:
            reply = self.llm.generate(extract_entities_user_prompt(content), json_mode=True)
            data = self._parse_json(reply)
        except Exception as e:  # 抽取失败降级，不阻断
            log.warning("LLM 抽取失败：%s", e)
            return None
        if not isinstance(data, dict):
            return None
        return {
            "entities": data.get("entities", []) or [],
            "relations": data.get("relations", []) or [],
        }

    @staticmethod
    def _chunk_content(content: str, max_chars: int = EXTRACT_MAX_CHARS,
                       overlap: int = EXTRACT_CHUNK_OVERLAP) -> list[str]:
        """按字符上限分块（带重叠），返回各块文本；短文本仅一块。"""
        content = content or ""
        if len(content) <= max_chars:
            return [content]
        chunks: list[str] = []
        start = 0
        while start < len(content):
            end = min(start + max_chars, len(content))
            chunks.append(content[start:end])
            if end >= len(content):
                break
            start = max(start + 1, end - overlap)
        return chunks

    def _extract_all(self, content: str) -> dict:
        """分块抽取并跨块合并：实体按同名合并，关系全部汇总，再统一建边。

        相比单次截断前 6000 字符，长笔记不再遗漏后段实体/关系；跨块边界处通过重叠
        和跨块实体名映射，让后块的关系也能正确连到已抽取的实体。
        """
        entities: dict[str, dict] = {}
        relations: list[dict] = []
        for chunk in self._chunk_content(content):
            res = self._extract(chunk)
            if not res:
                continue
            for ent in res["entities"]:
                name = (ent.get("name") or "").strip()
                if not name:
                    continue
                cur = entities.get(name)
                if cur is None:
                    entities[name] = ent
                else:
                    if not cur.get("entity_type") and ent.get("entity_type"):
                        cur["entity_type"] = ent["entity_type"]
                    if not cur.get("description") and ent.get("description"):
                        cur["description"] = ent["description"]
            relations.extend(res["relations"])
        return {"entities": list(entities.values()), "relations": relations}

    @staticmethod
    def _parse_json(text: str) -> dict:
        """从 LLM 回复中稳健提取 JSON 对象。"""
        text = (text or "").strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
        return json.loads(text)

    def _upsert_entity(self, db: Session, note_id: int, name: str, entity_type: str | None, desc: str | None) -> int:
        """实体去重合并（向量相似度匹配同工作区实体），返回图实体 id。"""
        # 实体按 (name, workspace_id) 隔离：同名字跨工作区可各自存在
        existing = db.query(GraphEntity).filter(
            GraphEntity.name == name, workspace_scope(GraphEntity, self.workspace_id)).first()
        if existing is None:
            target_id = self._find_similar_entity(db, name)
            if target_id is not None:
                existing = db.query(GraphEntity).filter(
                    GraphEntity.id == target_id,
                    workspace_scope(GraphEntity, self.workspace_id)).first()
        if existing is not None:
            src = self._note_ids(existing.source_note_ids)
            if note_id not in src:
                src.append(note_id)
            existing.source_note_ids = json.dumps(src, ensure_ascii=False)
            if desc and not existing.description:
                existing.description = desc
            self._ensure_entity_embedding(db, existing, name)
            return existing.id
        ent = GraphEntity(
            name=name, name_hash=_name_hash(name, self.workspace_id),
            entity_type=entity_type or "概念", description=desc,
            source_note_ids=json.dumps([note_id], ensure_ascii=False),
            workspace_id=self.workspace_id,
        )
        db.add(ent)
        db.flush()  # 拿到自增 id 用于 embedding 关联
        self._ensure_entity_embedding(db, ent, name)
        return ent.id

    def _find_similar_entity(self, db: Session, name: str) -> int | None:
        """用实体名向量在同工作区实体库语义匹配，命中阈值返回其 id（合并重复概念）。"""
        try:
            emb = self.llm.embed([name])[0]
            hits = self.vs.query(ENTITY_COLLECTION, [emb], top_k=3,
                                 where={"workspace_id": self.workspace_id})
        except Exception as e:
            log.warning("实体相似匹配失败：%s", e)
            return None
        for h in hits:
            if h["score"] >= ENTITY_MERGE_THRESHOLD:
                target = h["metadata"].get("entity_id")
                if target:
                    return int(target)
        return None

    def _ensure_entity_embedding(self, db: Session, ent: GraphEntity, name: str) -> None:
        """为实体生成/更新其 Embedding 快照（用于后续同工作区去重）。"""
        try:
            emb = self.llm.embed([name])[0]
            eid = f"ent:{ent.id}"
            # 实体向量元数据带 workspace_id，检索时按工作区过滤
            self.vs.add(ENTITY_COLLECTION, eid, emb, name,
                        {"entity_id": ent.id, "name": name, "workspace_id": self.workspace_id})
            ent.embedding_snapshot = 1
        except Exception as e:
            log.warning("实体 Embedding 失败：%s", e)

    def _upsert_relation(self, db: Session, note_id: int, sid: int, tid: int, rt: str, desc: str | None) -> None:
        dup = (
            db.query(GraphRelation)
            .filter(GraphRelation.source_entity_id == sid,
                    GraphRelation.target_entity_id == tid,
                    GraphRelation.relation_type == rt,
                    workspace_scope(GraphRelation, self.workspace_id))
            .first()
        )
        if dup is not None:  # 同对实体同类型关系去重（合并来源笔记）
            return
        db.add(GraphRelation(
            source_entity_id=sid, target_entity_id=tid, relation_type=rt,
            description=desc, source_note_id=note_id, workspace_id=self.workspace_id,
        ))

    def _sync_to_neo4j(self, db: Session, note_id: int) -> None:
        """把该笔记涉及的实体/关系同步写 Neo4j（MERGE 幂等）。"""
        if not self.graph_available():
            log.warning("Neo4j 不可用，跳过同步（MySQL 为权威，可后续重建）")
            return
        # 会话配置了 autoflush=False：刚 add 的实体/关系需显式落库后查询才可见
        db.flush()
        rels = db.query(GraphRelation).filter(
            GraphRelation.source_note_id == note_id,
            workspace_scope(GraphRelation, self.workspace_id)).all()
        ent_ids = {r.source_entity_id for r in rels} | {r.target_entity_id for r in rels}
        ents = db.query(GraphEntity).filter(
            GraphEntity.id.in_(list(ent_ids) or [0]),
            workspace_scope(GraphEntity, self.workspace_id)).all()
        for ent in ents:
            self.graph.upsert_entity(ent.name, ent.entity_type, ent.description,
                                     workspace_id=self.workspace_id)
            ent.neo4j_id = ent.name
        for rel in rels:
            s = db.query(GraphEntity).filter(
                GraphEntity.id == rel.source_entity_id,
                workspace_scope(GraphEntity, self.workspace_id)).first()
            t = db.query(GraphEntity).filter(
                GraphEntity.id == rel.target_entity_id,
                workspace_scope(GraphEntity, self.workspace_id)).first()
            if s and t:
                self.graph.upsert_relation(rel.relation_type, s.name, t.name, rel.description,
                                           rel.source_note_id, str(rel.id),
                                           workspace_id=self.workspace_id)
                rel.neo4j_rel_id = str(rel.id)

    # ================= 清理 =================
    def remove_relations_for_note(self, db: Session, note_id: int) -> None:
        db.query(GraphRelation).filter(
            GraphRelation.source_note_id == note_id,
            workspace_scope(GraphRelation, self.workspace_id)).delete()
        if self.graph_available():
            try:
                self.graph.remove_relations_for_note(note_id, workspace_id=self.workspace_id)
            except Exception as e:
                log.warning("Neo4j 关系清理失败：%s", e)

    def cleanup_note(self, db: Session, note_id: int) -> None:
        """笔记删除后的下游清理：向量 + 该笔记图谱关系（Neo4j）。孤立实体由调用方按 _cleanup_orphan_entities 决定。"""
        self.remove_note_vectors(db, note_id)
        self.remove_relations_for_note(db, note_id)

    # ================= 图谱删除 =================
    def delete_entity(self, db: Session, name: str) -> dict:
        """删除实体节点：先清其全部关系（MySQL 权威 + Neo4j），再删实体、移除实体向量。"""
        ent = db.query(GraphEntity).filter(
            GraphEntity.name == name, workspace_scope(GraphEntity, self.workspace_id)).first()
        if ent is None:
            raise FileNotFoundError("实体不存在")
        eid = ent.id
        db.query(GraphRelation).filter(
            (GraphRelation.source_entity_id == eid) | (GraphRelation.target_entity_id == eid),
            workspace_scope(GraphRelation, self.workspace_id),
        ).delete()
        db.delete(ent)
        db.commit()
        if self.graph_available():
            try:
                self.graph.detach_entity(name, workspace_id=self.workspace_id)
            except Exception as e:
                log.warning("Neo4j 实体删除失败：%s", e)
        try:
            self.vs.delete(ENTITY_COLLECTION, [f"ent:{eid}"])
        except Exception as e:
            log.warning("实体向量删除失败：%s", e)
        log.info("图谱实体已删除：%s", name)
        return {"name": name, "deleted": True}

    def delete_relation(self, db: Session, source: str, target: str, relation_type: str) -> dict:
        """删除指定关系边（MySQL 权威 + Neo4j 同步）。"""
        s = db.query(GraphEntity).filter(
            GraphEntity.name == source, workspace_scope(GraphEntity, self.workspace_id)).first()
        t = db.query(GraphEntity).filter(
            GraphEntity.name == target, workspace_scope(GraphEntity, self.workspace_id)).first()
        if s is None or t is None:
            raise FileNotFoundError("关联实体不存在")
        rel = db.query(GraphRelation).filter(
            GraphRelation.source_entity_id == s.id,
            GraphRelation.target_entity_id == t.id,
            GraphRelation.relation_type == relation_type,
            workspace_scope(GraphRelation, self.workspace_id),
        ).first()
        if rel is None:
            raise FileNotFoundError("关系不存在")
        db.delete(rel)
        db.commit()
        if self.graph_available():
            try:
                self.graph.remove_relation(source, target, relation_type,
                                           workspace_id=self.workspace_id)
            except Exception as e:
                log.warning("Neo4j 关系删除失败：%s", e)
        log.info("图谱关系已删除：%s→%s·%s", source, target, relation_type)
        return {"source": source, "target": target, "relation_type": relation_type, "deleted": True}

    # ---------- 工具 ----------
    @staticmethod
    def _note_ids(raw: str | None) -> list[int]:
        if not raw:
            return []
        try:
            return [x for x in json.loads(raw) if isinstance(x, int)]
        except (ValueError, TypeError):
            return []


def _name_hash(name: str, workspace_id: int) -> str:
    import hashlib
    # 阶段七：把 workspace_id 纳入哈希盐，跨工作区同名实体各自独立唯一（graph_entities.name_hash 唯一约束）
    return hashlib.sha256(f"{workspace_id}\x00{name}".encode("utf-8")).hexdigest()