"""阶段七 7.8 数据回填：为既有记录重算"工作区感知"唯一哈希。

背景：阶段七把业务唯一哈希列的盐从仅路径/名称扩展为 `{workspace_id}\\x00{值的}`，
新增 write/import 开始按新算法写入。但迁移阶段一~六存量数据时，历史工作区
（"历史数据默认空间"ws=3）的哈希仍是旧算法（不含 workspace 盐），导致：
- 重复写库去重（note_path_hash）、导入去重（rel_path_hash）、图谱实体去重
  （name_hash）对存量记录无法命中，可能产生重复；
- 删除级联按哈希索引用例失效。

本脚本全表重算三处哈希列（确定性、幂等，可与新写入记录混跑），使其与
`kb_service._rel_hash` / `import_service._rel_hash` / `graph_service._name_hash`
的现行算法保持一致。

安全：无删除/无结构变更，仅 UPDATE 哈希列。可在后端运行期间安全执行。
用法：python scripts/backfill_workspace_hashes.py
"""
from __future__ import annotations

import hashlib


def _ws_hash(workspace_id, value: str) -> str:
    return hashlib.sha256(f"{workspace_id}\x00{value}".encode("utf-8")).hexdigest()


def run() -> None:
    try:
        import pymysql
    except ImportError as e:  # pragma: no cover
        raise SystemExit("缺少依赖 pymysql，请先安装：pip install pymysql") from e

    from app.core.config import settings

    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        passwd=settings.mysql_password,
        db=settings.mysql_db,
        charset="utf8mb4",
    )
    cur = conn.cursor()

    tasks = [
        ("kb_notes", "note_path", "note_path_hash"),
        ("import_files", "rel_path", "rel_path_hash"),
        ("graph_entities", "name", "name_hash"),
    ]

    total = 0
    for table, valcol, hashcol in tasks:
        cur.execute(
            f"SELECT id, {valcol}, workspace_id FROM {table} WHERE workspace_id IS NOT NULL"
        )
        rows = cur.fetchall()
        n = 0
        for rid, val, ws in rows:
            if val is None:
                continue
            new_hash = _ws_hash(ws, val)
            cur.execute(
                f"UPDATE {table} SET {hashcol} = %s WHERE id = %s", (new_hash, rid)
            )
            n += 1
        conn.commit()
        total += n
        print(f"  {table}: 重算 {n} 条")

    conn.close()
    print(f"完成：共回填 {total} 条哈希列。")
    print("注：由于旧哈希不含 workstation 盐，历史工作区的导入去重可从 UUID 幂等重放验证。")


if __name__ == "__main__":
    run()