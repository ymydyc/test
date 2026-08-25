"""MySQL 引擎 / 会话 / 事务管理。"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def build_url(host=None, port=None, user=None, password=None, db=None) -> str:
    """构造 pymysql 连接串（encoding 统一 utf8mb4）。"""
    host = host or settings.mysql_host
    port = port or settings.mysql_port
    user = user or settings.mysql_user
    password = password or settings.mysql_password
    db = db or settings.mysql_db
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{db}?charset=utf8mb4"


# 默认引擎指向 RAG 库（若库已存在）
engine = create_engine(
    build_url(),
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False,
    future=True,
)

# 不带库的连接引擎（用于建库）
admin_engine = create_engine(
    build_url(db=None),
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：提供请求级会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """脚本/服务层通用的会话上下文。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()