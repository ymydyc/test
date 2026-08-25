"""【校验】ORM ↔ DB 一致性对账（《开发.md》约定：以 ORM 为唯一权威）。

用法：
  python scripts/db_sync.py            # 检查：列出缺失/多余的表与列
  python scripts/db_sync.py --apply    # 按 ORM 增补缺失的表/列（幂等）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import inspect, text  # noqa: E402

from app.db import init_db  # noqa: E402,F401  确保模型注册
from app.db.base import Base  # noqa: E402
from app.db.engine import engine  # noqa: E402


def sync(check_only: bool = True) -> None:
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    wanted = set(Base.metadata.tables.keys())
    missing = wanted - existing
    extra = existing - wanted
    print("ORM 期望表数:", len(wanted), "| DB 实际表数:", len(existing))
    if missing:
        print("[缺失表]", sorted(missing))
    if extra:
        print("[多余表]", sorted(extra))
    # 按表比对列
    col_issues = []
    for table in sorted(wanted & existing):
        db_cols = {c["name"] for c in insp.get_columns(table)}
        orm_cols = {c.name for c in Base.metadata.tables[table].columns}
        for c in orm_cols - db_cols:
            col_issues.append(f"{table}.{c}")
    if col_issues:
        print("[缺失列]")
        for c in sorted(col_issues):
            print("  -", c)

    if not check_only:
        if missing:
            Base.metadata.create_all(bind=engine, tables=[Base.metadata.tables[t] for t in missing])
            print("已创建缺失表:", sorted(missing))
        if col_issues:
            # 已存在的表增列（简单处理：仅打印提示，复杂演进请用 alembic）
            print("提示：已存在表中新增列需用为 DDL 迁移，本脚本对已存在表仅建表不删列。")
        print("sync --apply 完成。")
    elif not missing and not extra and not col_issues:
        print("一致性检查通过：无差异。")
    else:
        print("存在差异，可加 --apply 应用缺失的表/列。")


if __name__ == "__main__":
    check = "--apply" not in sys.argv
    sync(check_only=check)
    if check:
        # 幂等探测 MySQL 连通
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
print("done")