"""导入区业务服务单元测试（使用临时目录，不依赖真实 raw/）。"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import ImportFile
from app.services.import_service import ImportService, PathSafetyError


@pytest.fixture()
def raw_dir(tmp_path: Path):
    d = tmp_path / "raw"
    d.mkdir()
    return d


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool, future=True,
    )
    # SQLite 无法对 BIGINT 主键自增，手工建一张适配的表结构（与 ORM import_files 列一致）
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE import_files (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                rel_path       VARCHAR(1024) NOT NULL,
                rel_path_hash  VARCHAR(64)   NOT NULL UNIQUE,
                file_name      VARCHAR(512)  NOT NULL,
                ext_type       VARCHAR(32)   NOT NULL,
                is_dir         INTEGER       NOT NULL DEFAULT 0,
                parent_path    VARCHAR(1024),
                content_hash   CHAR(64),
                import_status  INTEGER       NOT NULL DEFAULT 0,
                file_size      BIGINT,
                content        BLOB,
                workspace_id   BIGINT,
                created_at     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        # build_tree 需联查 kb_notes（取已导入文件对应的笔记 id）
        conn.execute(text("""
            CREATE TABLE kb_notes (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                note_path         VARCHAR(1024) NOT NULL,
                note_path_hash    VARCHAR(64)   NOT NULL UNIQUE,
                title             VARCHAR(512)  NOT NULL,
                content_md        TEXT          NOT NULL,
                content_hash      CHAR(64)      NOT NULL,
                origin_import_id  BIGINT,
                frontmatter_json  TEXT,
                workspace_id      BIGINT,
                created_at        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def service(raw_dir: Path):
    return ImportService(raw_dir)


def test_save_upload_preserves_structure(service, db, raw_dir):
    svc = service
    svc.save_upload(db, ["a/b/c.txt", "root.md"], [b"hello", b"# t"], target_dir="")
    rec = db.query(ImportFile).filter(ImportFile.rel_path == "a/b/c.txt").one()
    assert rec.import_status == 0
    assert rec.rel_path_hash == svc._rel_hash("a/b/c.txt")
    assert len(rec.content_hash) == 64
    # 阶段六：内容不再落本地磁盘，而是存 DB content
    assert rec.content == b"hello"
    assert rec.file_size == 5
    assert not (raw_dir / "a" / "b" / "c.txt").exists()
    # get_content 从 DB 读取等价内容
    assert service.get_content(db, "a/b/c.txt") == b"hello"
    assert service.get_content(db, "root.md") == b"# t"


def test_rename_updates_db(service, db, raw_dir):
    svc = service
    svc.save_upload(db, ["old.txt"], [b"data"], target_dir="")
    svc.rename(db, "old.txt", "new.txt")
    rec = db.query(ImportFile).filter(ImportFile.rel_path == "new.txt").one()
    assert rec.file_name == "new.txt"
    assert rec.rel_path_hash == svc._rel_hash("new.txt")
    # 重命名仅改路径，内容保留在 DB
    assert rec.content == b"data"
    assert not (raw_dir / "old.txt").exists()


def test_rename_moves_children(service, db, raw_dir):
    svc = service
    svc.save_upload(db, ["d/f1.txt", "d/f2.txt"], [b"1", b"2"], target_dir="")
    svc.rename(db, "d", "d2")
    # 文件夹行与子文件行一并迁移（文件夹在 DB 中以 is_dir=1 行表示）
    recs = {r.rel_path for r in db.query(ImportFile).all()}
    dirs = {r.rel_path for r in db.query(ImportFile).filter(ImportFile.is_dir == 1).all()}
    assert recs == {"d2", "d2/f1.txt", "d2/f2.txt"}
    assert dirs == {"d2"}


def test_delete_recursive(service, db, raw_dir):
    svc = service
    svc.save_upload(db, ["d/f1.txt", "d/f2.txt"], [b"1", b"2"], target_dir="")
    svc.delete(db, "d")
    assert db.query(ImportFile).count() == 0
    assert service.get_content(db, "d/f1.txt") is None


def test_dir_import_status(service, db, raw_dir):
    svc = service
    svc.save_upload(db, ["d/f1.txt", "d/f2.txt"], [b"1", b"2"], target_dir="")
    # 全部未导入 -> 0
    assert svc.build_tree(db)["children"][0]["import_status"] == 0
    # 标记一个已导入，仍非全量 -> 0
    rec = db.query(ImportFile).filter(ImportFile.rel_path == "d/f1.txt").one()
    rec.import_status = 1
    db.commit()
    svc.sync_from_fs(db)  # 同步不应重置 import_status
    assert svc.build_tree(db)["children"][0]["import_status"] == 0
    # 全部已导入 -> 1
    rec2 = db.query(ImportFile).filter(ImportFile.rel_path == "d/f2.txt").one()
    rec2.import_status = 1
    db.commit()
    assert svc.build_tree(db)["children"][0]["import_status"] == 1


@pytest.mark.parametrize("bad", ["../x", "a/../../x", "/abs", r"..\x"])
def test_path_traversal_blocked(service, db, bad):
    with pytest.raises(PathSafetyError):
        service._abs(bad)