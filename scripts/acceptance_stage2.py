"""阶段二「文本解析管道 + 选择性写入知识库」自动化验收脚本。

前置：后端已启动(127.0.0.1:8000)、MySQL 已就绪、raw 目录可写。
运行：python scripts/acceptance_stage2.py
覆盖：解析预览 / 单文件写入 / 重复写入跳过 / 文件夹递归写入(部分跳过)
      / 笔记编辑标记联动(来源 import_status 置0) / 重写更新原笔记(同 note_id)
      / 级联清理(删除笔记后向量块清除、来源标记置0、孤立实体清理)。
逐项断言，末尾汇总；任一项失败退出码非 0。测试数据以 acc2_ 前缀，结束后清理。
"""
from __future__ import annotations

import json
import sys

import httpx

BASE_IMPORT = "http://localhost:8000/api/v1/import-files"
BASE_KB = "http://localhost:8000/api/v1/kb"
PASS = 0
FAIL = 0
RESULTS: list[tuple[str, bool, str]] = []
PREFIX = "acc2_"


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    RESULTS.append((name, ok, detail))
    if ok:
        PASS += 1
    else:
        FAIL += 1


def tree_map(node) -> dict:
    out = {}
    for child in node.get("children", []):
        p = child["path"]
        out[p] = {"is_dir": child["is_dir"], "import_status": child["import_status"]}
        out.update(tree_map(child))
    return out


def main() -> int:
    with httpx.Client(timeout=30) as c:
        # 0. 健康
        h = httpx.get("http://localhost:8000/healthz", timeout=5)
        check("健康检查 GET /healthz", h.status_code == 200 and h.json()["status"] == "ok", h.text)

        # 1. 建测试目录与上传
        r = c.post(f"{BASE_IMPORT}/folders", json={"target_dir": "", "name": PREFIX})
        check("新建测试根目录", r.status_code == 200 and r.json()["ok"], r.text)
        files = [
            (f"{PREFIX}/a.md", "# Alpha\n\n第一段。".encode("utf-8"), "text/markdown"),
            (f"{PREFIX}/b.txt", b"plain beta", "text/plain"),
            (f"{PREFIX}/sub/c.md", "# Gamma\n\n第三段。".encode("utf-8"), "text/markdown"),
        ]
        r = c.post(
            f"{BASE_IMPORT}/upload",
            files=[("files", (n, b, t)) for n, b, t in files],
            data={"paths": json.dumps([f for f, _, _ in files]), "target_dir": ""},
        )
        ok = r.status_code == 200 and r.json()["ok"] and len(r.json()["data"]["saved"]) == 3
        check("上传 3 个测试文件", ok, r.text)

        # 2. 解析预览
        r = c.get(f"{BASE_KB}/preview", params={"rel_path": f"{PREFIX}/a.md"})
        body = r.json()
        check("解析预览 a.md -> Markdown", r.status_code == 200 and "# Alpha" in body.get("content_md", ""), r.text)

        # 3. 单文件写入
        r = c.post(f"{BASE_KB}/write", json={"paths": [f"{PREFIX}/a.md"]})
        b = r.json()
        it = b["results"][0]
        note_id_a = it.get("note_id")
        check("写入单文件成功且返回 note_id",
              r.status_code == 200 and b["summary"]["written"] == 1 and it["status"] == "success" and note_id_a,
              r.text)

        # 4. 树标记：a.md 已导入
        r = c.get(f"{BASE_IMPORT}/tree")
        tm = tree_map(r.json())
        check("写入后 import_status=1",
              r.status_code == 200 and tm.get(f"{PREFIX}/a.md", {}).get("import_status") == 1, r.text)

        # 5. 重复写入跳过
        r = c.post(f"{BASE_KB}/write", json={"paths": [f"{PREFIX}/a.md"]})
        b = r.json()
        check("重复写入自动跳过", r.status_code == 200 and b["summary"] == {"written": 0, "skipped": 1, "failed": 0},
              r.text)

        # 6. 文件夹递归写入（b.txt 未导入，c.md 未导入）→ 全成功
        r = c.post(f"{BASE_KB}/write", json={"paths": [f"{PREFIX}/sub"]})
        b = r.json()
        it = b["results"][0]
        check("文件夹递归写入 c.md",
              r.status_code == 200 and it["type"] == "dir" and b["summary"]["written"] == 1, r.text)

        # 7. 部分跳过：先写 b.txt 再整体写文件夹(含已导入 a/b/sub) → 跳过 3 新增 0
        r = c.post(f"{BASE_KB}/write", json={"paths": [f"{PREFIX}/b.txt"]})
        b = r.json()
        check("写入 b.txt", r.status_code == 200 and b["summary"]["written"] == 1, r.text)
        r = c.post(f"{BASE_KB}/write", json={"paths": [f"{PREFIX}"]})
        b = r.json()
        check("文件夹整体重写 -> 全部跳过",
              r.status_code == 200 and b["summary"] == {"written": 0, "skipped": 3, "failed": 0}, r.text)

        # 8. 笔记列表
        r = c.get(f"{BASE_KB}/notes")
        notes = r.json()["notes"]
        titles = {n["title"] for n in notes}
        check("笔记列表含 3 条", r.status_code == 200 and len(notes) >= 3 and {"Alpha", "Gamma"} <= titles,
              r.text)

        # 9. 笔记编辑 -> 来源标记置 0
        r = c.put(f"{BASE_KB}/notes/{note_id_a}", json={"content_md": "# Alpha 改\n\n已修改。\n"})
        b = r.json()
        check("保存笔记编辑并重置来源标记", r.status_code == 200 and b["import_status_reset"] is True, r.text)
        r = c.get(f"{BASE_IMPORT}/tree")
        tm = tree_map(r.json())
        check("编辑后来源 import_status=0",
              r.status_code == 200 and tm.get(f"{PREFIX}/a.md", {}).get("import_status") == 0, r.text)

        # 10. 重写原笔记 -> 更新而非新建（note_id 不变，内容从源文件重新同步）
        r = c.post(f"{BASE_KB}/write", json={"paths": [f"{PREFIX}/a.md"]})
        b = r.json()
        it = b["results"][0]
        r2 = c.get(f"{BASE_KB}/notes/{note_id_a}")
        check("重写更新原笔记(同 id, 内容同步自源文件)",
              r.status_code == 200 and it["status"] == "success" and it["note_id"] == note_id_a
              and r2.status_code == 200 and "# Alpha" in r2.json()["content_md"], r.text)

        # 11. 保存导入区文本文件编辑 -> 标记置 0
        r = c.put(f"{BASE_KB}/file-content", json={"rel_path": f"{PREFIX}/b.txt", "content": "beta edited"})
        check("保存导入区文本编辑", r.status_code == 200 and r.json()["import_status"] == 0, r.text)

        # 12. 级联清理：删除笔记 -> 向量块清除 + 来源标记置 0 + 孤立实体清理
        r = c.get(f"{BASE_KB}/notes/{note_id_a}")
        check("删除前笔记存在", r.status_code == 200, r.text)
        r = c.delete(f"{BASE_KB}/notes/{note_id_a}")
        check("删除笔记成功", r.status_code == 200 and r.json()["deleted"] is True, r.text)
        r = c.get(f"{BASE_KB}/notes/{note_id_a}")
        check("删除后笔记不存在(404)", r.status_code == 404, r.text)
        r = c.get(f"{BASE_IMPORT}/tree")
        tm = tree_map(r.json())
        check("删除笔记后来源标记置 0",
              r.status_code == 200 and tm.get(f"{PREFIX}/a.md", {}).get("import_status") == 0, r.text)

        # 13. 清理测试数据：删除测试根目录（导入区文件/记录一并清理）
        r = c.delete(f"{BASE_IMPORT}/{PREFIX}")
        check("清理测试目录", r.status_code == 200 and r.json()["ok"], r.text)
        # 清理本次生成的知识库笔记（按来源路径前缀识别）
        for n in c.get(f"{BASE_KB}/notes").json()["notes"]:
            if (n.get("origin_rel_path") or "").startswith(PREFIX):
                c.delete(f"{BASE_KB}/notes/{n['id']}")
        # 删除目录后磁盘树不再有 PREFIX
        r = c.get(f"{BASE_IMPORT}/tree")
        tm = tree_map(r.json())
        leftover = [p for p in tm if p.startswith(PREFIX)]
        check("清理后无残留测试节点", r.status_code == 200 and not leftover,
              f"残留: {leftover}" if leftover else "")

    print("\n======== 阶段二验收汇总 ========")
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail[:140]}" if not ok else ""))
    print("================================")
    print(f"通过 {PASS} / 失败 {FAIL} / 合计 {PASS + FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
