"""认证服务（阶段七）：注册 / 登录 / 注销 / 会话管理 / 改密 / 资料。

- 登录即签发 access JWT；在 `auth_sessions` 登记（按 jti 吊销）。
- 种子账号默认密码在 init_db 初始化；此处不处理密码重置（预留）。
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import create_access_token, hash_password, sha256, verify_password
from app.db.models import AuthSession, User, WorkspaceMember
from app.services.workspace_service import WorkspaceService

log = get_logger("services.auth")


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.ws_svc = WorkspaceService(db)

    # ---------- 注册 / 登录 ----------
    def register(self, username: str, password: str, display_name: str | None = None) -> User:
        username = (username or "").strip()
        if len(username) < 3:
            raise ValueError("用户名至少 3 个字符")
        if self.db.query(User).filter(User.username == username).first() is not None:
            raise ValueError("用户名已存在")
        user = User(
            username=username,
            password_hash=hash_password(password),
            display_name=(display_name or username).strip(),
            status=1,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        self.ws_svc.ensure_personal(user)  # 注册即拥有个人工作区
        return user

    def login(self, username: str, password: str, label: str | None = None) -> tuple[User, str, int]:
        """校验凭证；成功签发 JWT 并登记会话。返回 (user, token, expires_in)。"""
        user = self.db.query(User).filter(User.username == (username or "").strip()).first()
        if user is None or not verify_password(password, user.password_hash):
            raise ValueError("用户名或密码错误")
        if user.status != 1:
            raise ValueError("账号已被禁用")
        ws = self.ws_svc.ensure_personal(user)
        jti = uuid.uuid4().hex
        token = create_access_token(user.id, ws.id, jti)
        self.db.add(AuthSession(
            user_id=user.id,
            token_jti=sha256(jti),
            label=label,
            expires_at=_now_plus(settings.jwt_access_ttl_seconds),
        ))
        self.db.commit()
        return user, token, settings.jwt_access_ttl_seconds

    def last_seen(self, user_id: int, jti: str) -> None:
        """刷新会话最近活跃时间（由登录校验依赖在每次请求时调用一次）。"""
        s = (
            self.db.query(AuthSession)
            .filter(AuthSession.user_id == user_id, AuthSession.token_jti == sha256(jti))
            .first()
        )
        if s is not None:
            s.last_seen = _now()
            self.db.commit()

    # ---------- 注销 / 会话 ----------
    def logout(self, user_id: int, jti: str) -> None:
        s = (
            self.db.query(AuthSession)
            .filter(AuthSession.user_id == user_id, AuthSession.token_jti == sha256(jti))
            .first()
        )
        if s is not None:
            s.revoked = 1
            self.db.commit()

    def list_sessions(self, user_id: int) -> list[AuthSession]:
        return (
            self.db.query(AuthSession)
            .filter(AuthSession.user_id == user_id)
            .order_by(AuthSession.created_at.desc())
            .all()
        )

    def revoke_session(self, user_id: int, session_id: int) -> None:
        s = self.db.query(AuthSession).filter(AuthSession.id == session_id, AuthSession.user_id == user_id).first()
        if s is None:
            raise LookupError("会话不存在")
        s.revoked = 1
        self.db.commit()

    # ---------- 账号 ----------
    def change_password(self, user: User, old_password: str, new_password: str) -> None:
        if not verify_password(old_password, user.password_hash):
            raise ValueError("原密码错误")
        if len(new_password) < 6:
            raise ValueError("新密码至少 6 位")
        user.password_hash = hash_password(new_password)
        self.db.commit()

    def update_profile(self, user: User, display_name: str | None = None) -> User:
        if display_name is not None and display_name.strip():
            user.display_name = display_name.strip()
            self.db.commit()
            self.db.refresh(user)
        return user

    def active_workspace_id(self, user_id: int) -> int:
        ws_id = (
            self.db.query(WorkspaceMember.workspace_id)
            .filter(WorkspaceMember.user_id == user_id)
            .first()
        )
        return int(ws_id[0]) if ws_id else 0


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _now_plus(seconds: int):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(tzinfo=None)