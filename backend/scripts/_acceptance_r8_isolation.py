"""阶段八批次4 HTTP 验收：组内共享 + 权限约束（8.5/8.6）。

覆盖：
1) 个人/组活动工作区切换：组内成员带 `X-Workspace-Id` 才可访问该组数据；
2) 组共享：Alice 往组内写库笔记，Bob（已加入）同组可见且能读写；
3) 非成员（carol）带组头访问 → 403（跨组隔离/无权限）；
4) 被移除成员（creator 移除 Bob）后失权 → 403；
5) 成员退出组（carol 加入后退出）→ 退后访问组 → 403；
6) 创建者不能退出/移除自己。
"""
from __future__ import annotations

import random
import string

import requests

BASE = "http://127.0.0.1:8000/api/v1"
PASSWORD = "test123456"

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


def login(username: str) -> str:
    r = requests.post(f"{BASE}/auth/login",
                      json={"username": username, "password": PASSWORD}, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def hws(token: str, ws_id: int) -> dict:
    return {"Authorization": f"Bearer {token}", "X-Workspace-Id": str(ws_id)}


def main() -> None:
    ta = login("test_alice")
    tb = login("test_bob")

    suffix = "".join(random.choices(string.ascii_lowercase, k=4))
    gname = f"共享组-{suffix}"

    # ---- 建组 + Bob 加入 ----
    r = requests.post(f"{BASE}/workspaces", headers=h(ta),
                      json={"name": gname, "description": "批次4共享"}, timeout=15)
    gid = r.json()["id"]
    inv = requests.get(f"{BASE}/workspaces/{gid}/invite", headers=h(ta), timeout=15).json()["code"]
    r = requests.post(f"{BASE}/workspaces/join", headers=h(tb), json={"code": inv}, timeout=15)
    check("Bob 加入组成功", r.status_code == 200, r.text)

    # carol 第三账号（非本组成员）
    cname = f"carol_{suffix}"
    requests.post(f"{BASE}/auth/register",
                  json={"username": cname, "password": PASSWORD}, timeout=15)
    tc = login(cname)

    # ---- 1) 组共享：Alice 在组内写库笔记，Bob 同组可见 ----
    print(f"=== 组共享（Alice 组内写库 → Bob 组内可见）===")
    rel = f"r8s-{suffix}.md"
    up = requests.post(
        f"{BASE}/import-files/upload", headers=hws(ta, gid),
        files={"files": ("f", rel.encode(), "text/markdown")},
        data={"paths": f'["{rel}"]'}, timeout=15)
    check("Alice 组内上传", up.status_code == 200, up.text)
    wr = requests.post(f"{BASE}/kb/write", headers=hws(ta, gid),
                       json={"paths": [rel]}, timeout=15)
    check("Alice 组内写库", wr.status_code == 200, wr.text)

    # Alice 组内笔记列表（应能取到 note）
    alist = requests.get(f"{BASE}/kb/notes", headers=hws(ta, gid), timeout=15).json()["notes"]
    anote = next((n for n in alist if rel in (n.get("note_path") or "")), None)
    check("Alice 组内可见自己笔记", anote is not None, str(alist))

    # Bob 组内列表（组共享：能看到 Alice 建的笔记）
    blist = requests.get(f"{BASE}/kb/notes", headers=hws(tb, gid), timeout=15).json()["notes"]
    bnote = next((n for n in blist if rel in (n.get("note_path") or "")), None)
    check("Bob 组内共享可见 Alice 笔记", bnote is not None, str(blist))
    if bnote and anote:
        nid = bnote["id"]
        g = requests.get(f"{BASE}/kb/notes/{nid}", headers=hws(tb, gid), timeout=15)
        check("Bob 组内可读笔记详情", g.status_code == 200, g.text)

    # Bob 组内是否误带个人空间：个人列表不应含组内笔记
    bpers = requests.get(f"{BASE}/kb/notes", headers=h(tb), timeout=15).json()["notes"]
    check("Bob 个人空间不含组内笔记", all(rel not in (n.get("note_path") or "") for n in bpers), str(bpers))

    # ---- 2) 非成员访问组 → 403（跨组隔离）----
    print(f"=== 权限约束：非成员 / 移除 / 退出 ===")
    r = requests.get(f"{BASE}/kb/notes", headers=hws(tc, gid), timeout=15)
    check("非成员(未加入)组头访问 → 403", r.status_code == 403, f"status={r.status_code}")

    # ---- 3) 无组头默认个人空间不可见（Bob 个人空间列表不含组笔记已验）----

    # ---- 4) creator 移除 Bob → Bob 组内失权 ----
    # Bob 用户 id
    me = requests.get(f"{BASE}/auth/me", headers=h(tb), timeout=15).json()
    bob_uid = me["id"]
    r = requests.post(f"{BASE}/workspaces/{gid}/members/{bob_uid}/remove",
                      headers=h(ta), timeout=15)
    check("creator 移除 Bob → 200", r.status_code == 200, r.text)
    r = requests.get(f"{BASE}/kb/notes", headers=hws(tb, gid), timeout=15)
    check("被移除后 Bob 组内访问 → 403", r.status_code == 403, f"status={r.status_code}")
    # 非 creator 不能移除
    carol_me = requests.get(f"{BASE}/auth/me", headers=h(tc), timeout=15).json()
    r = requests.post(f"{BASE}/workspaces/{gid}/members/{carol_me['id']}/remove",
                      headers=h(tc), timeout=15)
    check("非 creator 尝试移除 → 403", r.status_code == 403, f"status={r.status_code}")
    # creator 不能移除自己
    alice_me = requests.get(f"{BASE}/auth/me", headers=h(ta), timeout=15).json()
    r = requests.post(f"{BASE}/workspaces/{gid}/members/{alice_me['id']}/remove",
                      headers=h(ta), timeout=15)
    check(" creator 不能移除自己 → 403", r.status_code == 403, f"status={r.status_code}")

    # ---- 5) carol 加入后退出 -> 退后失权；creator 不能退出 ----
    inv2 = requests.get(f"{BASE}/workspaces/{gid}/invite", headers=h(ta), timeout=15).json()["code"]
    requests.post(f"{BASE}/workspaces/join", headers=h(tc), json={"code": inv2}, timeout=15)
    r = requests.get(f"{BASE}/kb/notes", headers=hws(tc, gid), timeout=15)
    check("Carol 加入后组内可访问(200)", r.status_code == 200, f"status={r.status_code}")
    r = requests.post(f"{BASE}/workspaces/{gid}/leave", headers=h(tc), timeout=15)
    check("Carol 退出组 → 200", r.status_code == 200, r.text)
    r = requests.get(f"{BASE}/kb/notes", headers=hws(tc, gid), timeout=15)
    check("Carol 退组后组内访问 → 403", r.status_code == 403, f"status={r.status_code}")
    r = requests.post(f"{BASE}/workspaces/{gid}/leave", headers=h(ta), timeout=15)
    check("creator 不能退出自己的组 → 403", r.status_code == 403, f"status={r.status_code}")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()