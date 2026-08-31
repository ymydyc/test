import pymysql
from app.vectorstore.chroma_store import ChromaVectorStore, CHUNK_COLLECTION, ENTITY_COLLECTION

store = ChromaVectorStore()

def sample(col, n=8):
    c = store._collection(col)
    got = c.get(limit=n)
    for i, m in enumerate(got.get("metadatas") or []):
        ids = got["ids"]
        print(f"  {col}[{ids[i]}] meta={m}")

print("== chunks 无 ws 样本 ==")
got = store._collection(CHUNK_COLLECTION).get(limit=10)
print("  metas keys:", sorted({k for m in (got.get('metadatas') or [{}]) for k in (m or {}).keys()}))
sample(CHUNK_COLLECTION)

print("== entities 无 ws 样本 ==")
got = store._collection(ENTITY_COLLECTION).get(limit=10)
print("  metas keys:", sorted({k for m in (got.get('metadatas') or [{}]) for k in (m or {}).keys()}))
sample(ENTITY_COLLECTION)