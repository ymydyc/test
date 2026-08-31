"""阶段八批次3 单元测试：组创建 + 邀请码机制（8.4）。"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import models  # noqa: F401  确保所有模型注册
from app.db.models import User, Workspace, WorkspaceMember, WorkspaceInvite
from app.services.auth_service import AuthService
from app.services.workspace_service import WorkspaceService


def _ddl_sql():
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
        """CREATE TABLE workspace_invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id BIGINT NOT NULL REFERENCES workspaces(id),
            creator_id BIGINT NOT NULL REFERENCES users(id),
            code VARCHAR(64) UNIQUE NOT NULL,
            expires_at DATETIME NOT NULL,
            used_by BIGINT REFERENCES users(id),
            used_at DATETIME,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
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


def _make_users(db) -> tuple[User, User]:
    svc = AuthService(db)
    creator = svc.register("alice", "test123456", "Alice")
    joiner = svc.register("bob", "test123456", "Bob")
    return creator, joiner


# ---------- 组创建 ----------
def test_create_group_creator_is_owner(db):
    creator, _ = _make_users(db)
    ws = WorkspaceService(db).create_group(" 我的组 ", creator, "desc")
    assert ws.type == "group"
    assert ws.name == "我的组"
    assert ws.creator_id == creator.id
    me = db.query(WorkspaceMember).filter(
        WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == creator.id).first()
    assert me is not None and me.role == "owner"


def test_create_group_empty_name_rejected(db):
    creator, _ = _make_users(db)
    with pytest.raises(ValueError):
        WorkspaceService(db).create_group("   ", creator)


# ---------- 邀请码权限 ----------
def test_invite_only_creator(db):
    creator, joiner = _make_users(db)
    svc = WorkspaceService(db)
    ws = svc.create_group("组", creator)
    with pytest.raises(PermissionError):
        svc.get_invite(ws.id, joiner)
    inv = svc.get_invite(ws.id, creator)
    assert len(inv.code) == 8 and inv.code.isalnum()


def test_invite_code_refresh_invalidates_old(db):
    creator, _ = _make_users(db)
    svc = WorkspaceService(db)
    ws = svc.create_group("组", creator)
    inv1 = svc.get_invite(ws.id, creator)
    inv2 = svc.get_invite(ws.id, creator)
    assert inv1.code != inv2.code
    # 同一组只保留最新邀请码
    remaining = db.query(WorkspaceInvite).filter(
        WorkspaceInvite.workspace_id == ws.id).all()
    assert [i.code for i in remaining] == [inv2.code]


# ---------- 凭码加入 ----------
def test_redeem_invite_joins_as_member(db):
    creator, joiner = _make_users(db)
    svc = WorkspaceService(db)
    ws = svc.create_group("组", creator)
    inv = svc.get_invite(ws.id, creator)
    got = svc.redeem_invite(inv.code, joiner)
    assert got.id == ws.id
    me = db.query(WorkspaceMember).filter(
        WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == joiner.id).first()
    assert me is not None and me.role == "member"
    # 码已一次性使用
    inv2 = db.get(WorkspaceInvite, inv.id)
    assert inv2.used_by == joiner.id and inv2.used_at is not None


def test_redeem_used_invite_rejected(db):
    creator, joiner = _make_users(db)
    svc = WorkspaceService(db)
    ws = svc.create_group("组", creator)
    inv = svc.get_invite(ws.id, creator)
    svc.redeem_invite(inv.code, joiner)
    with pytest.raises(LookupError):
        svc.redeem_invite(inv.code, joiner)


def test_redeem_expired_invite_rejected(db):
    creator, joiner = _make_users(db)
    svc = WorkspaceService(db)
    ws = svc.create_group("组", creator)
    inv = svc.get_invite(ws.id, creator)
    # 手动改为过期
    from datetime import datetime, timedelta
    inv.expires_at = datetime.now() - timedelta(minutes=1)
    db.commit()
    with pytest.raises(LookupError):
        svc.redeem_invite(inv.code, joiner)


# ---------- 批次4：共享隔离 + 权限约束 ----------
def test_member_reading_shared_workspace(db):
    creator, joiner = _make_users(db)
    svc = WorkspaceService(db)
    ws = svc.create_group("组", creator)
    inv = svc.get_invite(ws.id, creator)
    svc.redeem_invite(inv.code, joiner)
    # 加入后 joiner 已成为成员，可参与该 workspace（模拟活动工作区切换）
    me = svc.member(ws.id, joiner.id)
    assert me is not None and me.role == "member"
    others = svc.list_user_workspaces(joiner.id)
    assert any(w.id == ws.id for w in others), "joiner 应能在自己的工作区列表看到组"


def test_non_member_workspace_inaccessible(db):
    creator, joiner = _make_users(db)
    from app.services.auth_service import AuthService
    outsider = AuthService(db).register("carol", "test123456")
    svc = WorkspaceService(db)
    ws = svc.create_group("组", creator)
    # outsider 不在成员表
    assert svc.member(ws.id, outsider.id) is None


def test_leave_group_as_creator_rejected(db):
    creator, joiner = _make_users(db)
    svc = WorkspaceService(db)
    ws = svc.create_group("组", creator)
    with pytest.raises(PermissionError):
        svc.leave_group(ws.id, creator)


def test_leave_group_as_member_ok(db):
    creator, joiner = _make_users(db)
    svc = WorkspaceService(db)
    ws = svc.create_group("组", creator)
    inv = svc.get_invite(ws.id, creator)
    svc.redeem_invite(inv.code, joiner)
    svc.leave_group(ws.id, joiner)
    assert svc.member(ws.id, joiner.id) is None


def test_remove_member_only_creator(db):
    creator, joiner = _make_users(db)
    from app.services.auth_service import AuthService
    outsider = AuthService(db).register("carol", "test123456")
    svc = WorkspaceService(db)
    ws = svc.create_group("组", creator)
    inv = svc.get_invite(ws.id, creator)
    svc.redeem_invite(inv.code, joiner)
    # 非 creator 尝试移除 → PermissionError
    with pytest.raises(PermissionError):
        svc.remove_member(ws.id, joiner, outsider.id)
    # creator 不能移除自己
    with pytest.raises(PermissionError):
        svc.remove_member(ws.id, creator, creator.id)
    # creator 移除 joiner → 成功
    svc.remove_member(ws.id, creator, joiner.id)
    assert svc.member(ws.id, joiner.id) is None


# ---------- 批次2b：组 CRUD（编辑/转让/解散） ----------
def test_update_group_only_creator(db):
    creator, joiner = _make_users(db)
    svc = WorkspaceService(db)
    ws = svc.create_group("组", creator, "desc")
    inv = svc.get_invite(ws.id, creator)
    svc.redeem_invite(inv.code, joiner)
    # 非 creator 修改 → PermissionError
    with pytest.raises(PermissionError):
        svc.update_group(ws.id, joiner, name="改了")
    # creator 修改组名
    svc.update_group(ws.id, creator, name=" 新组名 ", description="新描述")
    db.refresh(ws)
    assert ws.name == "新组名" and ws.description == "新描述"
    # 空名拒绝
    with pytest.raises(ValueError):
        svc.update_group(ws.id, creator, name="   ")


def test_transfer_group_ownership(db):
    creator, joiner = _make_users(db)
    svc = WorkspaceService(db)
    ws = svc.create_group("组", creator)
    inv = svc.get_invite(ws.id, creator)
    svc.redeem_invite(inv.code, joiner)
    # 非 creator 转让 → PermissionError
    with pytest.raises(PermissionError):
        svc.transfer_group(ws.id, joiner, creator.id)
    # 转让给自己 → ValueError
    with pytest.raises(ValueError):
        svc.transfer_group(ws.id, creator, creator.id)
    # 转让给非成员 → LookupError
    with pytest.raises(LookupError):
        svc.transfer_group(ws.id, creator, 999999)
    # 成功转让
    svc.transfer_group(ws.id, creator, joiner.id)
    db.refresh(ws)
    assert ws.creator_id == joiner.id
    assert svc.member(ws.id, joiner.id).role == "owner"
    assert svc.member(ws.id, creator.id).role == "member"


def test_transfer_invalidates_invites(db):
    creator, joiner = _make_users(db)
    svc = WorkspaceService(db)
    ws = svc.create_group("组", creator)
    inv1 = svc.get_invite(ws.id, creator)
    svc.redeem_invite(inv1.code, joiner)
    svc.transfer_group(ws.id, creator, joiner.id)
    # 转让后无邀请码
    assert db.query(WorkspaceInvite).filter(
        WorkspaceInvite.workspace_id == ws.id).count() == 0
    # 新 owner 可重新生成
    new_inv = svc.get_invite(ws.id, joiner)
    assert new_inv is not None


def test_delete_group_only_creator(db):
    creator, joiner = _make_users(db)
    svc = WorkspaceService(db)
    ws = svc.create_group("组", creator)
    inv = svc.get_invite(ws.id, creator)
    svc.redeem_invite(inv.code, joiner)
    # 非 creator 解散 → PermissionError
    with pytest.raises(PermissionError):
        svc.delete_group(ws.id, joiner)
    # creator 解散
    svc.delete_group(ws.id, creator)
    assert db.get(Workspace, ws.id) is None
    assert db.query(WorkspaceMember).filter(
        WorkspaceMember.workspace_id == ws.id).count() == 0
    assert db.query(WorkspaceInvite).filter(
        WorkspaceInvite.workspace_id == ws.id).count() == 0