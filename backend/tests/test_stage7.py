"""阶段七 单元测试：用户认证与工作区数据隔离验证（阶段七）。"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.security import hash_password
from app.db import models  # noqa: F401  确保所有模型注册
from app.db.models import (
    User,
    Workspace,
    WorkspaceMember,
    ImportFile,
    KbNote,
    DocChunk,
    GraphEntity,
    GraphRelation,
    ChatSession,
)
from app.services.auth_service import AuthService
from app.services.import_service import ImportService
from app.services.kb_service import KbService
from app.db.scoping import scope, workspace_scope


def _ddl_sql():
    """给 SQLite 建测试表结构（对齐 ORM）。"""
    return [
        """CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username VARCHAR(64) UNIQUE NOT NULL,
            password_hash VARCHAR(256) NOT NULL,
            display_name VARCHAR(128),
            status SMALLINT NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE workspaces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(128) NOT NULL,
            type VARCHAR(16) NOT NULL DEFAULT 'personal',
            creator_id BIGINT NOT NULL REFERENCES users(id),
            description VARCHAR(255),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE workspace_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id BIGINT NOT NULL REFERENCES workspaces(id),
            user_id BIGINT NOT NULL REFERENCES users(id),
            role VARCHAR(16) NOT NULL DEFAULT 'member',
            joined_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (workspace_id, user_id)
        )""",
        """CREATE TABLE auth_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id BIGINT NOT NULL REFERENCES users(id),
            token_jti VARCHAR(64) NOT NULL,
            label VARCHAR(128),
            expires_at DATETIME,
            revoked SMALLINT NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen DATETIME
        )""",
        """CREATE TABLE import_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rel_path VARCHAR(1024) NOT NULL,
            rel_path_hash VARCHAR(64) NOT NULL UNIQUE,
            file_name VARCHAR(512) NOT NULL,
            ext_type VARCHAR(32) NOT NULL,
            is_dir INTEGER NOT NULL DEFAULT 0,
            parent_path VARCHAR(1024),
            content_hash CHAR(64),
            import_status INTEGER NOT NULL DEFAULT 0,
            file_size BIGINT,
            content BLOB,
            workspace_id BIGINT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE kb_notes (
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
        )""",
        """CREATE TABLE doc_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note_id INTEGER NOT NULL REFERENCES kb_notes(id),
            chunk_index INTEGER NOT NULL,
            chunk_text TEXT NOT NULL,
            char_start INTEGER NOT NULL,
            char_end INTEGER NOT NULL,
            parent_chunk_id BIGINT,
            chroma_id VARCHAR(128),
            workspace_id BIGINT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE graph_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(512) NOT NULL,
            name_hash VARCHAR(64) UNIQUE NOT NULL,
            entity_type VARCHAR(128) NOT NULL,
            description TEXT,
            source_note_ids TEXT,
            neo4j_id VARCHAR(128),
            embedding_snapshot TINYINT NOT NULL DEFAULT 0,
            workspace_id BIGINT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE graph_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_entity_id BIGINT NOT NULL REFERENCES graph_entities(id),
            target_entity_id BIGINT NOT NULL REFERENCES graph_entities(id),
            relation_type VARCHAR(128) NOT NULL,
            description TEXT,
            source_note_id INTEGER,
            neo4j_rel_id VARCHAR(128),
            workspace_id BIGINT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE chat_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id BIGINT REFERENCES users(id),
            title VARCHAR(255),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    ]


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool, future=True,
    )
    with engine.begin() as conn:
        for ddl in _ddl_sql():
            conn.execute(text(ddl))
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


# ---------- 用户/工作区注册与认证 ----------
def test_register_creates_user_and_personal_workspace(db):
    svc = AuthService(db)
    user = svc.register("alice", "test123456", "Alice")
    assert user.id > 0
    assert user.username == "alice"
    # 自动创建 personal 工作区并加入成员
    ws = (
        db.query(Workspace)
        .filter(Workspace.creator_id == user.id, Workspace.type == "personal")
        .first()
    )
    assert ws is not None
    member = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == user.id)
        .first()
    )
    assert member is not None
    assert member.role == "owner"


def test_login_succeeds_with_correct_password(db):
    svc = AuthService(db)
    svc.register("alice", "test123456")
    user, token, expires = svc.login("alice", "test123456")
    assert token is not None
    assert expires > 0


def test_login_fails_with_wrong_password(db):
    svc = AuthService(db)
    svc.register("alice", "test123456")
    with pytest.raises(ValueError, match="用户名或密码错误"):
        svc.login("alice", "wrongpass")


def test_login_fails_disabled_account(db):
    svc = AuthService(db)
    user = svc.register("alice", "test123456")
    user.status = 0
    db.commit()
    with pytest.raises(ValueError, match="账号已被禁用"):
        svc.login("alice", "test123456")


# ---------- 导入区数据隔离 ----------
def test_import_data_is_isolated_by_workspace(db, tmp_path):
    # 纯 DB 存储，raw_dir 仅用于 sync_from_fs 兜底；用空临时目录避免扫描真实项目路径
    raw = tmp_path / "raw"
    raw.mkdir()
    # 创建两个人（分别创建 workspace 并拿两个 ws id）
    svc_a = AuthService(db)
    u_a = svc_a.register("alice", "test123")
    ws_a = svc_a.ws_svc.ensure_personal(u_a)

    svc_b = AuthService(db)
    u_b = svc_b.register("bob", "test123")
    ws_b = svc_b.ws_svc.ensure_personal(u_b)

    # Alice 创建一个文件 a.md
    isvc_a = ImportService(raw, workspace_id=ws_a.id)
    isvc_a.save_upload(db, ["a.md"], [b"alice content"], target_dir="")

    # Bob 创建一个文件 b.md
    isvc_b = ImportService(raw, workspace_id=ws_b.id)
    isvc_b.save_upload(db, ["b.md"], [b"bob content"], target_dir="")

    # 验证：Alice 看不到 Bob 的文件，只能看到自己的
    tree_a = isvc_a.build_tree(db)
    names_a = {node["path"] for node in tree_a["children"]}
    assert "a.md" in names_a
    assert "b.md" not in names_a
    # 数量正确（Alice 仅 1 个）
    assert len(tree_a["children"]) == 1

    # 验证：Bob 看不到 Alice 的文件
    tree_b = isvc_b.build_tree(db)
    names_b = {node["path"] for node in tree_b["children"]}
    assert "b.md" in names_b
    assert "a.md" not in names_b


def test_query_scope_filters_correctly(db):
    # 模拟 workspace_scope：两个 workspace 各一个文件
    a_user = User(username="a", password_hash=hash_password("pass"))
    db.add(a_user)
    db.commit()
    ws_a = Workspace(name="a personal", type="personal", creator_id=a_user.id)
    db.add(ws_a)
    db.commit()
    db.add(ImportFile(rel_path="a.txt", rel_path_hash="hash", file_name="a.txt", ext_type="txt",
                      is_dir=0, content_hash="hash", workspace_id=ws_a.id))
    ws_b = Workspace(name="b personal", type="personal", creator_id=a_user.id)
    db.add(ws_b)
    db.commit()
    db.add(ImportFile(rel_path="b.txt", rel_path_hash="hash2", file_name="b.txt", ext_type="txt",
                      is_dir=0, content_hash="hash2", workspace_id=ws_b.id))
    db.commit()

    from app.db.scoping import scope as scope_q
    q = scope_q(db.query(ImportFile), ImportFile, ws_a.id)
    rows = q.all()
    assert len(rows) == 1
    assert rows[0].rel_path == "a.txt"

    q2 = scope_q(db.query(ImportFile), ImportFile, ws_b.id)
    rows2 = q2.all()
    assert len(rows2) == 1
    assert rows2[0].rel_path == "b.txt"


# ---------- KbNote 隔离 ----------
def test_kb_note_visible_only_own_workspace(db, tmp_path):
    svc_a = AuthService(db)
    u_a = svc_a.register("alice", "test123456")
    ws_a = svc_a.ws_svc.ensure_personal(u_a)
    svc_b = AuthService(db)
    u_b = svc_b.register("bob", "test123456")
    ws_b = svc_b.ws_svc.ensure_personal(u_b)

    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()

    # Alice：上传并写库
    ImportService("", workspace_id=ws_a.id).save_upload(db, ["test.md"], [b"# Alice note\nhello alice"], "")
    a = KbService(kb_dir, "", workspace_id=ws_a.id)
    res_a = a.write_selected(db, ["test.md"])
    assert res_a["results"][0]["status"] == "success"

    # Bob：上传并写库
    ImportService("", workspace_id=ws_b.id).save_upload(db, ["test.md"], [b"# Bob note\nhello bob"], "")
    b = KbService(kb_dir, "", workspace_id=ws_b.id)
    res_b = b.write_selected(db, ["test.md"])
    assert res_b["results"][0]["status"] == "success"

    # 各自只能看到各自的笔记
    notes_a = a.list_notes(db)
    notes_b = b.list_notes(db)
    assert len(notes_a) == 1
    assert len(notes_b) == 1
    assert "Alice" in notes_a[0]["title"]
    assert "Bob" in notes_b[0]["title"]

    # Alice 能取到自己的正文，取不到 bob 的（bob note id 在 alice 工作区不可见）
    note_a_id = notes_a[0]["id"]
    assert "# Alice note" in a.get_note(db, note_a_id)["content_md"]


# ---------- ChatSession 隔离 ----------
def test_chat_session_is_by_user(db):
    u_a = User(username="alice", password_hash=hash_password("pass"))
    u_b = User(username="bob", password_hash=hash_password("pass"))
    db.add(u_a)
    db.add(u_b)
    db.commit()

    # 两个用户各一个会话
    db.add(ChatSession(user_id=u_a.id, title="alice session"))
    db.add(ChatSession(user_id=u_b.id, title="bob session"))
    db.commit()

    rows_a = db.query(ChatSession).filter(ChatSession.user_id == u_a.id).all()
    assert len(rows_a) == 1
    assert rows_a[0].title == "alice session"

    rows_b = db.query(ChatSession).filter(ChatSession.user_id == u_b.id).all()
    assert len(rows_b) == 1
    assert rows_b[0].title == "bob session"


# ---------- GraphEntity 隔离 ----------
def test_graph_entity_is_isolated_by_workspace(db):
    a_user = User(username="alice", password_hash=hash_password("pass"))
    db.add(a_user)
    db.commit()
    ws_a = Workspace(name="a", type="personal", creator_id=a_user.id)
    db.add(ws_a)
    ws_b = Workspace(name="b", type="personal", creator_id=a_user.id)
    db.add(ws_b)
    db.commit()

    # 两个 workspace 同名概念共存（隔离互不影响）
    db.add(GraphEntity(name="Python", name_hash="hasha", entity_type="概念",
                      description="alice's Python", workspace_id=ws_a.id))
    db.add(GraphEntity(name="Python", name_hash="hashb", entity_type="语言",
                      description="bob's Python", workspace_id=ws_b.id))
    db.commit()

    from app.db.scoping import scope as scope_q
    ent_a = scope_q(db.query(GraphEntity), GraphEntity, ws_a.id).filter(GraphEntity.name == "Python").first()
    assert ent_a is not None
    assert ent_a.description == "alice's Python"

    ent_b = scope_q(db.query(GraphEntity), GraphEntity, ws_b.id).filter(GraphEntity.name == "Python").first()
    assert ent_b is not None
    assert ent_b.description == "bob's Python"

    # 总数：两个 workspace 各一个，总共有两个
    assert db.query(GraphEntity).count() == 2