"""Final stage-1 smoke: upload nested structure, verify tree, delete, cleanup."""
import json
import tempfile

import httpx

BASE = "http://localhost:8000/api/v1/import-files"
with httpx.Client(base_url=BASE, timeout=30) as c:
    # prepare 2 files
    tmp1 = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
    tmp1.write("hello stage1".encode()); tmp1.close()
    tmp2 = tempfile.NamedTemporaryFile(suffix=".md", delete=False)
    tmp2.write("# 标题".encode()); tmp2.close()

    # upload preserving structure
    with open(tmp1.name, "rb") as f1, open(tmp2.name, "rb") as f2:
        r = c.post("/upload",
                   files=[("files", ("docs/a.txt", f1, "text/plain")),
                          ("files", ("docs/sub/b.md", f2, "text/markdown"))],
                   data={"paths": json.dumps(["docs/a.txt", "docs/sub/b.md"]), "target_dir": ""})
    print("upload:", r.status_code, r.json()["ok"])

    r = c.get("/tree")
    root = r.json()["children"][0]
    print("tree root child:", root["name"], "| file under docs/sub:", root["children"][0]["children"][0]["name"])

    # delete recursively
    r = c.delete("/docs")
    print("delete:", r.status_code, r.json()["ok"])

    r = c.get("/tree")
    print("tree after delete, children count:", len(r.json()["children"]))