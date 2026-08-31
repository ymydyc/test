"""认证路由（阶段七）：注册 / 登录 / 登出 / 本人信息 / 改密 / 会话管理。

未登录访问受保护业务接口时由 `get_current_user` 依赖抛出 401。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user, get_current_workspace
from app.core.logging import get_logger
from app.core.security import decode_token
from app.db.engine import get_db
from app.db.models import User, Workspace
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    SessionInfo,
    TokenResponse,
    UpdateProfileRequest,
    UserInfo,
    WorkspaceInfo,
)
from app.services.auth_service import AuthService

log = get_logger("api.auth")
router = APIRouter()
_bearer = HTTPBearer(auto_error=False)


def _401(detail: str = "未认证或令牌无效") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail,
                         headers={"WWW-Authenticate": "Bearer"})


def _to_user_info(u: User) -> UserInfo:
    return UserInfo(id=u.id, username=u.username, display_name=u.display_name,
                    status=u.status, created_at=u.created_at)


@router.post("/register", response_model=TokenResponse, summary="注册（自动创建个人工作区并登录）")
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    svc = AuthService(db)
    try:
        user = svc.register(body.username, body.password, body.display_name)
        user, token, expires = svc.login(user.username, body.password, label="注册后自动登录")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    ws = WorkspaceService(db).ensure_personal(user)
    return _token_response(user, token, expires, ws)


@router.post("/login", response_model=TokenResponse, summary="登录")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    svc = AuthService(db)
    try:
        user, token, expires = svc.login(body.username, body.password, label="登录")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    ws = svc.ws_svc.ensure_personal(user)
    return _token_response(user, token, expires, ws)


@router.post("/logout", summary="登出（吊销当前令牌）")
def logout(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
):
    if creds is None:
        raise _401()
    payload = decode_token(creds.credentials)
    jti = payload.get("jti") if payload else None
    user = None
    if payload:
        user = db.query(User).filter(User.id == payload.get("sub")).first()
    if user is not None and jti:
        AuthService(db).logout(user.id, jti)
    return {"ok": True}


@router.get("/me", response_model=UserInfo, summary="当前账号信息")
def me(user: User = Depends(get_current_user)):
    return _to_user_info(user)


@router.get("/me/workspace", response_model=WorkspaceInfo, summary="当前活动工作区")
def my_workspace(ws: Workspace = Depends(get_current_workspace)):
    return WorkspaceInfo(id=ws.id, name=ws.name, type=ws.type)


@router.post("/password", summary="修改密码")
def change_password(body: ChangePasswordRequest, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    try:
        AuthService(db).change_password(user, body.old_password, body.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@router.patch("/me", response_model=UserInfo, summary="更新资料")
def update_profile(body: UpdateProfileRequest, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    return _to_user_info(AuthService(db).update_profile(user, body.display_name))


@router.get("/sessions", response_model=list[SessionInfo], summary="活动会话列表")
def list_sessions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = AuthService(db).list_sessions(user.id)
    return [_to_session(s) for s in rows]


@router.delete("/sessions/{session_id}", summary="吊销指定会话")
def revoke_session(session_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        AuthService(db).revoke_session(user.id, session_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"ok": True}


def _to_session(s) -> SessionInfo:
    return SessionInfo(id=s.id, label=s.label, created_at=s.created_at,
                       last_seen=s.last_seen, expires_at=s.expires_at, revoked=s.revoked)


def _token_response(user: User, token: str, expires: int, ws: Workspace) -> TokenResponse:
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires,
        user=_to_user_info(user),
        workspace=WorkspaceInfo(id=ws.id, name=ws.name, type=ws.type),
    )


from app.services.workspace_service import WorkspaceService  # noqa: E402