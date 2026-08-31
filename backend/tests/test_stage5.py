"""阶段五单元测试：周期回顾（FR-09）+ 健康检查（FR-10）+ 网页剪藏（FR-12）。

不打真实 DashScope/网络；LLM/抓取均以 Mock 注入。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import ChatMessage, ChatSession, DocChunk, GraphEntity, GraphRelation, ImportFile, KbNote, ReviewRecord

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
            workspace_id BIGINT,
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
            workspace_id BIGINT,
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
            workspace_id BIGINT,
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
            workspace_id BIGINT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "review_records": """
        CREATE TABLE review_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_type VARCHAR(16) NOT NULL,
            period_key VARCHAR(32) NOT NULL,
            summary_md TEXT NOT NULL,
            note_id BIGINT,
            workspace_id BIGINT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "import_files": """
        CREATE TABLE import_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rel_path VARCHAR(1024) NOT NULL,
            rel_path_hash VARCHAR(64) NOT NULL UNIQUE,
            file_name VARCHAR(512) NOT NULL,
            ext_type VARCHAR(32) NOT NULL,
            is_dir INTEGER NOT NULL DEFAULT 0,
            parent_path VARCHAR(1024),
            content_hash CHAR(64),
            import_status INTEGER NOT NULL DEFAULT 0,
            file_size INTEGER,
            content BLOB,
            workspace_id BIGINT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "chat_sessions": """
        CREATE TABLE chat_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id BIGINT,
            title VARCHAR(255),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "chat_messages": """
        CREATE TABLE chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id BIGINT NOT NULL,
            role VARCHAR(16) NOT NULL,
            content TEXT NOT NULL,
            citations_json TEXT,
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


class _MockLLM:
    """返回固定回顾摘要的 Mock LLM。"""

    def generate(self, messages, *, json_mode=False, **kw):
        return "## 知识点\n- RAG 相关知识\n\n## 待办\n- 继续深入向量检索"

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]

    def availability(self):
        return {"ok": True}


class _NoopGraphService:
    """生成回顾后触发的向量/图谱构建 no-op。"""

    def __init__(self, *args, **kwargs):
        pass

    def build_note_vectors(self, *a, **k):
        return {"indexed": 0}

    def build_note_graph(self, *a, **k):
        return {"status": "skipped"}

    def remove_relations_for_note(self, *a, **k):
        return None

    def remove_note_vectors(self, *a, **k):
        return None


def _mk_note(db, path, content, updated=None, title=None):
    import hashlib
    note = KbNote(
        note_path=path, note_path_hash=hashlib.sha256(path.encode()).hexdigest(),
        title=title or Path(path).stem, content_md=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        origin_import_id=None,
    )
    if updated is not None:
        note.updated_at = updated
    db.add(note)
    db.flush()
    return note


# ---------- 周期回顾（FR-09） ----------
def test_review_generate_creates_note_and_record(db, monkeypatch, tmp_path):
    """生成周回顾：写 reviews/ 笔记 + 登记 review_records + 关联 note_id。"""
    import app.services.review_service as rmod
    import app.services.graph_service as gmod
    monkeypatch.setattr(gmod, "GraphService", _NoopGraphService)
    monkeypatch.setattr(rmod.settings, "dashscope_api_key", "test-key")

    _mk_note(db, "a.md", "# A\n\n关于 RAG 的内容", updated=dt.datetime(2026, 8, 18, 10, 0))
    db.commit()

    svc = rmod.ReviewService(kb_dir=tmp_path, llm=_MockLLM())
    result = svc.generate_review(db, "week", "2026-W34")

    assert result["status"] == "ok"
    assert result["period_type"] == "week"
    assert result["note_count"] == 1

    record = db.query(ReviewRecord).filter(ReviewRecord.period_key == "2026-W34").first()
    assert record is not None
    assert record.note_id == result["note_id"]
    assert "#weekly-review" in record.summary_md or "知识点" in record.summary_md

    note = db.query(KbNote).filter(KbNote.note_path == "reviews/2026-W34.md").first()
    assert note is not None
    assert (tmp_path / "reviews" / "2026-W34.md").exists()
    assert "#weekly-review" in note.content_md
    # 父子块结构存在
    chunks = db.query(DocChunk).filter(DocChunk.note_id == note.id).all()
    assert any(c.parent_chunk_id is None for c in chunks)


def test_review_generate_idempotent(db, monkeypatch, tmp_path):
    """同周期重复生成不新建记录/笔记（更新原记录）。"""
    import app.services.review_service as rmod
    import app.services.graph_service as gmod
    monkeypatch.setattr(gmod, "GraphService", _NoopGraphService)
    monkeypatch.setattr(rmod.settings, "dashscope_api_key", "test-key")

    _mk_note(db, "b.md", "# B\n\n内容", updated=dt.datetime.now())
    db.commit()
    svc = rmod.ReviewService(kb_dir=tmp_path, llm=_MockLLM())

    svc.generate_review(db, "month", "2026-08")
    svc.generate_review(db, "month", "2026-08")
    assert db.query(ReviewRecord).filter(ReviewRecord.period_key == "2026-08").count() == 1
    assert db.query(KbNote).filter(KbNote.note_path == "reviews/2026-08.md").count() == 1


def test_review_generate_skips_without_data(db, monkeypatch, tmp_path):
    """窗口内无数据 → 优雅跳过（不抛错、不写库）。"""
    import app.services.review_service as rmod
    monkeypatch.setattr(rmod.settings, "dashscope_api_key", "test-key")
    svc = rmod.ReviewService(kb_dir=tmp_path, llm=_MockLLM())
    result = svc.generate_review(db, "week", "2026-W40")
    assert result["status"] == "skipped"
    assert "无活动数据" in result["reason"]
    assert db.query(ReviewRecord).count() == 0


def test_review_skips_without_api_key(db, monkeypatch, tmp_path):
    """无 DASHSCOPE_API_KEY → 跳过生成。"""
    import app.services.review_service as rmod
    monkeypatch.setattr(rmod.settings, "dashscope_api_key", "")
    _mk_note(db, "c.md", "# C", updated=dt.datetime.now())
    db.commit()
    svc = rmod.ReviewService(kb_dir=tmp_path, llm=_MockLLM())
    assert svc.generate_review(db, "week")["status"] == "skipped"


def test_review_delete_cascades_note(db, monkeypatch, tmp_path):
    """删除回顾记录同步删除知识库回顾笔记。"""
    import app.services.review_service as rmod
    import app.services.graph_service as gmod
    monkeypatch.setattr(gmod, "GraphService", _NoopGraphService)
    monkeypatch.setattr(rmod.settings, "dashscope_api_key", "test-key")

    # 固定到 W35 窗口内（[2026-08-24, 2026-08-31)），避免随真实日期推移而失效
    _mk_note(db, "d.md", "# D", updated=dt.datetime(2026, 8, 25, 10, 0))
    db.commit()
    svc = rmod.ReviewService(kb_dir=tmp_path, llm=_MockLLM())
    result = svc.generate_review(db, "week", "2026-W35")
    rid = db.query(ReviewRecord).filter(ReviewRecord.period_key == "2026-W35").first().id

    svc.delete_review(db, rid)
    assert db.query(ReviewRecord).count() == 0
    assert db.query(KbNote).filter(KbNote.note_path == "reviews/2026-W35.md").count() == 0
    assert not (tmp_path / "reviews" / "2026-W35.md").exists()


def test_review_time_window_parsing():
    """period_key 解析出正确时间窗口。"""
    import app.services.review_service as rmod
    svc = rmod.ReviewService(kb_dir=".", llm=_MockLLM())
    start, end = svc.time_window("week", "2026-W34")
    assert start == dt.datetime(2026, 8, 17, 0, 0)  # ISO 周一
    assert end == dt.datetime(2026, 8, 24, 0, 0)
    ms, me = svc.time_window("month", "2026-08")
    assert ms == dt.datetime(2026, 8, 1)
    assert me == dt.datetime(2026, 9, 1)


def test_review_collects_chat_sessions(db, monkeypatch, tmp_path):
    """生成回顾时采集 AI 对话记录。"""
    import app.services.review_service as rmod
    import app.services.graph_service as gmod
    monkeypatch.setattr(gmod, "GraphService", _NoopGraphService)
    monkeypatch.setattr(rmod.settings, "dashscope_api_key", "test-key")

    # 创建一条 AI 对话
    cs = ChatSession(user_id=42, title="测试对话", created_at=dt.datetime(2026, 8, 18, 10, 0))
    db.add(cs)
    db.flush()
    db.add(ChatMessage(session_id=cs.id, role="user", content="今天学了什么？",
                       created_at=dt.datetime(2026, 8, 18, 10, 1)))
    db.add(ChatMessage(session_id=cs.id, role="assistant", content="今天学习了 RAG 和向量检索",
                       created_at=dt.datetime(2026, 8, 18, 10, 2)))
    db.commit()

    svc = rmod.ReviewService(kb_dir=tmp_path, llm=_MockLLM(), user_id=42)
    result = svc.generate_review(db, "week", "2026-W34")
    assert result["status"] == "ok"
    assert result["chat_count"] == 1


def test_review_collects_import_files(db, monkeypatch, tmp_path):
    """生成回顾时采集导入文件目录。"""
    import app.services.review_service as rmod
    import app.services.graph_service as gmod
    import hashlib
    monkeypatch.setattr(gmod, "GraphService", _NoopGraphService)
    monkeypatch.setattr(rmod.settings, "dashscope_api_key", "test-key")

    path = "docs/report.pdf"
    db.add(ImportFile(
        rel_path=path, rel_path_hash=hashlib.sha256(path.encode()).hexdigest(),
        file_name="report.pdf", ext_type="pdf", is_dir=0,
        workspace_id=1, created_at=dt.datetime(2026, 8, 18, 10, 0),
    ))
    db.commit()

    svc = rmod.ReviewService(kb_dir=tmp_path, llm=_MockLLM(), workspace_id=1)
    result = svc.generate_review(db, "week", "2026-W34")
    assert result["status"] == "ok"
    assert result["import_count"] == 1


def test_review_skips_chat_without_user_id(db, monkeypatch, tmp_path):
    """无 user_id 时不采集 AI 对话记录。"""
    import app.services.review_service as rmod
    import app.services.graph_service as gmod
    monkeypatch.setattr(gmod, "GraphService", _NoopGraphService)
    monkeypatch.setattr(rmod.settings, "dashscope_api_key", "test-key")

    cs = ChatSession(user_id=42, title="测试对话", created_at=dt.datetime(2026, 8, 18, 10, 0))
    db.add(cs)
    db.flush()
    db.add(ChatMessage(session_id=cs.id, role="user", content="测试",
                       created_at=dt.datetime(2026, 8, 18, 10, 1)))
    # 有对话但无 user_id，只能靠 kb_notes 触发
    _mk_note(db, "a.md", "# A", updated=dt.datetime(2026, 8, 18, 10, 0))
    db.commit()

    svc = rmod.ReviewService(kb_dir=tmp_path, llm=_MockLLM())  # 无 user_id
    result = svc.generate_review(db, "week", "2026-W34")
    assert result["status"] == "ok"
    assert result["chat_count"] == 0  # 不采集对话
    assert result["note_count"] == 1  # 只采集笔记


# ---------- 健康检查（FR-10） ----------
def test_health_isolated_entities(db, tmp_path):
    from app.services.health_service import HealthService
    e1 = GraphEntity(name="A", name_hash="h1", entity_type="概念", source_note_ids="[]")
    e2 = GraphEntity(name="B", name_hash="h2", entity_type="概念", source_note_ids="[]")
    db.add_all([e1, e2])
    db.flush()
    db.add(GraphRelation(source_entity_id=e1.id, target_entity_id=e2.id, relation_type="相关"))
    db.commit()

    report = HealthService(raw_dir=tmp_path / "raw", kb_dir=tmp_path / "kb").run_report(db)
    isolated = {e["name"] for e in report["isolated_entities"]}
    # A 与 B 之间有边 → 都不孤立；再补一个孤立实体验证
    assert isolated == set()
    e3 = GraphEntity(name="C", name_hash="h3", entity_type="概念", source_note_ids="[]")
    db.add(e3)
    db.commit()
    report = HealthService(raw_dir=tmp_path / "raw", kb_dir=tmp_path / "kb").run_report(db)
    assert {e["name"] for e in report["isolated_entities"]} == {"C"}


def test_health_stale_and_missing_notes(db, tmp_path):
    from app.services.health_service import HealthService
    kb = tmp_path / "kb"
    kb.mkdir(parents=True)
    (kb / "ok.md").write_text("新内容", encoding="utf-8")
    _mk_note(db, "ok.md", "新内容")  # 一致

    (kb / "stale.md").write_text("磁盘内容变了", encoding="utf-8")
    _mk_note(db, "stale.md", "数据库里的旧内容")  # 不一致

    _mk_note(db, "gone.md", "文件缺失")  # 无对应文件

    db.commit()
    report = HealthService(raw_dir=tmp_path / "raw", kb_dir=kb).run_report(db)
    assert {n["note_path"] for n in report["stale_notes"]} == {"stale.md"}
    assert {n["note_path"] for n in report["missing_note_files"]} == {"gone.md"}


def test_health_missing_import_and_unregistered(db, tmp_path):
    from app.services.health_service import HealthService
    raw = tmp_path / "raw"
    raw.mkdir(parents=True)
    (raw / "orphan.txt").write_text("x", encoding="utf-8")  # 磁盘有、DB 无
    db.add(ImportFile(rel_path="gone.txt", rel_path_hash="g1", file_name="gone.txt",
                      ext_type="txt", content_hash="h", import_status=0, file_size=0))
    db.commit()
    report = HealthService(raw_dir=raw, kb_dir=tmp_path / "kb").run_report(db)
    assert {f["rel_path"] for f in report["missing_import_files"]} == {"gone.txt"}
    assert {f["rel_path"] for f in report["unregistered_files"]} == {"orphan.txt"}


def test_health_no_backlink_notes(db, tmp_path):
    from app.services.health_service import HealthService
    _mk_note(db, "n1.md", "没有双链的笔记")
    _mk_note(db, "n2.md", "有双链 [[n1]] 的笔记")
    db.commit()
    report = HealthService(raw_dir=tmp_path / "raw", kb_dir=tmp_path / "kb").run_report(db)
    assert {n["note_path"] for n in report["no_backlink_notes"]} == {"n1.md"}


# ---------- 网页剪藏（FR-12） ----------
def test_clip_url_saves_md_and_registers(db, monkeypatch, tmp_path):
    from app.services.clip_service import ClipError, ClipService
    raw = tmp_path / "raw"
    raw.mkdir()
    svc = ClipService(raw_dir=raw)
    svc._fetch = lambda url: "<html><body><p>正文</p></body></html>"
    svc._extract = lambda html: {"text": "剪藏正文", "title": "示例页面"}

    result = svc.clip_url(db, "https://example.com/a/b", target_dir="web")
    assert result["name"].endswith(".md")
    assert result["path"].startswith("web/")

    rec = db.query(ImportFile).filter(ImportFile.rel_path == result["path"]).first()
    assert rec is not None
    assert rec.ext_type == "md"
    assert rec.import_status == 0
    # 阶段六：内容写入 DB content，不再落本地磁盘
    assert rec.content is not None
    assert "剪藏正文" in rec.content.decode("utf-8")
    assert not (raw / result["path"]).exists()

    # 同一 URL 再剪藏 → 自动改名 a-2.md，不覆盖
    result2 = svc.clip_url(db, "https://example.com/a/b", target_dir="web")
    assert result2["path"] != result["path"]
    assert result2["name"].endswith("-2.md")


def test_clip_url_invalid_url(db, tmp_path):
    from app.services.clip_service import ClipError, ClipService
    svc = ClipService(raw_dir=tmp_path / "raw")
    with pytest.raises(ClipError):
        svc.clip_url(db, "not-a-url")


def test_clip_url_empty_extract(db, monkeypatch, tmp_path):
    from app.services.clip_service import ClipError, ClipService
    svc = ClipService(raw_dir=tmp_path / "raw")
    svc._fetch = lambda url: "<html></html>"
    svc._extract = lambda html: {"text": "", "title": ""}
    with pytest.raises(ClipError):
        svc.clip_url(db, "https://example.com/empty")


# ---------- 周期回顾调度器（FR-09） ----------
def test_review_scheduler_registers_weekly_and_monthly_jobs():
    """启动调度器后注册周记(周一08:00)+月报(每月1日08:00)两个 cron 任务。"""
    from app.scheduler.review_scheduler import ReviewScheduler
    sched = ReviewScheduler()
    sched.start()
    try:
        jobs = sched._scheduler.get_jobs()
        ids = {j.id for j in jobs}
        triggers = {j.id: str(j.trigger) for j in jobs}
        assert "weekly-review" in ids
        assert "monthly-review" in ids
        assert "mon" in triggers.get("weekly-review", "")
        assert "day='1'" in triggers.get("monthly-review", "")
    finally:
        sched.stop()
