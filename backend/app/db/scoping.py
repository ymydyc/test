"""阶段七 工作区隔离辅助：SQLAlchemy 查询按 workspace_id 收缩。

语义（对齐 api/v1/deps.scope_query）：
- 显式 `workspace_id == N` 的记录只属于工作区 N；
- 默认工作区(id=1)：同时兼容迁移前历史数据——`workspace_id` 为 NULL 的记录视为归属默认工作区。
所以对默认工作区使用 `(col == 1) OR (col IS NULL)`，其余工作区严格等值。
"""
from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Query

DEFAULT_WORKSPACE_ID = 1


def workspace_scope(model, workspace_id: int):
    """返回按工作区过滤某模型（须含 workspace_id 列）的过滤表达式，供 Query.filter 使用。"""
    col = model.workspace_id
    if workspace_id == DEFAULT_WORKSPACE_ID:
        return or_(col == DEFAULT_WORKSPACE_ID, col.is_(None))
    return col == workspace_id


def scope(q: Query, model, workspace_id: int) -> Query:
    """对既有 Query 施加工作区过滤。"""
    return q.filter(workspace_scope(model, workspace_id))