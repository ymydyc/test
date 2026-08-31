"""阶段八批次5 端到端验收（对拍前端双账号流程，8.7）。

模拟前端工作流，覆盖：
1) Alice 登录→建组→生成邀请码；
2) Bob 登录→凭码加入；Alice 与 Bob 各自的工作区列表都出现该组；
3) Alice 切到组（X-Workspace-Id）上传并写库；Bob 切到组可见共享笔记；
4) Bob 切回个人工作区 → 看不到组内笔记（隔离）；
5) 权限约束（前端面板同款操作）：非 owner 移除成员 403 / 解散 403；
6) owner 转让所有权后，新 owner 解散（清理）。
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


def uid(token: str) -> int:
    return requests.get(f"{BASE}/auth/me", headers=h(token), timeout=15).json()["id"]


def h(token: str, ws: int | None = None) -> dict:
    hd = {"Authorization": f"Bearer {token}"}
    if ws is not None:
        hd["X-Workspace-Id"] = str(ws)
    return hd


def main() -> None:
    ta = login("test_alice")
    tb = login("test_bob")
    alice_uid = uid(ta)
    bob_uid = uid(tb)
    suffix = "".join(random.choices(string.ascii_lowercase, k=4))
    fname = f"r8e2e-{suffix}.md"

    # 1) 建组 + 邀请 + 加入
    r = requests.post(f"{BASE}/workspaces", headers=h(ta),
                      json={"name": f"端到端组-{suffix}", "description": "e2e"}, timeout=15)
    gid = r.json()["id"]
    print(f"组 id={gid}")

    invite = requests.get(f"{BASE}/workspaces/{gid}/invite", headers=h(ta), timeout=15).json()["code"]
    r = requests.post(f"{BASE}/workspaces/join", headers=h(tb), json={"code": invite}, timeout=15)
    check("Bob 凭码加入组", r.status_code == 200, r.text)

    la = requests.get(f"{BASE}/workspaces", headers=h(ta), timeout=15).json()["workspaces"]
    lb = requests.get(f"{BASE}/workspaces", headers=h(tb), timeout=15).json()["workspaces"]
    check("Alice 列表含新组", any(w["id"] == gid for w in la), str(la))
    check("Bob 列表含新组", any(w["id"] == gid for w in lb), str(lb))
    check("Bob 组角色为 member", next(w["role"] for w in lb if w["id"] == gid) == "member", str(lb))

    # 2) Alice 切到组 → 上传 + 写库
    up = requests.post(f"{BASE}/import-files/upload", headers=h(ta, gid),
                       files={"files": ("f", fname.encode(), "text/markdown")},
                       data={"paths": f'["{fname}"]'}, timeout=15)
    check("Alice 组内上传", up.status_code == 200, up.text)
    wr = requests.post(f"{BASE}/kb/write", headers=h(ta, gid),
                       json={"paths": [fname]}, timeout=15)
    check("Alice 组内写库", wr.status_code == 200, wr.text)

    # 3) Bob 切到组 → 共享可见；Bob 切回个人 → 隔离
    bnotes = requests.get(f"{BASE}/kb/notes", headers=h(tb, gid), timeout=15).json()["notes"]
    check("Bob 组内可见 Alice 笔记",
          any(fname in (n.get("note_path") or "") for n in bnotes), str(bnotes))
    bob_home = next(w["id"] for w in lb if w["type"] == "personal")
    ball = requests.get(f"{BASE}/kb/notes", headers=h(tb, bob_home), timeout=15).json()["notes"]
    check("Bob 个人空间隔离（无组笔记）",
          all(fname not in (n.get("note_path") or "") for n in ball), str(ball))

    # 4) 权限约束（前端面板同款）
    r = requests.post(f"{BASE}/workspaces/{gid}/members/{alice_uid}/remove",
                      headers=h(tb), timeout=15)
    check("非 owner(Bob) 移除 Alice → 403", r.status_code == 403, f"status={r.status_code}")
    r = requests.delete(f"{BASE}/workspaces/{gid}", headers=h(tb), timeout=15)
    check("非 owner 解散 → 403", r.status_code == 403, f"status={r.status_code}")

    # 5) 转让所有权后新 owner 解散（清理）
    r = requests.post(f"{BASE}/workspaces/{gid}/transfer", headers=h(ta),
                      json={"new_owner_id": bob_uid}, timeout=15)
    check("Alice 转让给 Bob", r.status_code == 200, r.text)
    r = requests.delete(f"{BASE}/workspaces/{gid}", headers=h(tb), timeout=15)
    check("新 owner(Bob) 解散组", r.status_code == 200, r.text)
    r = requests.get(f"{BASE}/workspaces/{gid}", headers=h(tb), timeout=15)
    check("解散后详情 404", r.status_code == 404, f"status={r.status_code}")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()