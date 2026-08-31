"""阶段八批次2b HTTP 验收：组 CRUD（编辑/转让/解散，8.2）。

覆盖：
1) 编辑组信息：仅创建者可改（非 creator → 403）；改名/描述生效；
2) 转让所有权：仅创建者（非 creator → 403）；转让给组内成员成功（creator_id 变更、
   原创建者降为 member、新 owner 可生成邀请码、旧邀请码作废）；转让给非成员 → 404；
3) 解散组：仅创建者（非 creator → 403）；解散后组详情 → 404、成员关系与邀请码清除。
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


def main() -> None:
    ta = login("test_alice")
    tb = login("test_bob")
    suffix = "".join(random.choices(string.ascii_lowercase, k=4))
    gname = f"批次2b组-{suffix}"
    aname = f"批次2b改-{suffix}"

    # ---- 建组 + Bob 加入 ----
    r = requests.post(f"{BASE}/workspaces", headers=h(ta),
                      json={"name": gname, "description": "初版"}, timeout=15)
    gid = r.json()["id"]
    inv = requests.get(f"{BASE}/workspaces/{gid}/invite", headers=h(ta), timeout=15).json()["code"]
    r = requests.post(f"{BASE}/workspaces/join", headers=h(tb), json={"code": inv}, timeout=15)
    check("Bob 加入组成功", r.status_code == 200, r.text)
    bob_uid = requests.get(f"{BASE}/auth/me", headers=h(tb), timeout=15).json()["id"]

    # ---- 1) 编辑组信息 ----
    print("=== 编辑组信息 ===")
    r = requests.put(f"{BASE}/workspaces/{gid}", headers=h(tb),
                     json={"name": "不该改"}, timeout=15)
    check("非 creator 改组名 → 403", r.status_code == 403, f"status={r.status_code} {r.text}")
    r = requests.put(f"{BASE}/workspaces/{gid}", headers=h(ta),
                     json={"name": aname, "description": "已更新"}, timeout=15)
    check("creator 改名 → 200", r.status_code == 200, r.text)
    if r.status_code == 200:
        d = r.json()
        check("改名生效", d["name"] == aname, str(d))
        check("描述更新", d.get("description") == "已更新", str(d))
    r = requests.put(f"{BASE}/workspaces/{gid}", headers=h(ta),
                     json={"name": "   "}, timeout=15)
    check("空组名 → 400", r.status_code == 400, f"status={r.status_code}")

    # ---- 2) 转让所有权 ----
    print("=== 转让所有权 ===")
    alice_uid = requests.get(f"{BASE}/auth/me", headers=h(ta), timeout=15).json()["id"]
    # 非 creator(Bob) 尝试转让给合法成员(Alice) → 应被权限校验拒绝(403)
    r = requests.post(f"{BASE}/workspaces/{gid}/transfer", headers=h(tb),
                      json={"new_owner_id": alice_uid}, timeout=15)
    check("非 creator 转让 → 403", r.status_code == 403, f"status={r.status_code} {r.text}")
    r = requests.post(f"{BASE}/workspaces/{gid}/transfer", headers=h(ta),
                      json={"new_owner_id": 99999999}, timeout=15)
    check("转让给非成员 → 404", r.status_code == 404, f"status={r.status_code} {r.text}")
    # 转让前旧 creator 有邀请码，转让后应作废
    requests.get(f"{BASE}/workspaces/{gid}/invite", headers=h(ta), timeout=15)
    r = requests.post(f"{BASE}/workspaces/{gid}/transfer", headers=h(ta),
                      json={"new_owner_id": bob_uid}, timeout=15)
    check("creator 转让给 Bob → 200", r.status_code == 200, r.text)
    if r.status_code == 200:
        d = r.json()
        check("转让后 creator_id=Bob", d["creator_id"] == bob_uid, str(d))
        roles = {m["user_id"]: m["role"] for m in d["members"]}
        check("Bob 成为 owner", roles.get(bob_uid) == "owner", str(roles))
        alice_uid = requests.get(f"{BASE}/auth/me", headers=h(ta), timeout=15).json()["id"]
        check("原创建者降为 member", roles.get(alice_uid) == "member", str(roles))
    # 新 owner(Bob) 可再生成邀请码；原 creator(Alice) 现在被拒绝
    r = requests.get(f"{BASE}/workspaces/{gid}/invite", headers=h(tb), timeout=15)
    check("新 owner 可生成邀请码 → 200", r.status_code == 200, f"status={r.status_code}")
    r = requests.get(f"{BASE}/workspaces/{gid}/invite", headers=h(ta), timeout=15)
    check("原 creator 再发邀请码 → 403", r.status_code == 403, f"status={r.status_code}")

    # ---- 3) 解散组 ----
    print("=== 解散组 ===")
    r = requests.delete(f"{BASE}/workspaces/{gid}", headers=h(ta), timeout=15)
    check("非 creator 解散 → 403", r.status_code == 403, f"status={r.status_code} {r.text}")
    r = requests.delete(f"{BASE}/workspaces/{gid}", headers=h(tb), timeout=15)
    check("新 owner 解散 → 200", r.status_code == 200, r.text)
    if r.status_code == 200:
        r = requests.get(f"{BASE}/workspaces/{gid}", headers=h(tb), timeout=15)
        check("解散后组详情 → 404", r.status_code == 404, f"status={r.status_code}")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()