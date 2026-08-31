"""阶段七 7.11 迁移：Neo4j 实体/关系唯一约束升级 + 历史数据工作区回填。

背景：阶段七把实体唯一键从 `name` 升级为 `(name, workspace_id)`，但真实库仍保留：
- 旧约束 `entity_name`（UNIQUENESS 于 `name`）——它会被 `ensure_schema` 的
  `IF NOT EXISTS` 视为"已存在同名约束"而**跳过**，导致跨工作区同名实体插入时撞旧约束报错；
- 历史实体（192 个）与关系（82 条）缺少 `workspace_id` 属性。

本脚本按序完成：
  1) 回填缺失 `workspace_id`：Entity/RELATES_TO 历史数据归入"历史数据默认空间" ws=3
     （与 MySQL 侧 `backfill_workspace_hashes.py` 的迁移空间保持一致）；
  2) 删除旧的 `entity_name`（仅 name）唯一约束；
  3) 创建新的 `(name, workspace_id)` 复合约束（优先 NODE KEY，降级 UNIQUE，与
     `Neo4jGraphStore.ensure_schema` 语义一致）。

幂等：可重复执行。安全：仅属性回填 + 约束重建，不删数据。
用法：python scripts/migrate_neo4j_workspace.py
"""
from __future__ import annotations

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("migrate.neo4j")

LEGACY_WORKSPACE_ID = 3  # 与 MySQL 回填一致：历史数据默认空间


def run() -> None:
    drv = GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password),
        connection_timeout=6,
    )
    db = settings.neo4j_database
    with drv.session(database=db) as s:

        # 1) 回填 workspace_id
        # 实体（节点标签）：历史数据归入"历史数据默认空间" ws=3
        rec = s.run(
            "MATCH (e:Entity) WHERE e.workspace_id IS NULL "
            "SET e.workspace_id = $ws RETURN count(e) AS c", {"ws": LEGACY_WORKSPACE_ID}
        ).single()
        log.info("Entity 回填 workspace_id=%s → %s 条", LEGACY_WORKSPACE_ID, rec["c"])
        # 关系（关系类型）：RELATES_TO 边缺 workspace_id 一并归入 ws=3
        rec = s.run(
            "MATCH ()-[r:RELATES_TO]->() WHERE r.workspace_id IS NULL "
            "SET r.workspace_id = $ws RETURN count(r) AS c", {"ws": LEGACY_WORKSPACE_ID}
        ).single()
        log.info("RELATES_TO 回填 workspace_id=%s → %s 条", LEGACY_WORKSPACE_ID, rec["c"])

        # 2) 删除旧 name 唯一约束（约束名与新建同名，必须先删再建）
        drops = [
            ("DROP CONSTRAINT entity_name IF EXISTS",),
            ("DROP CONSTRAINT entity_name_unique IF EXISTS",),
        ]
        for (q,) in drops:
            try:
                s.run(q)
                log.info("已尝试删除旧约束：%s", q)
            except Neo4jError as e:  # pragma: no cover
                log.warning("删除约束跳过：%s", e)

        # 3) 建立 (name, workspace_id) 复合约束
        try:
            s.run(
                "CREATE CONSTRAINT entity_name IF NOT EXISTS "
                "FOR (e:Entity) REQUIRE (e.name, e.workspace_id) IS NODE KEY"
            )
            log.info("已创建 NODE KEY 复合唯一约束 (name, workspace_id)")
        except Neo4jError as e:
            log.warning("NODE KEY 不可用，改用复合唯一约束：%s", e)
            try:
                s.run(
                    "CREATE CONSTRAINT entity_name IF NOT EXISTS "
                    "FOR (e:Entity) REQUIRE (e.name, e.workspace_id) IS UNIQUE"
                )
                log.info("已创建复合唯一约束 (name, workspace_id) IS UNIQUE")
            except Neo4jError as e2:
                raise SystemExit(f"创建复合约束失败：{e2}") from e2

    drv.close()
    print("Neo4j 约束迁移完成（节点/关系已回填 workspace_id，旧 name 唯一约束已替换为复合约束）。")


if __name__ == "__main__":
    run()