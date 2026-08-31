"""AI 助手服务单元测试（SQLite + 临时目录，不依赖 DashScope 在线）。

覆盖会话持久化、检索导入区、生成 md 的路径安全；流式对话依赖在线 API 仅做冒烟跳过。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.models import ImportFile
from app.services.assistant_service import AssistantService
from app.services.import_service import ImportService

DDL = {
    "chat_sessions": """
        CREATE TABLE chat_sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     BIGINT,
            title       VARCHAR(255),
            created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "chat_messages": """
        CREATE TABLE chat_messages (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id     BIGINT NOT NULL,
            role           VARCHAR(16) NOT NULL,
            content        TEXT NOT NULL,
            citations_json TEXT,
            created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """,
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


@pytest.fixture()
def svc():
    return AssistantService()


@pytest.fixture()
def dirs(tmp_path):
    raw = tmp_path / "raw"
    indir = tmp_path / "input"
    raw.mkdir()
    indir.mkdir()
    old_raw, old_in = settings.raw_dir, settings.input_dir
    settings.raw_dir, settings.input_dir = raw, indir
    yield raw, indir
    settings.raw_dir, settings.input_dir = old_raw, old_in


# ---------- 会话持久化 ----------
def test_session_create_list_messages(db, svc):
    s = svc.create_session(db, title="测试会话")
    assert s["id"] > 0
    msgs = svc.list_messages(db, s["id"])
    assert msgs == []

    # 写入一轮 user/assistant 消息（含引用）
    svc._add_message(db, s["id"], "user", "你好")
    mid = svc._add_message(db, s["id"], "assistant", "你好！", [{"idx": 1, "title": "资料"}])
    assert mid > 0

    lst = svc.list_messages(db, s["id"])
    assert [m["role"] for m in lst] == ["user", "assistant"]
    assert lst[0]["content"] == "你好"
    assert lst[1]["citations"] == [{"idx": 1, "title": "资料"}]

    sessions = svc.list_sessions(db)
    assert any(x["id"] == s["id"] for x in sessions)
    assert sessions[0]["title"] == "测试会话"


# ---------- 检索导入区（阶段六：内容存 DB） ----------
def test_retrieve_import_by_keyword(svc, db, dirs):
    raw, _ = dirs
    ImportService(raw).save_upload(
        db,
        ["ai/论文.md", "笔记.txt"],
        ["机器学习综述".encode("utf-8"), "今天学习 RAG".encode("utf-8")],
        target_dir="",
    )

    res = svc.retrieve_import(db, "论文", limit=10)
    names = [f["name"] for f in res["files"]]
    assert "论文.md" in names and "笔记.txt" not in names
    hit = next(f for f in res["files"] if f["name"] == "论文.md")
    assert hit["snippet"] == "机器学习综述"  # 内容预览已从 DB content 读取
    assert hit["dir"] == "ai"  # 保留相对目录

    empty = svc.retrieve_import(db, "不存在的关键词xyz", limit=10)
    assert empty["files"] == []


# ---------- 生成 md（写入导入区 import_files.content，默认 output/ + 防穿越） ----------
def _content_of(db, rel):
    rec = db.query(ImportFile).filter(ImportFile.rel_path == rel).one()
    return rec.content.decode("utf-8") if rec.content else None


def test_generate_md_writes_to_default_output(svc, db, dirs):
    raw, _ = dirs
    res = svc.generate_md(db, "# 总结\n\n这是一份总结", title="周报 2026")
    assert res["ok"] is True
    rel = "output/周报_2026.md"
    assert _content_of(db, rel) == "# 总结\n\n这是一份总结"
    assert not (raw / rel).exists()  # 内容在 DB，不再落磁盘


def test_generate_md_subpath_exists(svc, db, dirs):
    raw, _ = dirs
    imp = ImportService(raw)
    imp.create_folder(db, "", "nested")
    imp.create_folder(db, "nested", "deep")
    res = svc.generate_md(db, "正文", title="子", target_subpath="nested/deep")
    assert _content_of(db, "nested/deep/子.md") == "正文"
    assert "nested" in res["path"] and "deep" in res["path"]


def test_generate_md_fallback_output_when_subpath_missing(svc, db, dirs):
    raw, _ = dirs
    # 用户指定位置不存在（DB 无对应文件夹行）→ 回退默认 output/
    res = svc.generate_md(db, "正文", title="子", target_subpath="nested/deep")
    assert res["ok"] is True
    assert _content_of(db, "output/子.md") == "正文"
    # 未创建文件夹行
    folders = {r.rel_path for r in db.query(ImportFile).filter(ImportFile.is_dir == 1).all()}
    assert "nested" not in folders


def test_generate_md_blocks_path_traversal(svc, db, dirs):
    # 向上跳出导入区被拦截（防路径穿越）
    with pytest.raises(ValueError):
        svc.generate_md(db, "泄露", title="x", target_subpath="../escape")
    # 多层跨越同样拦截
    with pytest.raises(ValueError):
        svc.generate_md(db, "泄露", title="x", target_subpath="a/../../outside")