"""MySQL 引擎 / 会话 / 事务管理。"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# 图谱/向量构建会在 LLM 抽取期间长时间无 MySQL 流量；若沿用 MySQL 默认 wait_timeout
# （本机实测 120s），长笔记构建时连接会被服务端空闲超时断开，抛 "server has gone away"，
# 导致后续写入失败、图谱无法构建。故在每条物理连接建立时将会话空闲超时统一调长。
_MTY_KEEPALIVE = int(settings.mysql_wait_timeout_keepalive or 3600)


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


@event.listens_for(engine, "connect")
def _raise_session_timeout(dbapi_conn, _record) -> None:  # type: ignore[no-untyped-def]
    _set_session_keepalive(dbapi_conn)


@event.listens_for(admin_engine, "connect")
def _raise_admin_session_timeout(dbapi_conn, _record) -> None:  # type: ignore[no-untyped-def]
    _set_session_keepalive(dbapi_conn)


def _set_session_keepalive(dbapi_conn) -> None:  # type: ignore[no-untyped-def]
    dbapi_conn.cursor().execute(f"SET SESSION wait_timeout = {_MTY_KEEPALIVE}")


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