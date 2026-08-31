"""Neo4j 图数据库实现（GraphStore 接口）。
"""
from __future__ import annotations

from typing import Any

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

from app.core.config import settings
from app.core.logging import get_logger
from app.graphdb.base import GraphStore

log = get_logger("graphdb.neo4j")


class Neo4jGraphStore(GraphStore):
    """对接 Docker Neo4j（5.x），Cypher 幂等建实体/关系。"""

    def __init__(
        self,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
    ) -> None:
        self.uri = uri or settings.neo4j_uri
        self.user = user or settings.neo4j_user
        self.password = password or settings.neo4j_password
        self.database = database or settings.neo4j_database
        self._driver = GraphDatabase.driver(
            self.uri, auth=(self.user, self.password), connection_timeout=6,
        )

    def close(self) -> None:
        self._driver.close()

    # ---------- 基础 ----------
    def _run(self, query: str, params: dict | None = None):
        # 默认会话即 WRITE 访问模式；显式指定会因驱动版本各异报错，故不传
        with self._driver.session(database=self.database) as s:
            return list(s.run(query, params or {}))

    def health(self) -> dict[str, Any]:
        try:
            self._driver.verify_connectivity()
            return {"ok": True, "uri": self.uri, "database": self.database}
        except Exception as e:  # pragma: no cover
            return {"ok": False, "uri": self.uri, "error": str(e)}

    def ensure_schema(self) -> None:
        # 实体以 (name, workspace_id) 复合作为唯一合并键：同名实体在不同工作区可共存（阶段七隔离）
        try:
            self._driver.execute_query(
                "CREATE CONSTRAINT entity_name IF NOT EXISTS "
                "FOR (e:Entity) REQUIRE (e.name, e.workspace_id) IS NODE KEY",
                database_=self.database,
            )
        except Neo4jError as e:  # 驱动/版本不支持 NODE KEY → 改用复合唯一约束
            log.warning("建 NODE KEY 约束跳过，改用复合唯一约束：%s", e)
            try:
                self._driver.execute_query(
                    "CREATE CONSTRAINT entity_name IF NOT EXISTS "
                    "FOR (e:Entity) REQUIRE (e.name, e.workspace_id) IS UNIQUE",
                    database_=self.database,
                )
            except Neo4jError as e2:  # 幂等容错：约束已存在等场景不阻断
                log.warning("建复合唯一约束跳过：%s", e2)

    # ---------- 实体/关系 ----------
    def upsert_entity(self, name: str, entity_type: str, description: str | None, workspace_id: int = 1) -> None:
        # 实体唯一键为 (name, workspace_id)，保证不同工作区同名实体互不覆盖
        self._run(
            "MERGE (e:Entity {name: $name, workspace_id: $ws}) "
            "ON CREATE SET e.entity_type = $t, e.description = $d "
            "ON MATCH SET e.entity_type = $t, e.description = coalesce($d, e.description)",
            {"name": name, "t": entity_type, "d": description, "ws": workspace_id},
        )

    def upsert_relation(
        self,
        rel_type: str,
        source: str,
        target: str,
        description: str | None,
        source_note_id: int | None,
        meta_id: str,
        workspace_id: int = 1,
    ) -> None:
        # 端点实体与关系边都带 workspace_id；关系 MERGE 键含 workspace_id，防止跨空间同名实体互相连接
        self._run(
            "MATCH (a:Entity {name: $s, workspace_id: $ws}), (b:Entity {name: $t, workspace_id: $ws}) "
            "MERGE (a)-[r:RELATES_TO {relation_type: $rt, workspace_id: $ws}]->(b) "
            "ON CREATE SET r.description = $d, r.source_note_id = $sn, r.meta_id = $mid "
            "ON MATCH SET r.description = $d, r.source_note_id = $sn, r.meta_id = $mid",
            {"s": source, "t": target, "rt": rel_type, "d": description,
             "sn": source_note_id, "mid": meta_id, "ws": workspace_id},
        )

    def remove_relations_for_note(self, source_note_id: int, workspace_id: int = 1) -> None:
        self._run(
            "MATCH (:Entity)-[r:RELATES_TO]->(:Entity) "
            "WHERE r.source_note_id = $sn AND r.workspace_id = $ws "
            "DETACH DELETE r",
            {"sn": source_note_id, "ws": workspace_id},
        )

    def remove_relation(self, source: str, target: str, relation_type: str, workspace_id: int = 1) -> None:
        self._run(
            "MATCH (a:Entity {name: $s, workspace_id: $ws})-[r:RELATES_TO {relation_type: $rt, workspace_id: $ws}]->(b:Entity {name: $t, workspace_id: $ws}) "
            "DELETE r",
            {"s": source, "t": target, "rt": relation_type, "ws": workspace_id},
        )

    def detach_entity(self, name: str, workspace_id: int = 1) -> None:
        self._run(
            "MATCH (e:Entity {name: $name, workspace_id: $ws}) DETACH DELETE e",
            {"name": name, "ws": workspace_id},
        )

    # ---------- 导出（FR-06 图谱可视化数据源） ----------
    def fetch_graph(self, limit: int = 1000, workspace_id: int = 1) -> dict[str, list]:
        nodes: list[dict] = []
        edges: list[dict] = []
        for rec in self._run(
            "MATCH (e:Entity) WHERE e.workspace_id = $ws "
            "RETURN e.name AS name, e.entity_type AS t "
            "ORDER BY name LIMIT $limit", {"limit": limit, "ws": workspace_id}
        ):
            nodes.append({"id": rec["name"], "name": rec["name"], "entity_type": rec["t"]})
        for rec in self._run(
            "MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) "
            "WHERE a.workspace_id = $ws "
            "RETURN a.name AS s, b.name AS t, r.relation_type AS rt, "
            "       r.description AS d, r.source_note_id AS sn LIMIT $limit",
            {"limit": limit, "ws": workspace_id},
        ):
            edges.append({
                "id": f"{rec['s']}::{rec['t']}::{rec['rt']}",
                "source": rec["s"], "target": rec["t"],
                "relation_type": rec["rt"], "description": rec["d"], "source_note_id": rec["sn"],
            })
        return {"nodes": nodes, "edges": edges}

    def node_detail(self, name: str, workspace_id: int = 1) -> dict[str, Any] | None:
        """实体详情（与 fetch_graph 同源，保证前端点击节点能取到详情）。

        返回 {name, entity_type, description, source_note_ids, edges}；实体不存在返回 None。
        """
        recs = self._run(
            "MATCH (e:Entity {name: $name, workspace_id: $ws}) RETURN e.name AS n, "
            "e.entity_type AS t, e.description AS d", {"name": name, "ws": workspace_id}
        )
        if not recs:
            return None
        rec = recs[0]
        edges: list[dict] = []
        note_ids: set[int] = set()
        # 出边 + 入边（无向展示全部关联）
        for row in self._run(
            "MATCH (e:Entity {name: $name, workspace_id: $ws})-[r:RELATES_TO]->(o:Entity) "
            "WHERE o.workspace_id = $ws "
            "RETURN o.name AS o, r.relation_type AS rt, r.description AS d, r.source_note_id AS sn",
            {"name": name, "ws": workspace_id},
        ):
            edges.append({"source": rec["n"], "target": row["o"],
                          "relation_type": row["rt"], "description": row["d"]})
            if row["sn"] is not None:
                note_ids.add(row["sn"])
        for row in self._run(
            "MATCH (i:Entity)-[r:RELATES_TO]->(e:Entity {name: $name, workspace_id: $ws}) "
            "WHERE i.workspace_id = $ws "
            "RETURN i.name AS i, r.relation_type AS rt, r.description AS d, r.source_note_id AS sn",
            {"name": name, "ws": workspace_id},
        ):
            edges.append({"source": row["i"], "target": rec["n"],
                          "relation_type": row["rt"], "description": row["d"]})
            if row["sn"] is not None:
                note_ids.add(row["sn"])
        return {
            "name": rec["n"], "entity_type": rec["t"], "description": rec["d"],
            "source_note_ids": sorted(note_ids), "edges": edges,
        }

    # ---------- 图谱检索（FR-04 图路径召回） ----------
    def neighbor_search(self, names: list[str], hops: int = 1, limit: int = 50, workspace_id: int = 1) -> dict[str, list]:
        if not names:
            return {"nodes": [], "edges": [], "matched": []}
        nodes: list[dict] = []
        edges: list[dict] = []
        seen_n: set[str] = set()
        seen_e: set[str] = set()
        # 多跳展开（hops<=2 内安全），全程限定本工作区
        for rec in self._run(
            "MATCH p=(a:Entity)-[r:RELATES_TO*1..%(hops)s]-(b:Entity) "
            "WHERE a.name IN $names AND a.workspace_id = $ws AND r.workspace_id = $ws "
            "UNWIND nodes(p) AS nd "
            "WITH DISTINCT nd WHERE nd.workspace_id = $ws "
            "RETURN DISTINCT nd" % {"hops": max(1, hops)},
            {"names": names, "ws": workspace_id},
        ):
            nd = rec["nd"]
            n = nd["name"]
            if n not in seen_n:
                seen_n.add(n)
                nodes.append({"id": n, "name": n, "entity_type": nd.get("entity_type")})
        for rec in self._run(
            "MATCH (a:Entity)-[r:RELATES_TO]-(b:Entity) "
            "WHERE (a.name IN $names OR b.name IN $names) "
            "  AND a.workspace_id = $ws AND r.workspace_id = $ws "
            "RETURN DISTINCT a.name AS s, b.name AS t, r.relation_type AS rt LIMIT $limit",
            {"names": names, "limit": limit, "ws": workspace_id},
        ):
            key = f"{rec['s']}::{rec['t']}::{rec['rt']}"
            if key not in seen_e:
                seen_e.add(key)
                edges.append({"id": key, "source": rec["s"], "target": rec["t"],
                              "relation_type": rec["rt"]})
        return {"nodes": nodes, "edges": edges, "matched": names}