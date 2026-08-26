"""启动自动建库建表（幂等）。

- 连接 MySQL（无库），`CREATE DATABASE IF NOT EXISTS`。
- 再基于 ORM 元数据 `create_all`（IF NOT EXISTS 语义）。
- 返回 True 表示成功，否则退出前抛错（由入口调用方决定是否阻断启动）。

以代码中 ORM 模型为唯一权威（与《数据库设计.md》一致）。
"""
from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import Base
from app.db import models  # noqa: F401  确保所有模型注册到 Base.metadata

log = get_logger("db.init")


def _connect(engine: Engine, tries=3) -> None:
    """带重试的连通性探测。"""
    last: Exception | None = None
    for i in range(tries):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except SQLAlchemyError as e:  # pragma: no cover
            last = e
            log.warning("MySQL 连接失败(第 %s/%s 次): %s", i + 1, tries, e)
    raise ConnectionError(f"MySQL 不可达: {last}") from last


def ensure_database() -> None:
    """确保 RAG 库存在。"""
    url = (
        f"mysql+pymysql://{settings.mysql_user}:{settings.mysql_password}"
        f"@{settings.mysql_host}:{settings.mysql_port}/?charset=utf8mb4"
    )
    admin = create_engine(url, pool_pre_ping=True, future=True)
    _connect(admin)
    with admin.connect() as conn:
        db = settings.mysql_db
        conn.execute(text(
            f"CREATE DATABASE IF NOT EXISTS `{db}` "
            f"DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        ))
        conn.commit()
    admin.dispose()


def create_tables() -> None:
    """基于 ORM 元数据自动建表（已存在则跳过）。"""
    from app.db.engine import engine as app_engine
    Base.metadata.create_all(bind=app_engine)


def _upgrade_column_types() -> None:
    """对已存在的表做幂等列类型升级（create_all 不改已存在列）。

    当前项：
    - `kb_notes.content_md` 由 TEXT 升级为 LONGTEXT（长文档笔记，避免 64KB 截断）。
    - `doc_chunks.parent_chunk_id` 字段 + 索引（父子块关联，阶段二增量）。
    仅对 MySQL 生效；SQLite（测试）无需处理。
    """
    from sqlalchemy.exc import SQLAlchemyError
    from app.db.engine import engine as app_engine
    try:
        with app_engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE kb_notes MODIFY content_md LONGTEXT NOT NULL COMMENT '笔记 Markdown 正文（长文档用 LONGTEXT）'"
            ))
            # 幂等新增 parent_chunk_id（MySQL 8.0 部分版本不支持 ADD COLUMN IF NOT EXISTS，故先查 information_schema）
            has_col = conn.execute(text(
                "SELECT COUNT(*) FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'doc_chunks' "
                "AND COLUMN_NAME = 'parent_chunk_id'"
            )).scalar()
            if not has_col:
                conn.execute(text(
                    "ALTER TABLE doc_chunks ADD COLUMN parent_chunk_id BIGINT NULL "
                    "COMMENT '父块 doc_chunks.id（父子块关联；父块自身为空）'"
                ))
            has_idx = conn.execute(text(
                "SELECT COUNT(*) FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'doc_chunks' "
                "AND INDEX_NAME = 'idx_parent_chunk'"
            )).scalar()
            if not has_idx:
                conn.execute(text("CREATE INDEX idx_parent_chunk ON doc_chunks(parent_chunk_id)"))
        log.info("已升级 kb_notes.content_md -> LONGTEXT，并幂等新增 doc_chunks.parent_chunk_id + 索引")
    except SQLAlchemyError as e:
        log.info("列类型升级跳过：%s", e)


def _ensure_utf8mb4() -> None:
    """将库中所有表转换为 utf8mb4（支持 4 字节字符，如 emoji/生僻字），幂等。

    LLM 生成的回顾摘要/笔记正文可能含 emoji（🔍 等 4 字节 UTF-8），
    旧 utf8 列无法存储会报 DataError(1366)。CONVERT 对既有数据安全（utf8 ⊂ utf8mb4）。
    """
    from sqlalchemy.exc import SQLAlchemyError
    from app.db.engine import engine as app_engine
    try:
        with app_engine.begin() as conn:
            tables = [r[0] for r in conn.execute(text(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE()"
            ))]
            for t in tables:
                conn.execute(text(
                    f"ALTER TABLE `{t}` CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                ))
        log.info("已确保全表字符集 utf8mb4（%s 张表）", len(tables))
    except SQLAlchemyError as e:
        log.info("utf8mb4 升级跳过：%s", e)


def init_all() -> None:
    """完整初始化：建库 + 建表 + 目录。"""
    settings.ensure_dirs()
    ensure_database()
    create_tables()
    _upgrade_column_types()
    _ensure_utf8mb4()
    log.info("数据库初始化完成（库=%s，表=%s 张）", settings.mysql_db, len(Base.metadata.tables))


if __name__ == "__main__":
    init_all()
    print(f"Database `{settings.mysql_db}` initialized successfully.")