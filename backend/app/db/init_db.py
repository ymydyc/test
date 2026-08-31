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
    - `import_files.content` 字段（阶段六：导入区文件内容存 MySQL，LONGBLOB）。
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
            # 阶段六：幂等新增 import_files.content（导入区文件内容存 MySQL）
            has_content = conn.execute(text(
                "SELECT COUNT(*) FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'import_files' "
                "AND COLUMN_NAME = 'content'"
            )).scalar()
            if not has_content:
                conn.execute(text(
                    "ALTER TABLE import_files ADD COLUMN content LONGBLOB NULL "
                    "COMMENT '原始文件内容（阶段六起直接存 MySQL，文件夹为 NULL）'"
                ))
        log.info("已升级 kb_notes.content_md -> LONGTEXT，补齐 doc_chunks.parent_chunk_id 索引，幂等新增 import_files.content")
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


# 阶段七：需要按工作区隔离的业务表（其 ORM `workspace_id` 列需对已存在表幂等补齐）
_WS_TABLES = [
    "import_files",
    "kb_notes",
    "doc_chunks",
    "graph_entities",
    "graph_relations",
    "review_records",
]


def _ensure_chat_session_user_id() -> None:
    """为 chat_sessions 表幂等新增 user_id 列（阶段七迁移遗漏）。

    ORM 模型已定义 user_id，但已存在的表不会自动补列。
    """
    from sqlalchemy.exc import SQLAlchemyError
    from app.db.engine import engine as app_engine
    try:
        with app_engine.begin() as conn:
            has_col = conn.execute(text(
                "SELECT COUNT(*) FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_sessions' "
                "AND COLUMN_NAME = 'user_id'"
            )).scalar()
            if not has_col:
                conn.execute(text(
                    "ALTER TABLE chat_sessions ADD COLUMN user_id BIGINT NULL "
                    "COMMENT '所属用户 users.id（阶段七：会话按用户区分）'"
                ))
                has_idx = conn.execute(text(
                    "SELECT COUNT(*) FROM information_schema.STATISTICS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_sessions' "
                    "AND INDEX_NAME = 'idx_chat_sessions_user'"
                )).scalar()
                if not has_idx:
                    conn.execute(text("CREATE INDEX idx_chat_sessions_user ON chat_sessions(user_id)"))
                log.info("已补齐 chat_sessions.user_id 列与索引")
    except SQLAlchemyError as e:
        log.info("chat_sessions.user_id 迁移跳过：%s", e)


def _ensure_workspace_columns() -> None:
    """为已存在的业务表幂等新增 `workspace_id`（BIGINT NULL）与索引（阶段七）。

    ORM `create_all` 不会给已存在的表补列，故单独迁移；仅 MySQL 生效。
    """
    from sqlalchemy.exc import SQLAlchemyError
    from app.db.engine import engine as app_engine
    try:
        with app_engine.begin() as conn:
            for table in _WS_TABLES:
                has_col = conn.execute(text(
                    "SELECT COUNT(*) FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t "
                    "AND COLUMN_NAME = 'workspace_id'"
                ), {"t": table}).scalar()
                if not has_col:
                    conn.execute(text(
                        f"ALTER TABLE `{table}` ADD COLUMN workspace_id BIGINT NULL "
                        f"COMMENT '所属工作区 workspaces.id（阶段七：隔离键）'"
                    ))
                has_idx = conn.execute(text(
                    "SELECT COUNT(*) FROM information_schema.STATISTICS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t "
                    "AND INDEX_NAME = :i"
                ), {"t": table, "i": f"idx_ws_{table}"}).scalar()
                if not has_idx:
                    conn.execute(text(f"CREATE INDEX idx_ws_{table} ON `{table}`(workspace_id)"))
        log.info("已补齐工作区隔离列/索引（%s 张表）", len(_WS_TABLES))
    except SQLAlchemyError as e:
        log.info("工作区列迁移跳过：%s", e)


def _migrate_workspace_and_seed() -> None:
    """阶段七数据与账号初始化：
    1) 确保种子账号存在（test_alice / test_bob）并各自初始化个人工作区；
    2) 确保「历史数据默认空间」存在，并把历史业务数据（workspace_id 为 NULL）回填到该空间。
    """
    from datetime import datetime  # noqa: F401
    from sqlalchemy.orm import Session
    from app.core.config import settings
    from app.core.security import hash_password
    from app.db.engine import SessionLocal
    from app.db.models import User, Workspace, WorkspaceMember

    try:
        db = SessionLocal()
    except Exception as e:  # pragma: no cover
        log.warning("工作区/种子初始化跳过（会话不可用）：%s", e)
        return
    try:
        seed_users = [u.strip() for u in (settings.seed_test_accounts or "").split(",") if u.strip()]
        if not seed_users:
            return
        first_ws_owner_id: int | None = None
        for username in seed_users:
            user = db.query(User).filter(User.username == username).first()
            if user is None:
                user = User(
                    username=username,
                    password_hash=hash_password(settings.seed_test_password),
                    display_name=username,
                    status=1,
                )
                db.add(user)
                db.commit()
                db.refresh(user)
                log.info("已创建种子账号 %s", username)
            if first_ws_owner_id is None:
                first_ws_owner_id = user.id

        # 确保每个种子账号有 personal 工作区
        ws_ids: list[int] = []
        for username in seed_users:
            user = db.query(User).filter(User.username == username).first()
            ws = db.query(Workspace).filter(Workspace.creator_id == user.id, Workspace.type == "personal").first()
            if ws is None:
                ws = Workspace(name=f"{user.username} 的个人空间", type="personal",
                               creator_id=user.id, description="个人空间")
                db.add(ws)
                db.flush()
                db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
                db.commit()
            ws_ids.append(ws.id)

        # 历史数据默认空间（creator 取首个种子账号）——老数据回填目标
        legacy = (
            db.query(Workspace)
            .filter(Workspace.name == "历史数据默认空间", Workspace.creator_id == first_ws_owner_id)
            .first()
        )
        if legacy is None and first_ws_owner_id is not None:
            legacy = Workspace(name="历史数据默认空间", type="personal",
                               creator_id=first_ws_owner_id, description="阶段七迁移前历史数据归属空间")
            db.add(legacy)
            db.commit()
        if legacy is not None:
            # 回填历史数据：workspace_id 为 NULL 的行 → legacy.id
            from sqlalchemy import table, column
            for tname in _WS_TABLES:
                t = table(tname, column("workspace_id"))
                db.execute(
                    t.update().where(t.c.workspace_id.is_(None)).values(workspace_id=legacy.id)
                )
            db.commit()
            log.info("历史数据已回填到工作区 %s（%s）", legacy.id, legacy.name)
    except Exception as e:  # pragma: no cover
        db.rollback()
        log.warning("工作区/种子初始化失败：%s", e)
    finally:
        db.close()


def init_all() -> None:
    """完整初始化：建库 + 建表 + 目录 + 阶段七（工作区列/种子/历史回填）。"""
    settings.ensure_dirs()
    ensure_database()
    create_tables()
    _upgrade_column_types()
    _ensure_utf8mb4()
    _ensure_chat_session_user_id()
    _ensure_workspace_columns()
    _migrate_workspace_and_seed()
    log.info("数据库初始化完成（库=%s，表=%s 张）", settings.mysql_db, len(Base.metadata.tables))


if __name__ == "__main__":
    init_all()
    print(f"Database `{settings.mysql_db}` initialized successfully.")