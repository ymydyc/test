"""阶段一「项目骨架 + 原始文件导入区」自动化验收脚本。

前置：后端已启动(127.0.0.1:8000)、MySQL 已就绪。
运行：python scripts/acceptance_stage1.py
逐项断言，末尾汇总；任一项失败退出码非 0。
"""
from __future__ import annotations

import json
import sys

import httpx

BASE = "http://localhost:8000/api/v1/import-files"
PASS = 0
FAIL = 0
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    RESULTS.append((name, ok, detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


def tree_map(node) -> dict:
    """树递归转 map: path -> (is_dir, import_status)。"""
    out = {}
    for child in node.get("children", []):
        p = child["path"]
        out[p] = {"is_dir": child["is_dir"], "import_status": child["import_status"]}
        out.update(tree_map(child))
    return out


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=30) as c:
        # 1. 健康
        h = httpx.get("http://localhost:8000/healthz", timeout=5)
        check("健康检查 GET /healthz", h.status_code == 200 and h.json()["status"] == "ok", h.text)

        # 2. 空树
        r = c.get("/tree")
        empty = r.json().get("children", []) == []
        check("初始文件树为空", r.status_code == 200 and empty, r.text)

        # 3. 新建文件夹（根 + 子）
        r = c.post("/folders", json={"target_dir": "", "name": "acc"})
        check("新建根文件夹 acc", r.status_code == 200 and r.json()["ok"], r.text)
        r = c.post("/folders", json={"target_dir": "acc", "name": "sub"})
        check("新建子文件夹 acc/sub", r.status_code == 200 and r.json()["ok"], r.text)

        # 4. 上传（保留结构）
        f1 = ("note.md", "# ACC 文档".encode("utf-8"), "text/markdown")
        f2 = ("data.txt", b"hello from stage1", "text/plain")
        r = c.post(
            "/upload",
            files=[("files", (n, b, t)) for n, b, t in (f1, f2)],
            data={"paths": json.dumps(["acc/note.md", "acc/sub/data.txt"]), "target_dir": ""},
        )
        ok = r.status_code == 200 and r.json()["ok"] and len(r.json()["data"]["saved"]) == 2
        check("上传 2 文件且保存", ok, r.text)

        # 5. 树结构 + 导入标记
        r = c.get("/tree")
        tm = tree_map(r.json())
        need = {
            "acc": (True, 0),
            "acc/sub": (True, 0),
            "acc/note.md": (False, 0),
            "acc/sub/data.txt": (False, 0),
        }
        miss = [p for p, (isdir, st) in need.items() if p not in tm or tm[p]["is_dir"] != isdir]
        unimported = all(tm[p]["import_status"] == 0 for p in need) if not miss else False
        check("树结构与导入标记正确(" + (f"缺失: {miss}" if miss else "全在") + ", 均未导入=" + str(unimported) + ")",
              r.status_code == 200 and not miss and unimported, r.text)

        # 6. 重命名文件
        r = c.put("/rename", json={"rel_path": "acc/note.md", "new_name": "renamed.md"})
        check("重命名文件 acc/note.md->renamed.md", r.status_code == 200 and r.json()["ok"], r.text)

        # 7. 重命名目录（子级路径跟随）
        r = c.put("/rename", json={"rel_path": "acc/sub", "new_name": "sub2"})
        check("重命名目录 acc/sub->acc/sub2", r.status_code == 200 and r.json()["ok"], r.text)

        # 8. 防穿越：新建文件夹越界
        r = c.post("/folders", json={"target_dir": "../../", "name": "evil"})
        check("防穿越 新建文件夹(../) 返回 400", r.status_code == 400, r.text)

        # 9. 防穿越：上传越界路径
        r = c.post("/upload", files=[("files", ("a.txt", b"x", "text/plain"))],
                   data={"paths": json.dumps(["../evil.txt"]), "target_dir": ""})
        check("防穿越 上传(../) 返回 400", r.status_code == 400, r.text)

        # 10. 禁止删除根目录
        r = c.delete("/")
        check("禁止删除根目录 返回 400", r.status_code == 400, r.text)

        # 11. 删除单个文件
        r = c.delete("/acc/renamed.md")
        check("删除文件 acc/renamed.md", r.status_code == 200 and r.json()["ok"], r.text)

        # 12. 删除不存在文件 -> 400
        r = c.delete("/acc/not-exist.md")
        check("删除不存在文件 返回 400", r.status_code == 400, r.text)

        # 13. 删除目录递归
        r = c.delete("/acc")
        check("递归删除目录 /acc", r.status_code == 200 and r.json()["ok"], r.text)

        # 14. 删除后树为空
        r = c.get("/tree")
        after = r.json().get("children", []) == []
        check("删除后文件树为空", r.status_code == 200 and after, r.text)

    # 汇总
    print("\n======== 阶段一验收汇总 ========")
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail[:120]}" if not ok else ""))
    print(f"================================")
    print(f"通过 {PASS} / 失败 {FAIL} / 合计 {PASS + FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())