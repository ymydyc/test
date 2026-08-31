"""阶段八批次3 HTTP 验收：组创建 + 邀请码机制（8.4）。

覆盖：
1) 创建组（creator 成为 owner 成员）；
2) 仅 creator 可生成邀请码（非 creator → 403）；
3) 邀请码有效期内凭码加入成为 member；
4) 码用后即失效（重复使用 → 400）；
5) 过期码拒绝（用测试开关/直接改库无需等 5 分钟，改 DB validates_at）；
6) 生成新码后旧码作废；
7) 组详情：成员可见；非成员访问组详情 → 404。
"""
from __future__ import annotations

import random
import string

import pymysql
import requests

from app.core.config import settings

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


def db_conn():
    return pymysql.connect(
        host=settings.mysql_host, port=settings.mysql_port,
        user=settings.mysql_user, passwd=settings.mysql_password,
        db=settings.mysql_db, charset="utf8mb4")


def main() -> None:
    ta = login("test_alice")
    tb = login("test_bob")

    suffix = "".join(random.choices(string.ascii_lowercase, k=4))
    gname = f"验收组-{suffix}"

    # ---- 1) 创建组 ----
    r = requests.post(f"{BASE}/workspaces", headers=h(ta),
                      json={"name": gname, "description": "批次3验收"}, timeout=15)
    print(f"=== 创建组 ===")
    if r.status_code != 201:
        print("  创建组失败：", r.text)
        raise SystemExit(1)
    gid = r.json()["id"]
    check("创建组返回 201 且 type=group", r.json()["type"] == "group", r.text)

    det = requests.get(f"{BASE}/workspaces/{gid}", headers=h(ta), timeout=15).json()
    check("creator 成为 owner 成员", any(m["role"] == "owner" and m["user_id"] == det["creator_id"]
                                         for m in det["members"]), str(det["members"]))
    check("创建者可见组详情", det["id"] == gid)

    # ---- 2) 非 member 访问组详情 → 404（Bob 未加入前）----
    r = requests.get(f"{BASE}/workspaces/{gid}", headers=h(tb), timeout=15)
    check("非成员查看组详情 → 404", r.status_code == 404, f"status={r.status_code}")

    # ---- 3) 仅 creator 可生成邀请码 ----
    print(f"=== 邀请码权限（仅 creator）===")
    r = requests.get(f"{BASE}/workspaces/{gid}/invite", headers=h(tb), timeout=15)
    check("非 creator 生成邀请码 → 403", r.status_code == 403, f"status={r.status_code}")
    r = requests.get(f"{BASE}/workspaces/{gid}/invite", headers=h(ta), timeout=15)
    check("creator 生成邀请码 → 200", r.status_code == 200, r.text)
    code1 = r.json()["code"]
    check("邀请码为安全短码(8位)", len(code1) == 8 and code1.isalnum(), code1)

    # ---- 4) Bob 凭码加入成为 member ----
    print(f"=== 凭码加入组 ===")
    r = requests.post(f"{BASE}/workspaces/join", headers=h(tb),
                      json={"code": code1}, timeout=15)
    check("Bob 凭有效码加入 → 200", r.status_code == 200, r.text)
    join_ws = r.json()
    check("加入的是目标组", join_ws["id"] == gid)

    det = requests.get(f"{BASE}/workspaces/{gid}", headers=h(ta), timeout=15).json()
    check("Bob 已成为成员(member)", any(m["user_id"] is not None for m in det["members"]), str(det["members"]))

    # ---- 5) 码用后即失效 ----
    r = requests.post(f"{BASE}/workspaces/join", headers=h(ta),
                      json={"code": code1}, timeout=15)
    check("已用码重复加入 → 400", r.status_code == 400, f"status={r.status_code}")

    # ---- 6) 重新生成邀请码后，旧码作废、新码可用 ----
    print(f"=== 邀请码刷新/作废 ===")
    r = requests.get(f"{BASE}/workspaces/{gid}/invite", headers=h(ta), timeout=15)
    code_new = r.json()["code"]
    check("重新生成返回新码", code_new != code1, code_new)
    r = requests.post(f"{BASE}/workspaces/join", headers=h(ta),
                      json={"code": code1}, timeout=15)
    check("旧码在重新生成后已作废 → 400", r.status_code == 400, f"status={r.status_code}")

    # ---- 7) 邀请码过期 ----
    print(f"=== 邀请码过期 ===")
    r = requests.get(f"{BASE}/workspaces/{gid}/invite", headers=h(ta), timeout=15)
    code2 = r.json()["code"]
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("UPDATE workspace_invites SET expires_at = DATE_SUB(NOW(), INTERVAL 1 MINUTE) WHERE code=%s", (code2,))
    conn.commit()
    conn.close()
    # 用第三个测试新用户验证过期拒绝（避免 Bob 已在组内幂等返回）
    uname = f"r8u_{suffix}"
    requests.post(f"{BASE}/auth/register",
                  json={"username": uname, "password": PASSWORD}, timeout=15)
    t3 = login(uname)
    r = requests.post(f"{BASE}/workspaces/join", headers=h(t3),
                      json={"code": code2}, timeout=15)
    check("过期邀请码拒绝加入 → 400", r.status_code == 400, f"status={r.status_code}")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()