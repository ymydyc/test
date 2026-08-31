"""阶段七 7.5 向量隔离验收（Chroma 数据层）。

写入需 DASHSCOPE embedding，故在数据层直接验证隔离如何被强制：
1) `chunks`/`entities` 集合全部记录元数据含 workspace_id（无 NULL）；
2) 按 workspace_id 单键过滤查询仅返回本区记录（不漏其他区）；
3) 多条件 `{"workspace_id":N, "note_id":M}` 经 _where_and→$and 过滤生效，返回均属本区。
"""
from __future__ import annotations

from app.vectorstore.chroma_store import ChromaVectorStore, CHUNK_COLLECTION, ENTITY_COLLECTION

passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} — {detail}")


def run() -> None:
    store = ChromaVectorStore()
    dim = store.embedding_dim
    print(f"embedding_dim={dim}")

    for col in (CHUNK_COLLECTION, ENTITY_COLLECTION):
        c = store._collection(col)
        total = c.count()
        print(f"--- {col}: {total} 条 ---")
        ws_missing_multi = 0
        by_ws: dict[int, int] = {}
        id_by_ws: dict[int, str] = {}
        nid_by_ws: dict[int, int] = {}
        page = 0
        offset = 0
        batch = 500
        while True:
            got = c.get(limit=batch, offset=offset)
            ids = got.get("ids") or []
            if not ids:
                break
            metas = got.get("metadatas") or [{}] * len(ids)
            for i, m in enumerate(metas):
                m = m or {}
                ws = m.get("workspace_id")
                if ws is None:
                    ws_missing_multi += 1
                    continue
                ws = int(ws)
                by_ws[ws] = by_ws.get(ws, 0) + 1
                if ws not in id_by_ws:
                    id_by_ws[ws] = ids[i]
                if ws not in nid_by_ws and m.get("note_id") is not None:
                    nid_by_ws[ws] = int(m["note_id"])
            offset += batch
            if offset >= total:
                break
        if total > 0:
            check("全部记录带 workspace_id（无 NULL）", ws_missing_multi == 0,
                  f"缺 {ws_missing_multi}")
            check("记录按工作区分摊", len(by_ws) >= 1, str(by_ws))

        # 单键过滤查询：零向量 + where → 返回必须都属该区
        if by_ws:
            z = [0.0] * dim
            ws = next(iter(by_ws))
            rows = store.query(col, [z], top_k=min(10, total), where={"workspace_id": ws})
            leak = [r for r in rows if int(r["metadata"].get("workspace_id")) != ws]
            check(f"[{col}] 单键 `workspace_id={ws}` 过滤无跨区泄漏",
                  len(rows) > 0 and not leak,
                  f"rows={len(rows)} leak={len(leak)}")

            # 多条件 $and 过滤：workspace_id + note_id 双键
            nid = nid_by_ws.get(ws)
            if nid is not None:
                rows2 = store.query(col, [z], top_k=min(10, total),
                                    where={"workspace_id": ws, "note_id": nid})
                ok = all(
                    int(r["metadata"].get("workspace_id")) == ws and int(r["metadata"].get("note_id")) == nid
                    for r in rows2
                )
                check(f"[{col}] 多条件 `$and`(ws={ws}, note_id={nid}) 过滤生效",
                      len(rows2) > 0 and ok, f"rows={len(rows2)}")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    run()