"""阶段七 7.5/7.8 向量库回填：为历史 Chroma 记录补齐 workspace_id。

背景：阶段七按 workspace_id 做向量隔离后，检索/删除都会带 `workspace_id`
过滤（见 chroma_store._where_and → $and）。但阶段一~六存量写入的 Chroma 记录
元数据没有 `workspace_id`，导致按工作区过滤后这些记录"不可见"。

解决：用现有元数据建立工作区归属映射后回填：
- `chunks`  集合：元数据 `note_id`  → 查询 `kb_notes.id` 得到 `workspace_id`；
- `entities` 集合：元数据 `entity_id` → 查询 `graph_entities.id` 得到 `workspace_id`。
无缺失映射的记录跳过（可能已含 workspace_id 或源表无记录，无法判定归属）。

幂等：已带 workspace_id 的记录不动；重复运行结果一致。
安全：仅更新元数据，不改 embeddings/documents，不动集合结构。
用法：python scripts/backfill_chroma_workspace.py
"""
from __future__ import annotations

from app.core.config import settings
from app.vectorstore.chroma_store import ChromaVectorStore, CHUNK_COLLECTION, ENTITY_COLLECTION

BATCH = 500


def _load_map(sql: str, key_col: int) -> dict:
    """查询 id→workspace_id 映射，返回 {id: workspace_id}。"""
    import pymysql

    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        passwd=settings.mysql_password,
        db=settings.mysql_db,
        charset="utf8mb4",
    )
    cur = conn.cursor()
    cur.execute(sql)
    m = {int(r[key_col]): int(r[2]) for r in cur.fetchall() if r[key_col] is not None and r[2] is not None}
    conn.close()
    return m


def _backfill(store: ChromaVectorStore, collection: str, link_key: str, lookup: dict,
              fallback_ws: int | None = None) -> tuple[int, int]:
    """回填缺 workspace_id 的记录。返回 (fixed, skipped)。

    优先按 link_key 从 lookup 映射；若无法映射且给出 fallback_ws，
    则归入该默认空间（用于历史阶段 1~6 实体被合并/删除后残留的孤儿向量）。
    """
    c = store._collection(collection)
    total = c.count()
    fixed = 0
    skipped = 0
    offset = 0
    while True:
        got = c.get(limit=BATCH, offset=offset)
        ids = got.get("ids") or []
        if not ids:
            break
        metas = got.get("metadatas") or [{}] * len(ids)
        updates: dict = {"ids": [], "metadatas": []}
        for i, m in enumerate(metas):
            m = m or {}
            if m.get("workspace_id") is not None:
                continue  # 已回填
            link_val = m.get(link_key)
            ws = lookup.get(int(link_val)) if link_val is not None else None
            if ws is None:
                if fallback_ws is None:
                    skipped += 1
                    continue  # 无法判定归属，跳过
                ws = fallback_ws  # 孤儿历史向量归默认空间
            m["workspace_id"] = ws
            updates["ids"].append(ids[i])
            updates["metadatas"].append(m)
        if updates["ids"]:
            c.update(ids=updates["ids"], metadatas=updates["metadatas"])
            fixed += len(updates["ids"])
        offset += BATCH
        if offset >= total:
            break
    return fixed, skipped


def run() -> None:
    store = ChromaVectorStore()

    note_lookup = _load_map(
        "SELECT id, NULL, workspace_id FROM kb_notes WHERE workspace_id IS NOT NULL", 0
    )
    ent_lookup = _load_map(
        "SELECT id, NULL, workspace_id FROM graph_entities WHERE workspace_id IS NOT NULL", 0
    )
    print(f"kb_notes 映射 {len(note_lookup)} 条；graph_entities 映射 {len(ent_lookup)} 条")

    for col, link_key, lookup in (
        (CHUNK_COLLECTION, "note_id", note_lookup),
        (ENTITY_COLLECTION, "entity_id", ent_lookup),
    ):
        fallback = None if col == CHUNK_COLLECTION else 3  # 历史孤儿向量归"历史数据默认空间"
        fixed, skipped = _backfill(store, col, link_key, lookup, fallback_ws=fallback)
        print(f"  {col}: 回填 {fixed} 条，跳过(无归属) {skipped} 条")

    print("完成：历史 Chroma 记录已按归属补齐 workspace_id。")


if __name__ == "__main__":
    run()