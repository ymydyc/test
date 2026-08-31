"""知识库业务服务单元测试（临时目录 + SQLite，不依赖真实数据）。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import DocChunk, GraphEntity, GraphRelation, ImportFile, KbNote
from app.services.import_service import ImportService
from app.services.kb_service import KbService, chunk_text

DDL = {
    "import_files": """
        CREATE TABLE import_files (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            rel_path       VARCHAR(1024) NOT NULL,
            rel_path_hash  VARCHAR(64)   NOT NULL UNIQUE,
            file_name      VARCHAR(512)  NOT NULL,
            ext_type       VARCHAR(32)   NOT NULL,
            is_dir         INTEGER       NOT NULL DEFAULT 0,
            parent_path    VARCHAR(1024),
            content_hash   CHAR(64),
            import_status  INTEGER       NOT NULL DEFAULT 0,
            file_size      BIGINT,
            content        BLOB,
            workspace_id   BIGINT,
            created_at     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "kb_notes": """
        CREATE TABLE kb_notes (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            note_path         VARCHAR(1024) NOT NULL,
            note_path_hash    VARCHAR(64)   NOT NULL UNIQUE,
            title             VARCHAR(512)  NOT NULL,
            content_md        TEXT          NOT NULL,
            content_hash      CHAR(64)      NOT NULL,
            origin_import_id  BIGINT,
            frontmatter_json  TEXT,
            workspace_id      BIGINT,
            created_at        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "doc_chunks": """
        CREATE TABLE doc_chunks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            note_id     BIGINT NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_text  TEXT NOT NULL,
            char_start  INTEGER NOT NULL,
            char_end    INTEGER NOT NULL,
            parent_chunk_id BIGINT,
            chroma_id   VARCHAR(128),
            workspace_id BIGINT,
            created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "graph_entities": """
        CREATE TABLE graph_entities (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            name              VARCHAR(512) NOT NULL,
            name_hash         VARCHAR(64)  NOT NULL UNIQUE,
            entity_type       VARCHAR(128) NOT NULL,
            description       TEXT,
            source_note_ids   TEXT,
            neo4j_id          VARCHAR(128),
            embedding_snapshot INTEGER NOT NULL DEFAULT 0,
            workspace_id       BIGINT,
            created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "graph_relations": """
        CREATE TABLE graph_relations (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            source_entity_id  BIGINT NOT NULL,
            target_entity_id  BIGINT NOT NULL,
            relation_type     VARCHAR(128) NOT NULL,
            description       TEXT,
            source_note_id    BIGINT,
            neo4j_rel_id      VARCHAR(128),
            workspace_id       BIGINT,
            created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """,
}


@pytest.fixture()
def dirs(tmp_path: Path):
    raw = tmp_path / "raw"
    kb = tmp_path / "kb"
    raw.mkdir()
    kb.mkdir()
    return raw, kb


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


@pytest.fixture()
def svc(dirs):
    raw, kb = dirs
    return KbService(kb, raw)


@pytest.fixture()
def imp(dirs):
    return ImportService(dirs[0])


def _upload(imp, db, raw, files: dict[str, bytes], target=""):
    imp.save_upload(
        db,
        list(files.keys()),
        [v.encode("utf-8") if isinstance(v, str) else v for v in files.values()],
        target,
    )
    return raw


# ---------- 分块 ----------
def test_chunk_text_basic():
    text_content = "\n\n".join(f"第{i}段" + "字" * 120 for i in range(30))
    chunks = chunk_text(text_content)
    assert chunks
    for c in chunks:
        assert c["start"] < c["end"]
        assert c["text"]
    assert chunks[0]["start"] == 0
    assert chunks[-1]["end"] <= len(text_content)
    # 无标题文档：唯一父块（位于下标 0）+ 若干子块，子块均关联该父块
    parents = [c for c in chunks if c["is_parent"]]
    children = [c for c in chunks if not c["is_parent"]]
    assert len(parents) == 1
    assert parents[0]["parent_chunk_id"] is None
    assert children and all(c["parent_chunk_id"] == 0 for c in children)


def test_chunk_text_parent_child_by_headings():
    md = (
        "# 第一章 概述\n\n" + "概述段落" + "字" * 100 + "\n\n"
        "# 第二章 细节\n\n" + "细节段落A" + "字" * 200 + "\n\n" + "细节段落B" + "字" * 100
    )
    chunks = chunk_text(md)
    parents = [(i, c) for i, c in enumerate(chunks) if c["is_parent"]]
    children = [(i, c) for i, c in enumerate(chunks) if not c["is_parent"]]
    assert len(parents) == 2
    assert all(c["parent_chunk_id"] is None for _, c in parents)
    assert "第一章" in parents[0][1]["text"] and "第二章" in parents[1][1]["text"]
    p0_idx, p1_idx = parents[0][0], parents[1][0]
    # 父块 0 与父块 1 之间的子块归属父块 0；之后的归属父块 1
    for i, c in children:
        if p0_idx < i < p1_idx:
            assert c["parent_chunk_id"] == p0_idx
        else:
            assert c["parent_chunk_id"] == p1_idx


def test_chunk_text_subheading_creates_parent():
    md = "# 章\n\n## 节\n\n" + "内容" + "字" * 50
    chunks = chunk_text(md)
    parents = [c for c in chunks if c["is_parent"]]
    assert len(parents) == 2  # "# 章" 与 "## 节" 各自成为一个父块


def test_chunk_text_long_paragraph_hardcut():
    para = "长" * 2500  # 单段超过 800
    chunks = chunk_text(para, max_chars=800, overlap=100)
    parents = [c for c in chunks if c["is_parent"]]
    children = [c for c in chunks if not c["is_parent"]]
    assert len(parents) == 1
    assert len(children) >= 4  # 2500 / 800 ≈ 4 块
    assert all(c["text"] for c in children)
    # 相邻硬切块有重叠
    assert any(c2["start"] < c1["end"] for c1, c2 in zip(children, children[1:]))


# ---------- 选择性写入 ----------
def test_write_note_persists_parent_child_chunks(svc, imp, db, dirs):
    raw, _ = dirs
    _upload(imp, db, raw, {"doc.md": "# 第一章\n\n" + "内容A" + "字" * 100 + "\n\n# 第二章\n\n" + "内容B" + "字" * 100})
    svc.write_selected(db, ["doc.md"])
    note = db.query(KbNote).filter(KbNote.note_path == "doc.md").one()
    rows = db.query(DocChunk).filter(DocChunk.note_id == note.id).order_by(DocChunk.chunk_index).all()
    parents = [r for r in rows if r.parent_chunk_id is None]
    children = [r for r in rows if r.parent_chunk_id is not None]
    assert len(parents) == 2  # 两个一级标题 → 两个父块
    assert children
    parent_ids = {p.id for p in parents}
    assert all(c.parent_chunk_id in parent_ids for c in children)  # 子块均指向所属父块 id


def test_write_single_file_creates_note(svc, imp, db, dirs):
    raw, kb = dirs
    _upload(imp, db, raw, {"a.txt": "# 标题\n\n正文内容段落。"})
    res = svc.write_selected(db, ["a.txt"])
    assert res["summary"] == {"written": 1, "skipped": 0, "failed": 0}
    rec = db.query(ImportFile).filter(ImportFile.rel_path == "a.txt").one()
    assert rec.import_status == 1
    note = db.query(KbNote).filter(KbNote.origin_import_id == rec.id).one()
    assert note.title == "标题"
    assert "正文内容段落" in note.content_md
    assert (kb / "a.md").exists()
    # 向量块已重建
    chunks = db.query(DocChunk).filter(DocChunk.note_id == note.id).all()
    assert len(chunks) >= 1


def test_write_skips_imported_file(svc, imp, db, dirs):
    raw, _ = dirs
    _upload(imp, db, raw, {"a.txt": "内容"})
    svc.write_selected(db, ["a.txt"])
    res = svc.write_selected(db, ["a.txt"])
    assert res["summary"]["written"] == 0
    assert res["results"][0]["status"] == "skipped"
    assert "已导入" in res["results"][0]["reason"]
    assert db.query(KbNote).count() == 1  # 不重复建笔记


def test_write_folder_all_imported_skipped(svc, imp, db, dirs):
    raw, _ = dirs
    _upload(imp, db, raw, {"d/f1.txt": "1", "d/f2.txt": "2"})
    res = svc.write_selected(db, ["d"])
    assert res["summary"]["written"] == 2
    # 再次写入 → 全量已导入，整体跳过
    res2 = svc.write_selected(db, ["d"])
    dir_result = res2["results"][0]
    assert dir_result["status"] == "skipped"
    assert "整体跳过" in dir_result["reason"]
    assert db.query(KbNote).count() == 2


def test_write_folder_partial(svc, imp, db, dirs):
    raw, _ = dirs
    _upload(imp, db, raw, {"d/f1.txt": "1", "d/f2.txt": "2"})
    svc.write_selected(db, ["d"])
    # 编辑 f2（重置标记）后再写 → f1 跳过，f2 重写
    rec = db.query(ImportFile).filter(ImportFile.rel_path == "d/f2.txt").one()
    rec.import_status = 0
    db.commit()
    res = svc.write_selected(db, ["d"])
    assert res["summary"] == {"written": 1, "skipped": 1, "failed": 0}
    assert db.query(KbNote).count() == 2


def test_parse_error_does_not_break_batch(svc, imp, db, dirs):
    raw, _ = dirs
    _upload(imp, db, raw, {"bad.pdf": b"\x00\x01\xff\xfe binary garbage", "ok.txt": "# 好文档"})
    res = svc.write_selected(db, ["bad.pdf", "ok.txt"])
    by_path = {r["path"]: r for r in res["results"]}
    assert by_path["bad.pdf"]["status"] == "error"
    assert "解析失败" in by_path["bad.pdf"]["reason"]
    assert by_path["ok.txt"]["status"] == "success"
    assert res["summary"] == {"written": 1, "skipped": 0, "failed": 1}


# ---------- 编辑标记联动 ----------
def test_save_note_edit_resets_marker(svc, imp, db, dirs):
    raw, kb = dirs
    _upload(imp, db, raw, {"a.txt": "旧内容"})
    svc.write_selected(db, ["a.txt"])
    rec = db.query(ImportFile).filter(ImportFile.rel_path == "a.txt").one()
    note = db.query(KbNote).filter(KbNote.origin_import_id == rec.id).one()
    assert rec.import_status == 1

    new_content = "# 改后标题\n\n被修改后的正文。"
    svc.save_note_edit(db, note.id, new_content)
    db.refresh(rec)
    assert rec.import_status == 0  # 标记消失
    db.refresh(note)
    assert note.content_md == new_content
    assert note.title == "改后标题"
    assert (kb / "a.md").read_text(encoding="utf-8") == new_content
    # 向量块重建
    chunks = db.query(DocChunk).filter(DocChunk.note_id == note.id).all()
    assert len(chunks) >= 1


def test_save_file_edit_resets_marker(svc, imp, db, dirs):
    raw, _ = dirs
    _upload(imp, db, raw, {"a.txt": "旧内容"})
    svc.write_selected(db, ["a.txt"])
    rec = db.query(ImportFile).filter(ImportFile.rel_path == "a.txt").one()
    assert rec.import_status == 1

    svc.save_file_edit(db, "a.txt", "在内容区直接编辑的内容")
    db.refresh(rec)
    assert rec.import_status == 0
    # 阶段六：编辑内容直接写回 DB content，不再落本地磁盘
    new_bytes = "在内容区直接编辑的内容".encode("utf-8")
    assert rec.content == new_bytes
    assert rec.file_size == len(new_bytes)
    assert rec.content_hash == hashlib.sha256(new_bytes).hexdigest()
    assert not (raw / "a.txt").exists()


def test_save_file_edit_rejects_binary(svc, imp, db, dirs):
    raw, _ = dirs
    _upload(imp, db, raw, {"b.pdf": b"%PDF-1.4\nfake"})
    with pytest.raises(ValueError):
        svc.save_file_edit(db, "b.pdf", "text")


# ---------- 级联清理 ----------
def test_delete_note_cascade(svc, imp, db, dirs):
    raw, kb = dirs
    _upload(imp, db, raw, {"a.txt": "内容"})
    svc.write_selected(db, ["a.txt"])
    rec = db.query(ImportFile).filter(ImportFile.rel_path == "a.txt").one()
    note = db.query(KbNote).filter(KbNote.origin_import_id == rec.id).one()
    chunks = db.query(DocChunk).filter(DocChunk.note_id == note.id).count()
    assert chunks > 0

    svc.delete_note(db, note.id)
    assert db.query(KbNote).count() == 0
    assert db.query(DocChunk).filter(DocChunk.note_id == note.id).count() == 0
    assert not (kb / "a.md").exists()
    db.refresh(rec)
    assert rec.import_status == 0  # 来源文件标记恢复为未导入


def test_delete_note_keeps_shared_entity(svc, imp, db, dirs):
    """孤立实体被删除，共享实体保留（FR-02 级联清理）。"""
    raw, _ = dirs
    _upload(imp, db, raw, {"a.txt": "内容", "b.txt": "另一个"})
    svc.write_selected(db, ["a.txt", "b.txt"])
    note_a = db.query(KbNote).filter(KbNote.note_path == "a.md").one()
    note_b = db.query(KbNote).filter(KbNote.note_path == "b.md").one()

    # 手动构造实体：孤立实体(仅 note_a 引用) 与共享实体(note_a+note_b 引用)
    orphan = GraphEntity(
        name="孤立实体", name_hash=KbService._sha("孤立实体"),
        entity_type="概念", source_note_ids=json.dumps([note_a.id]),
    )
    shared = GraphEntity(
        name="共享实体", name_hash=KbService._sha("共享实体"),
        entity_type="概念", source_note_ids=json.dumps([note_a.id, note_b.id]),
    )
    db.add_all([orphan, shared])
    db.flush()
    db.add(GraphRelation(source_entity_id=orphan.id, target_entity_id=shared.id,
                         relation_type="相关", source_note_id=note_a.id))
    db.commit()

    svc.delete_note(db, note_a.id)
    # 孤立实体及其关系被删除
    assert db.query(GraphEntity).filter(GraphEntity.name == "孤立实体").count() == 0
    assert db.query(GraphRelation).filter(
        GraphRelation.source_entity_id == orphan.id
    ).count() == 0
    # 共享实体保留，且其来源列表仅剩 note_b
    kept = db.query(GraphEntity).filter(GraphEntity.name == "共享实体").one()
    assert json.loads(kept.source_note_ids) == [note_b.id]


# ---------- 预览 ----------
def test_preview_parses_text(svc, imp, db, dirs):
    raw, _ = dirs
    _upload(imp, db, raw, {"n.md": "# Hello\n\nworld"})
    p = svc.preview(db, "n.md")
    assert p["content_md"] == "# Hello\n\nworld"
    assert p["text_based"] is True
    assert p["ext"] == "md"
