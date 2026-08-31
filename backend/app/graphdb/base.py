"""图数据库抽象接口（可插拔：Neo4j/nebula/…实现）。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class GraphStore(ABC):
    """图谱存储的最小接口（面向 FR-03 增量构建 / FR-06 导出 / FR-04 图检索）。"""

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """连通性与状态摘要。"""

    @abstractmethod
    def ensure_schema(self) -> None:
        """建约束/索引（幂等）。"""

    @abstractmethod
    def upsert_entity(self, name: str, entity_type: str, description: str | None, workspace_id: int = 1) -> None:
        """按 (name, workspace_id) 幂等写入实体节点（存在则更新属性；工作区隔离，同名异区共存）。"""

    @abstractmethod
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
        """按 (source,target,rel_type,workspace_id) 幂等写入关系边。"""

    @abstractmethod
    def remove_relations_for_note(self, source_note_id: int, workspace_id: int = 1) -> None:
        """删除某来源笔记产生的全部关系边（限本工作区）。"""

    @abstractmethod
    def remove_relation(self, source: str, target: str, relation_type: str, workspace_id: int = 1) -> None:
        """删除指定 (source, target, relation_type, workspace_id) 关系边。"""

    @abstractmethod
    def detach_entity(self, name: str, workspace_id: int = 1) -> None:
        """删除实体节点及其全部关系边（限本工作区）。"""

    @abstractmethod
    def fetch_graph(self, limit: int = 1000, workspace_id: int = 1) -> dict[str, list]:
        """导出 {nodes:[{id,name,entity_type}], edges:[{source,target,type}]}（供图谱可视化，限本工作区）。"""

    @abstractmethod
    def node_detail(self, name: str, workspace_id: int = 1) -> dict[str, Any] | None:
        """实体详情（限本工作区）。"""

    @abstractmethod
    def neighbor_search(self, names: list[str], hops: int = 1, limit: int = 50, workspace_id: int = 1) -> dict[str, list]:
        """基于实体名集合的多跳邻域检索（限本工作区），返回 {nodes, edges, matched}（供混合检索）。"""