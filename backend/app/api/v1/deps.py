"""阶段七/八 认证/工作区依赖：从 JWT 解析当前用户与当前工作区。

- `get_current_user`：校验访问令牌（签名/过期/会话未吊销/账号正常）。
- `get_current_workspace`：返回当前用户的活动工作区。
  - 阶段七：默认返回用户 personal 工作区。
  - 阶段八（批次4）：支持可选请求头 `X-Workspace-Id` 显式指定活动工作区，
    并校验当前用户确为该工作区成员（组共享）；未提供则回退 personal。
- `workspace_filter(Model, ws_id)`：SQLAlchemy 查询隔离过滤辅助。
"""
from __future__ import annotations

from typing import Type, TypeVar

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Query, Session

from app.core.security import decode_token, sha256
from app.db.engine import get_db
from app.db.models import AuthSession, User, Workspace, WorkspaceMember
from app.db.scoping import workspace_scope

_bearer = HTTPBearer(auto_error=False)

M = TypeVar("M")


def _unauthorized(detail: str = "未认证或令牌无效") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail,
                         headers={"WWW-Authenticate": "Bearer"})


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None or not creds.credentials:
        raise _unauthorized()
    payload = decode_token(creds.credentials)
    if payload is None:
        raise _unauthorized("令牌无效或已过期")
    user_id = payload.get("sub")
    jti = payload.get("jti")
    if not user_id:
        raise _unauthorized()
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise _unauthorized("账号不存在")
    if user.status != 1:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已被禁用")
    # 校验会话未被吊销
    if jti:
        s = db.query(AuthSession).filter(AuthSession.user_id == user_id, AuthSession.token_jti == sha256(jti)).first()
        if s is None or s.revoked:
            raise _unauthorized("会话已失效，请重新登录")
    return user


def get_current_workspace(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_workspace_id: str | None = Header(default=None, description="可选：显式指定活动工作区（组共享场景）"),
) -> Workspace:
    # 阶段八（批次4）：支持请求头显式指定活动工作区（组共享）。
    # 校验当前用户确实为该工作区成员，否则无权限访问——实现"组内共享、非成员不可见、被移除后失权限"。
    if x_workspace_id is not None and str(x_workspace_id).strip().isdigit():
        ws_id = int(str(x_workspace_id).strip())
        ws = db.query(Workspace).filter(Workspace.id == ws_id).first()
        member = db.query(WorkspaceMember).filter(
            WorkspaceMember.workspace_id == ws_id,
            WorkspaceMember.user_id == user.id,
        ).first()
        if ws is None or member is None:
            raise HTTPException(status_code=403, detail="工作区不存在或无权限访问")
        return ws
    ws_id = _active_workspace_id(user, db)
    ws = db.query(Workspace).filter(Workspace.id == ws_id).first()
    if ws is None:
        raise HTTPException(status_code=403, detail="工作区不存在或无权限访问")
    return ws


def _active_workspace_id(user: User, db: Session) -> int:
    """当前用户的活动工作区：阶段七默认其 personal 工作区；组切换（阶段八）再扩展。"""
    ws_id = (
        db.query(WorkspaceMember.workspace_id)
        .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
        .filter(WorkspaceMember.user_id == user.id, Workspace.type == "personal")
        .limit(1)
        .scalar()
    )
    if ws_id is None:
        # 兜底：兼容仅按 creator 关联的旧数据
        ws_id = (
            db.query(Workspace.id)
            .filter(Workspace.creator_id == user.id, Workspace.type == "personal")
            .limit(1)
            .scalar()
        )
    if ws_id is None:
        raise HTTPException(status_code=403, detail="用户尚未初始化个人工作区")
    return int(ws_id)


def scope_query(q: Query, model: Type[M], workspace_id: int) -> Query:
    """把查询按 model.workspace_id 收缩到指定工作区。

    迁移/回填前的历史数据 workspace_id 为 NULL，视为仅归属默认个人工作区（id=1）；其余按工作区精确隔离。
    """
    return q.filter(workspace_scope(model, workspace_id))