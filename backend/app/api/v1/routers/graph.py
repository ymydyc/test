"""图谱 API（阶段三：导出 / 构建状态 / 节点连线详情）。

- GET /graph/export   → 可视化数据源 {nodes, edges}（优先 Neo4j，降级 MySQL）。
- GET /graph/status   → DashScope / Neo4j / 向量/图谱 就绪状态，前端由此决定图谱按钮可点。
- POST /graph/build   → 对指定笔记批量重建图谱（增量同步触发入口）。
- GET /graph/node/{name} → 某实体的详情（描述、来源笔记、关联边）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.engine import get_db
from app.graphdb.neo4j_driver import Neo4jGraphStore
from app.schemas.graph import GraphBuildRequest
from app.services.graph_service import GraphService
from app.vectorstore.chroma_store import ChromaVectorStore

router = APIRouter(prefix="/graph", tags=["图谱"])


def _graph_service() -> GraphService:
    return GraphService()


def _as_http(e: Exception, code: int = 500) -> HTTPException:
    return HTTPException(status_code=code, detail=str(e))


@router.get("/status", summary="图谱/向量/LLM 就绪状态")
def status():
    gs = _graph_service()
    return {
        "dashscope": bool(settings.dashscope_api_key),
        "llm": {
            "model": settings.llm_model,
            "embedding_model": settings.embedding_model,
            "embedding_dim": settings.embedding_dim,
        },
        "graph": gs.graph_available() if settings.dashscope_api_key else False,
        "vector": {
            "chunk_count": ChromaVectorStore().count("chunks"),
            "entity_count": ChromaVectorStore().count("entities"),
        },
        "ready": bool(settings.dashscope_api_key) and gs.graph_available(),
    }


@router.get("/export", summary="导出图谱可视化数据源（节点+边）")
def export(limit: int = 1000, db: Session = Depends(get_db)):
    if not settings.dashscope_api_key:
        raise _as_http(RuntimeError("未配置 DASHSCOPE_API_KEY，图谱不可用"), 400)
    from app.db.models import GraphEntity, GraphRelation
    try:
        store = Neo4jGraphStore()
        health = store.health()
        if health.get("ok"):
            return store.fetch_graph(limit=limit)
        # Neo4j 不可用 → 降级 MySQL 导出（图谱仍可见）
        # 注意：节点 id 使用实体名（与 Neo4j 导出一致），边端点也必须用实体名，
        # 否则前端 Cytoscape 会报 “nonexistent source”（此前误用实体 DB 整数 id 导致）。
        ents = db.query(GraphEntity).order_by(GraphEntity.id).limit(limit).all()
        nodes = [{"id": e.name, "name": e.name, "entity_type": e.entity_type} for e in ents]
        name_by_id = {e.id: e.name for e in ents}
        edges = []
        for r in db.query(GraphRelation).limit(limit).all():
            s, t = name_by_id.get(r.source_entity_id), name_by_id.get(r.target_entity_id)
            if not s or not t:
                continue
            edges.append({"id": f"{s}::{t}::{r.relation_type}",
                          "source": s, "target": t,
                          "relation_type": r.relation_type, "description": r.description})
        return {"nodes": nodes, "edges": edges}
    except Exception as e:  # pragma: no cover
        raise _as_http(e)


@router.post("/build", summary="批量重建指定笔记的图谱（增量同步触发入口）")
def build(payload: GraphBuildRequest, db: Session = Depends(get_db)):
    gs = _graph_service()
    if not settings.dashscope_api_key:
        raise _as_http(RuntimeError("未配置 DASHSCOPE_API_KEY"), 400)
    out: list[dict] = []
    for nid in payload.note_ids:
        try:
            res = gs.build_note_graph(db, nid)
            out.append({"note_id": nid, **res})
        except Exception as e:  # 单笔记失败不中断批量
            out.append({"note_id": nid, "status": "error", "reason": str(e)})
    return {"results": out}


@router.get("/node/{name}", summary="实体节点详情（描述、来源笔记、关联边）")
def node_detail(name: str, db: Session = Depends(get_db)):
    from app.db.models import GraphEntity, GraphRelation, KbNote
    # 优先 Neo4j（与 /graph/export 数据源一致，避免「图上有、库中无」时 404）
    store = Neo4jGraphStore()
    try:
        if store.health().get("ok"):
            nd = store.node_detail(name)
            if nd is not None:
                docs = ([{"note_id": n.id, "title": n.title, "note_path": n.note_path}
                         for n in db.query(KbNote).filter(KbNote.id.in_(list(nd["source_note_ids"]) or [0])).all()]
                        if nd["source_note_ids"] else [])
                return {**nd, "documents": docs}
    except Exception:  # Neo4j 查询异常 → 降级 MySQL
        pass
    ent = db.query(GraphEntity).filter(GraphEntity.name == name).first()
    if ent is None:
        raise _as_http(RuntimeError("实体不存在"), 404)
    rels = (
        db.query(GraphRelation)
        .filter((GraphRelation.source_entity_id == ent.id) | (GraphRelation.target_entity_id == ent.id))
        .all()
    )
    edges: list[dict] = []
    note_ids: set[int] = set()
    for r in rels:
        s = db.query(GraphEntity).filter(GraphEntity.id == r.source_entity_id).first()
        t = db.query(GraphEntity).filter(GraphEntity.id == r.target_entity_id).first()
        if s and t:
            edges.append({"source": s.name, "target": t.name,
                          "relation_type": r.relation_type, "description": r.description})
            if r.source_note_id:
                note_ids.add(r.source_note_id)
    docs = [{"note_id": n.id, "title": n.title, "note_path": n.note_path}
            for n in db.query(KbNote).filter(KbNote.id.in_(list(note_ids) or [0])).all()] if note_ids else []
    return {
        "name": ent.name, "entity_type": ent.entity_type, "description": ent.description,
        "source_note_ids": ent.source_note_ids, "edges": edges, "documents": docs,
    }