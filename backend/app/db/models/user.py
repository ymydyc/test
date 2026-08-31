"""users 用户表（阶段七：账号体系）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    """用户账号表——注册/登录/账号管理的基础（阶段七）。

    password_hash 采用 `pbkdf2_sha256$iter$salt$hash` 格式（见 app.core.security），
    status：1=正常，0=禁用。
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="登录用户名（唯一）")
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False, comment="密码哈希（pbkdf2_sha256$iter$salt$hash）")
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="显示名称（缺省取 username）")
    status: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False, comment="账号状态：1=正常，0=禁用")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False, comment="更新时间")