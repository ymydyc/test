"""ORM Base（Declarative）。"""
from __future__ import annotations

from sqlalchemy.orm import declarative_base

# 所有 ORM 模型继承此基类；元数据供 `create_all` 自动建表
Base = declarative_base()