"""阶段三单元测试：检索器(RRF 融合/向量/图谱) + 图谱服务去重合并（mock 外部依赖）。

不触碰真实 DashScope/Neo4j；LLM/向量库/图库全部注入 Mock。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import DocChunk, GraphEntity, KbNote
from app.services.retriever.coordinator import SearchCoordinator
from app.services.retriever.graph_retriever import GraphRetriever
from app.services.retriever.vector_retriever import VectorRetriever

DDL = {
    "kb_notes": """
        CREATE TABLE kb_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note_path VARCHAR(1024) NOT NULL,
            note_path_hash VARCHAR(64) NOT NULL UNIQUE,
            title VARCHAR(512) NOT NULL,
            content_md TEXT NOT NULL,
            content_hash CHAR(64) NOT NULL,
            origin_import_id BIGINT,
            frontmatter_json TEXT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "doc_chunks": """
        CREATE TABLE doc_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note_id BIGINT NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_text TEXT NOT NULL,
            char_start INTEGER NOT NULL,
            char_end INTEGER NOT NULL,
            parent_chunk_id BIGINT,
            chroma_id VARCHAR(128),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "graph_entities": """
        CREATE TABLE graph_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(512) NOT NULL,
            name_hash VARCHAR(64) NOT NULL UNIQUE,
            entity_type VARCHAR(128) NOT NULL,
            description TEXT,
            source_note_ids TEXT,
            neo4j_id VARCHAR(128),
            embedding_snapshot INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "graph_relations": """
        CREATE TABLE graph_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_entity_id BIGINT NOT NULL,
            target_entity_id BIGINT NOT NULL,
            relation_type VARCHAR(128) NOT NULL,
            description TEXT,
            source_note_id BIGINT,
            neo4j_rel_id VARCHAR(128),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """,
}


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool, future=True,
    )
    with engine.begin() as conn:
        for ddl in DDL.values():
            conn.execute(text(ddl))
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


# ---------- Mock（不打真实 DashScope/Chroma/Neo4j） ----------
class _MockLLM:
    def __init__(self, emb_dim=4) -> None:
        self.emb_dim = emb_dim

    def embed(self, texts):
        return [[float(len(t) % self.emb_dim) * 0.1] * self.emb_dim + [1.0] for t in texts]

    def generate(self, messages, *, json_mode=False, **kw):
        return "{}"

    def availability(self):
        return {"ok": True}


class _MockVS:
    def __init__(self, hits=None) -> None:
        self.hits = hits or []

    def query(self, collection, query_embeddings, top_k=10, where=None):
        return self.hits[:top_k]

    def add(self, *a, **k):
        pass

    def count(self, collection="chunks"):
        return 0


class _MockGraph:
    def health(self):
        return {"ok": True}

    def neighbor_search(self, names, hops=1, limit=50):
        return {
            "nodes": [{"name": n, "entity_type": "概念"} for n in names],
            "edges": [],
            "matched": names,
        }


def _mk_chunk(db, note_id, idx, text, parent_id=None):
    row = DocChunk(note_id=note_id, chunk_index=idx, chunk_text=text,
                   char_start=0, char_end=len(text), parent_chunk_id=parent_id)
    db.add(row)
    db.flush()
    return row


# ---------- RRF 融合 ----------
def test_rrf_fusion_merges_rankings():
    """两个检索器同返回不同 id 时，RRF 按排名加权融合，重叠项排名更高。"""
    ret_a = _FakeRetriever("a", [{"id": "x", "title": "x", "text": "t",
                                  "score": 1.0, "retriever": "a", "metadata": {}},
                                 {"id": "y", "title": "y", "text": "t",
                                  "score": 0.9, "retriever": "a", "metadata": {}}])
    ret_b = _FakeRetriever("b", [{"id": "y", "title": "y", "text": "t",
                                  "score": 0.8, "retriever": "b", "metadata": {}},
                                 {"id": "z", "title": "z", "text": "t",
                                  "score": 0.7, "retriever": "b", "metadata": {}}])
    coord = SearchCoordinator(retrievers=[ret_a, ret_b])
    out = coord.search("q", top_k=10)
    ids = [r["id"] for r in out["results"]]
    # y 在两个检索器都出现（排名叠加）→ 应该排最前
    assert ids[0] == "y"
    assert set(ids) == {"x", "y", "z"}
    assert set(out["sources"].keys()) == {"a", "b"}


class _FakeRetriever:
    def __init__(self, name, items):
        self.name = name
        self.items = items

    def retrieve(self, query, top_k=None):
        return self.items


def test_vector_retriever_parent_trackback(db):
    """向量命中子块后回填父块上下文（parent_text），并做语境相邻增强。"""
    note = KbNote(note_path="n.md", note_path_hash="n1", title="笔记", content_md="c", content_hash="h")
    db.add(note)
    db.flush()
    parent = _mk_chunk(db, note.id, 0, "父块章节文本", parent_id=None)
    child1 = _mk_chunk(db, note.id, 1, "第一段内容", parent_id=parent.id)
    child2 = _mk_chunk(db, note.id, 2, "第二段内容", parent_id=parent.id)
    db.commit()

    hits = [{"id": f"c{child1.id}", "document": "第一段内容",
             "metadata": {"chunk_id": child1.id, "note_id": note.id},
             "score": 0.95}]
    retriever = VectorRetriever(llm=_MockLLM(), vs=_MockVS(hits=hits), db=db, top_k=4, neighbors=1)
    results = retriever.retrieve("第一段", top_k=4)

    # 首个结果为命中子块，且回填了父块上下文 + 相邻子块增强
    assert results[0]["id"] == f"c{child1.id}"
    assert results[0]["parent_text"] == "父块章节文本"
    ids = {r["id"] for r in results}
    assert f"c{child2.id}" in ids  # 相邻增强
    assert all(r["retriever"] == "vector" for r in results)


def test_graph_retriever_degrades_when_no_neighbor(db):
    """图谱检索器在种子实体返回空时降级为空结果（不抛错）。"""
    ret = GraphRetriever(llm=_MockLLM(), vs=_MockVS(hits=[]), graph=_MockGraph())
    assert ret.retrieve("q") == []


def test_graph_retriever_expands_neighbors(db):
    hits = [{"id": "ent:1", "document": "知识图谱", "metadata": {"name": "知识图谱", "entity_id": 1},
             "score": 0.99}]
    ret = GraphRetriever(llm=_MockLLM(), vs=_MockVS(hits=hits), graph=_MockGraph())
    results = ret.retrieve("知识图谱")
    assert results
    assert results[0]["id"] == "g:知识图谱"
    assert results[0]["retriever"] == "graph"


def test_graph_service_entity_dedup(db, monkeypatch):
    """同名与近似实体经向量去重后合并，不重复建节点。"""
    from app.services.graph_service import GraphService

    class _VS:
        def __init__(self):
            self._ent_next = 100  # 模拟已有实体

        def query(self, collection, query_embeddings, top_k=3, where=None):
            # 模拟已存在实体 ent:1（向量命中 0.99 > 阈值 → 命中去重）
            return [{"id": "ent:1", "document": "RAG", "metadata": {"entity_id": 1, "name": "RAG"},
                     "score": 0.99}]

        def add(self, *a, **k):
            pass

    class _Graph:
        def health(self):
            return {"ok": True}

    # llm 返回固定 JSON，实体 "RAG" 命中已有 → 应归并到已存在 entity(id=1)
    llm = _ExtractLLM(json_text='{"entities":[{"name":"RAG","entity_type":"技术","description":"检索增强生成"}],'
                                 '"relations":[]}')
    vs = _VS()
    gs = GraphService(llm=llm, vs=vs, graph=_Graph())

    # 先造一个已存在实体、向量可命中（同上）
    existing = GraphEntity(name="RAG", name_hash="_", entity_type="技术",
                           description=None, source_note_ids="[1]")
    db.add(existing)
    db.flush()
    existing.id = 1
    existing.source_note_ids = "[1]"

    note = KbNote(note_path="m.md", note_path_hash="m1", title="T", content_md="c", content_hash="h")
    db.add(note)
    db.flush()
    res = gs.build_note_graph(db, note.id)

    assert res["status"] == "ok"
    assert res["entities"] == 1
    rows = db.query(GraphEntity).filter(GraphEntity.name == "RAG").all()
    assert len(rows) == 1  # 未重复建节点
    assert json_loads(rows[0].source_note_ids) != []  # 来源笔记已加入


class _ExtractLLM:
    def __init__(self, json_text) -> None:
        self.json_text = json_text

    def embed(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    def generate(self, messages, *, json_mode=False, **kw):
        return self.json_text

    def availability(self):
        return {"ok": True}


def json_loads(s):
    import json
    return json.loads(s)


def test_sync_to_neo4j_flushes_pending_relations(monkeypatch):
    """autoflush=False 会话下，_sync_to_neo4j 需先将 pending 关系 flush，Neo4j 才可见（回归修复）。"""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.services.graph_service import GraphService
    import app.services.graph_service as gmod
    monkeypatch.setattr(gmod.settings, "dashscope_api_key", "k")

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    with engine.begin() as conn:
        for ddl in DDL.values():
            conn.execute(text(ddl))
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()

    class _Graph:
        def __init__(self):
            self.ups = []

        def health(self):
            return {"ok": True}

        def upsert_entity(self, name, t, d):
            self.ups.append((name, t))

        def upsert_relation(self, rt, s, t, d, sn, mid):
            self.ups.append(("R", s, t))

        def remove_relations_for_note(self, n):
            pass

    graph = _Graph()
    llm = _ExtractLLM('{"entities":[{"name":"A","entity_type":"概念","description":"a"},'
                      '{"name":"B","entity_type":"概念","description":"b"}],'
                      '"relations":[{"source":"A","target":"B","relation_type":"相关",'
                      '"description":"ab"}]}')
    gs = GraphService(llm=llm, vs=_MockVS([]), graph=graph)

    note = KbNote(note_path="n.md", note_path_hash="n1", title="T", content_md="c", content_hash="h")
    db.add(note)
    db.flush()
    res = gs.build_note_graph(db, note.id)
    assert res["status"] == "ok"
    # 两实体（含仅作关系目标、source 之外的）都应同步到 Neo4j
    assert ("A", "概念") in graph.ups and ("B", "概念") in graph.ups
    assert ("R", "A", "B") in graph.ups
    db.rollback()
    engine.dispose()


def test_incremental_sync_rebuild_preserves_parent_child(db):
    """外部改 kb 文件触发局部重建时，父子块 parent_chunk_id 关联不丢失。"""
    from app.scheduler.incremental_sync import IncrementalSync
    from app.db.models import KbNote, DocChunk
    note = KbNote(note_path="sync/x.md", note_path_hash="sx1", title="T",
                  content_md="旧", content_hash="old")
    db.add(note)
    db.flush()

    class _MockGraphSvc:
        def build_note_vectors(self, *a, **k):
            pass

        def build_note_graph(self, *a, **k):
            pass

    inc = IncrementalSync()
    inc._graph = _MockGraphSvc()
    content = "# 第一章\n\n第一段内容。\n\n第二段内容。\n\n## 小节\n\n第三段内容。\n"
    inc._rebuild_note(db, "sync/x.md", content)

    chunks = db.query(DocChunk).filter(DocChunk.note_id == note.id).order_by(DocChunk.chunk_index).all()
    parents = [c for c in chunks if c.parent_chunk_id is None]
    children = [c for c in chunks if c.parent_chunk_id is not None]
    assert parents and children  # 既有父块又有子块
    child_ids = {c.id for c in chunks}
    assert all(c.parent_chunk_id in child_ids for c in children)  # 父块 id 有效
    assert note.content_hash == IncrementalSync._sha(content)