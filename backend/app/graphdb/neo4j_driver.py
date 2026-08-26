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
        # 实体以 name 为唯一合并键
        try:
            self._driver.execute_query(
                "CREATE CONSTRAINT entity_name IF NOT EXISTS FOR (e:Entity) REQUIRE e.name IS UNIQUE",
                database_=self.database,
            )
        except Neo4jError as e:  # pragma: no cover
            log.warning("建约束跳过：%s", e)

    # ---------- 实体/关系 ----------
    def upsert_entity(self, name: str, entity_type: str, description: str | None) -> None:
        self._run(
            "MERGE (e:Entity {name: $name}) "
            "ON CREATE SET e.entity_type = $t, e.description = $d "
            "ON MATCH SET e.entity_type = $t, e.description = coalesce($d, e.description)",
            {"name": name, "t": entity_type, "d": description},
        )

    def upsert_relation(
        self,
        rel_type: str,
        source: str,
        target: str,
        description: str | None,
        source_note_id: int | None,
        meta_id: str,
    ) -> None:
        # MERGE 关系时带 relation_type 属性作为区分键，支持同对节点多种关系共存
        self._run(
            "MATCH (a:Entity {name: $s}), (b:Entity {name: $t}) "
            "MERGE (a)-[r:RELATES_TO {relation_type: $rt}]->(b) "
            "ON CREATE SET r.description = $d, r.source_note_id = $sn, r.meta_id = $mid "
            "ON MATCH SET r.description = $d, r.source_note_id = $sn, r.meta_id = $mid",
            {"s": source, "t": target, "rt": rel_type, "d": description,
             "sn": source_note_id, "mid": meta_id},
        )

    def remove_relations_for_note(self, source_note_id: int) -> None:
        self._run(
            "MATCH (:Entity)-[r:RELATES_TO]->(:Entity) WHERE r.source_note_id = $sn "
            "DETACH DELETE r",
            {"sn": source_note_id},
        )

    def detach_entity(self, name: str) -> None:
        self._run(
            "MATCH (e:Entity {name: $name}) DETACH DELETE e", {"name": name},
        )

    # ---------- 导出（FR-06 图谱可视化数据源） ----------
    def fetch_graph(self, limit: int = 1000) -> dict[str, list]:
        nodes: list[dict] = []
        edges: list[dict] = []
        for rec in self._run(
            "MATCH (e:Entity) RETURN e.name AS name, e.entity_type AS t "
            "ORDER BY name LIMIT $limit", {"limit": limit}
        ):
            nodes.append({"id": rec["name"], "name": rec["name"], "entity_type": rec["t"]})
        for rec in self._run(
            "MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) "
            "RETURN a.name AS s, b.name AS t, r.relation_type AS rt, "
            "       r.description AS d, r.source_note_id AS sn LIMIT $limit",
            {"limit": limit},
        ):
            edges.append({
                "id": f"{rec['s']}::{rec['t']}::{rec['rt']}",
                "source": rec["s"], "target": rec["t"],
                "relation_type": rec["rt"], "description": rec["d"], "source_note_id": rec["sn"],
            })
        return {"nodes": nodes, "edges": edges}

    def node_detail(self, name: str) -> dict[str, Any] | None:
        """实体详情（与 fetch_graph 同源，保证前端点击节点能取到详情）。

        返回 {name, entity_type, description, source_note_ids, edges}；实体不存在返回 None。
        """
        recs = self._run(
            "MATCH (e:Entity {name: $name}) RETURN e.name AS n, "
            "e.entity_type AS t, e.description AS d", {"name": name}
        )
        if not recs:
            return None
        rec = recs[0]
        edges: list[dict] = []
        note_ids: set[int] = set()
        # 出边 + 入边（无向展示全部关联）
        for row in self._run(
            "MATCH (e:Entity {name: $name})-[r:RELATES_TO]->(o:Entity) "
            "RETURN o.name AS o, r.relation_type AS rt, r.description AS d, r.source_note_id AS sn",
            {"name": name},
        ):
            edges.append({"source": rec["n"], "target": row["o"],
                          "relation_type": row["rt"], "description": row["d"]})
            if row["sn"] is not None:
                note_ids.add(row["sn"])
        for row in self._run(
            "MATCH (i:Entity)-[r:RELATES_TO]->(e:Entity {name: $name}) "
            "RETURN i.name AS i, r.relation_type AS rt, r.description AS d, r.source_note_id AS sn",
            {"name": name},
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
    def neighbor_search(self, names: list[str], hops: int = 1, limit: int = 50) -> dict[str, list]:
        if not names:
            return {"nodes": [], "edges": [], "matched": []}
        nodes: list[dict] = []
        edges: list[dict] = []
        seen_n: set[str] = set()
        seen_e: set[str] = set()
        # 多跳展开（hops<=2 内安全）
        for rec in self._run(
            "MATCH p=(a:Entity)-[r:RELATES_TO*1..%(hops)s]-(b:Entity) "
            "WHERE a.name IN $names "
            "UNWIND nodes(p) AS nd RETURN DISTINCT nd" % {"hops": max(1, hops)},
            {"names": names},
        ):
            nd = rec["nd"]
            n = nd["name"]
            if n not in seen_n:
                seen_n.add(n)
                nodes.append({"id": n, "name": n, "entity_type": nd.get("entity_type")})
        for rec in self._run(
            "MATCH (a:Entity)-[r:RELATES_TO]-(b:Entity) "
            "WHERE a.name IN $names OR b.name IN $names "
            "RETURN DISTINCT a.name AS s, b.name AS t, r.relation_type AS rt LIMIT $limit",
            {"names": names, "limit": limit},
        ):
            key = f"{rec['s']}::{rec['t']}::{rec['rt']}"
            if key not in seen_e:
                seen_e.add(key)
                edges.append({"id": key, "source": rec["s"], "target": rec["t"],
                              "relation_type": rec["rt"]})
        return {"nodes": nodes, "edges": edges, "matched": names}