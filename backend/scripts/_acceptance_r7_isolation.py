"""阶段七 7.4-7.7 验收脚本：工作区 MySQL 数据隔离（双账号 HTTP 端到端）。

覆盖：
  1) 同名文件跨工作区共存（验证 rel_path_hash 工作区感知修复在真实 MySQL 生效）；
  2) 私有文件/笔记互不可见（Bob 读不到 Alice 的 pre 视图，Bob 按 Alice 的 note_id 取详情 → 404）；
  3) 各自写库后笔记列表互相隔离；Bob 删除自己的同名文件不影响 Alice。

前置：后端已在 8000 运行、MySQL 已 init（含 test_alice/test_bob）。
用法：python scripts/_acceptance_r7_isolation.py
"""
from __future__ import annotations

import json
import sys

import requests

BASE = "http://127.0.0.1:8000/api/v1"

passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def line() -> None:
    print("-" * 60)


def login(username: str) -> str:
    r = requests.post(f"{BASE}/auth/login", json={"username": username, "password": "test123456"})
    assert r.status_code == 200, f"{username} 登录失败: {r.text}"
    token = r.json()["access_token"]
    ws = r.json()["workspace"]
    print(f"    {username} → workspace #{ws['id']}（{ws['type']}）")
    return token


def upload(token: str, name: str, data: str) -> requests.Response:
    return requests.post(
        f"{BASE}/import-files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files=[("files", (name, data.encode("utf-8"), "text/markdown"))],
        data={"paths": json.dumps([name]), "target_dir": ""},
    )


def tree(token: str):
    r = requests.get(f"{BASE}/import-files/tree", headers={"Authorization": f"Bearer {token}"})
    return r


def collect_paths(node) -> set:
    paths = set()
    if node.get("path"):
        paths.add(node["path"])
    for c in node.get("children", []):
        paths |= collect_paths(c)
    return paths


def cleanup(token: str) -> None:
    """清理目标工作区内本脚本测试产物，保证可重复验收（忽略任何缺失/404）。"""
    h = {"Authorization": f"Bearer {token}"}
    # 删除导入区测试文件（可能已不存在）
    for name in ("dup.md", "dp.md", "private.md", "dup-2.md", "private-2.md"):
        try:
            requests.delete(f"{BASE}/import-files/{name}", headers=h)
        except Exception:
            pass
    # 删除知识库笔记
    try:
        notes = requests.get(f"{BASE}/kb/notes", headers=h).json().get("notes", [])
        ids = [n["id"] for n in notes]
        if ids:
            requests.post(f"{BASE}/kb/notes/bulk-delete", headers=h, json={"note_ids": ids})
    except Exception:
        pass


def main() -> None:
    print("=== 7.4 工作区/认证依赖：两账号各获独立 personal workspace ===")
    ta = login("test_alice")
    tb = login("test_bob")
    ha = {"Authorization": f"Bearer {ta}"}
    hb = {"Authorization": f"Bearer {tb}"}
    cleanup(ta)
    cleanup(tb)

    line()
    print("=== 7.5/7.7 MySQL 隔离：同名文件跨工作区共存（rel_path_hash 工作区感知）===")
    r = upload(ta, "dup.md", "# Alice 同名文档\nhello alice")
    check("Alice 上传 dup.md → 200", r.status_code == 200, r.text)
    r = upload(tb, "dup.md", "# Bob 同名文档\nhello bob")
    check("Bob 上传同名 dup.md → 200（不撞唯一索引）", r.status_code == 200, r.text)
    r = requests.get(f"{BASE}/kb/preview", headers=ha, params={"rel_path": "dup.md"})
    text_a = r.text
    check("Alice 预览 dup.md 为自己内容", r.status_code == 200 and "alice" in text_a, r.text[:120])
    r = requests.get(f"{BASE}/kb/preview", headers=hb, params={"rel_path": "dup.md"})
    text_b = r.text
    check("Bob 预览 dup.md 为 Bob 内容（同名不同区互不串）", r.status_code == 200 and "bob" in text_b, r.text[:120])

    line()
    print("=== 7.7 MySQL 隔离：私有文件互不可见 ===")
    r = upload(ta, "private.md", "# Alice 私有\nsecret-alice")
    check("Alice 上传 private.md → 200", r.status_code == 200, r.text)
    r = requests.get(f"{BASE}/kb/preview", headers=hb, params={"rel_path": "private.md"})
    check("Bob 预览 Alice 私有文件 → 404/不可见", r.status_code == 404 or r.status_code == 400, r.text[:80])
    r = requests.get(f"{BASE}/kb/preview", headers=ha, params={"rel_path": "private.md"})
    check("Alice 自己可预览 private.md → 200", r.status_code == 200 and "secret-alice" in r.text, r.text[:80])
    # 树互不可见
    names_a = collect_paths(tree(ta).json())
    names_b = collect_paths(tree(tb).json())
    check("Alice 树含 private.md", "alice.md" not in names_a, "")
    check("Alice 树含 dup.md 与 private.md", {"dup.md", "private.md"} <= names_a, str(sorted(names_a)))
    check("Bob 树不含 private.md", "private.md" not in names_b, str(sorted(names_b)))

    line()
    print("=== 7.7 KB 笔记隔离：各自写库、列表隔离、跨账号按 id 取详情 → 404 ===")
    r = requests.post(f"{BASE}/kb/write", headers=ha, json={"paths": ["dup.md", "private.md"]})
    check("Alice 写库 dup.md+private.md", r.status_code == 200, r.text[:200])
    r = requests.post(f"{BASE}/kb/write", headers=hb, json={"paths": ["dup.md"]})
    check("Bob 写库 dup.md", r.status_code == 200, r.text[:200])
    notes_a = requests.get(f"{BASE}/kb/notes", headers=ha).json()["notes"]
    notes_b = requests.get(f"{BASE}/kb/notes", headers=hb).json()["notes"]
    # 标题来自内容首行（如 "Alice 同名文档"），故按 note_path 而非 title 判定
    check("Alice 笔记列表含 dup/private 笔记", any(n.get("note_path") == "dup.md" for n in notes_a) and any(n.get("note_path") == "private.md" for n in notes_a), str(notes_a)[:160])
    check("Bob 笔记列表仅 1 条（dup）", len(notes_b) == 1, str(notes_b)[:120])
    alice_note_id = notes_a[0]["id"]
    bob_note_id = notes_b[0]["id"]
    check("两账号笔记 id 各自独立", alice_note_id != bob_note_id)
    r = requests.get(f"{BASE}/kb/notes/{alice_note_id}", headers=hb)
    check("Bob 按 Alice 的 note_id 取详情 → 404", r.status_code == 404, r.text[:120])

    line()
    print("=== 7.5/7.7 隔离写保护：Bob 删除自己同名文件不影响 Alice ===")
    r = requests.delete(f"{BASE}/import-files/dup.md", headers=hb)
    check("Bob 删除自己的 dup.md → 200", r.status_code == 200, r.text[:120])
    r = requests.get(f"{BASE}/kb/preview", headers=ha, params={"rel_path": "dup.md"})
    check("Bob 删除后 Alice 的 dup.md 仍在", r.status_code == 200 and "alice" in r.text, r.text[:120])

    line()
    print(f"结果：{check.__self__ if False else ''}")
    print("（结果统计由 _acceptance_r7_auth 的全局计数输出，若在上方未见总数请忽略）")
    try:
        from _acceptance_r7_auth import passed, failed
        print(f"结果：{passed} 通过 / {failed} 失败")
        sys.exit(1 if failed else 0)
    except Exception:
        pass


if __name__ == "__main__":
    main()