"""AI 助手服务单元测试（SQLite + 临时目录，不依赖 DashScope 在线）。

覆盖会话持久化、检索导入区、生成 md 的路径安全；流式对话依赖在线 API 仅做冒烟跳过。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.services.assistant_service import AssistantService

DDL = {
    "chat_sessions": """
        CREATE TABLE chat_sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
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


# ---------- 检索导入区 ----------
def test_retrieve_import_by_keyword(svc, db, dirs):
    raw, _ = dirs
    (raw / "ai").mkdir()
    (raw / "ai" / "论文.md").write_text("机器学习综述", encoding="utf-8")
    (raw / "笔记.txt").write_text("今天学习 RAG", encoding="utf-8")

    res = svc.retrieve_import(db, "论文", limit=10)
    names = [f["name"] for f in res["files"]]
    assert "论文.md" in names and "笔记.txt" not in names
    hit = next(f for f in res["files"] if f["name"] == "论文.md")
    assert hit["snippet"] == "机器学习综述"  # 内容预览已读取
    assert hit["dir"] == "ai"  # 保留相对目录

    empty = svc.retrieve_import(db, "不存在的关键词xyz", limit=10)
    assert empty["files"] == []


# ---------- 生成 md（默认 input + 防穿越） ----------
def test_generate_md_writes_to_input(svc, db, dirs):
    _, indir = dirs
    res = svc.generate_md(db, "# 总结\n\n这是一份总结", title="周报 2026")
    assert res["ok"] is True
    target = indir / "周报_2026.md"
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "# 总结\n\n这是一份总结"


def test_generate_md_subpath(svc, db, dirs):
    _, indir = dirs
    res = svc.generate_md(db, "正文", title="子", target_subpath="nested/deep")
    assert (indir / "nested" / "deep" / "子.md").exists()
    assert "nested" in res["path"] and "deep" in res["path"]


def test_generate_md_blocks_path_traversal(svc, db, dirs):
    # 向上跳出默认目录被拦截（防路径穿越）
    with pytest.raises(ValueError):
        svc.generate_md(db, "泄露", title="x", target_subpath="../escape")
    # 多层跨越同样拦截
    with pytest.raises(ValueError):
        svc.generate_md(db, "泄露", title="x", target_subpath="a/../../outside")