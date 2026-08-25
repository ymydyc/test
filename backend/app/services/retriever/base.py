"""检索器抽象接口（供混合检索插入新增检索器，如 BM25/图路径）。"""
from __future__ import annotations

from abc import ABC, abstractmethod


class Retriever(ABC):
    """检索器最小接口：输入查询，输出统一结构的结果列表。

    返回条目统一为 dict：
    {
        "id": str,          # 结果唯一标识（向量=chunk id，图=实体名）
        "title": str,       # 展示标题（语义=所属笔记/章节，图=实体名）
        "text": str,        # 可引用的上下文文本
        "score": float,     # 归一化相似度/得分（越大越相关，用于 RRF 前展示）
        "retriever": str,   # 来源检索器名（vector/graph）
        "metadata": dict,   # 额外信息（note_id, chunk_id, parent_chunk_id, 图边等）
    }
    """

    @abstractmethod
    def retrieve(self, query: str, top_k: int = 10) -> list[dict]:
        """检索查询，返回按相关性降序的结果列表。"""