"""阶段七 7.1-7.3 验收脚本：用户模型 / 认证与会话 / 账号管理（HTTP 端到端）。

流程：
  - 随机用户名注册 → 校验 TokenResponse(user + workspace 个人空间)
  - 未登录访问 /auth/me → 401
  - 错误密码登录 → 400
  - 正确登录 → 换新 token；/auth/me 返回本人；/auth/me/workspace 返回个人工作区
  - 改密（原密错误→400；正确→后续用新密再登录成功）
  - 更新资料（display_name 生效）
  - 会话列表 → 存在两条（注册自动登录 + 本次登录）；吊销其中一条 → 再用旧 token 访问 /me 应…（吊销的是本次登录的 token）
  - 登出 → 旧 token 再访问 /me 应 401
  - 种子账号 test_alice / test_bob 默认密码可登录且工作区互异
用法：python scripts/_acceptance_r7_auth.py
"""
from __future__ import annotations

import sys
import uuid

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


def main() -> None:
    uname = f"r7_{uuid.uuid4().hex[:8]}"
    pwd = "pass1234"

    print(f"=== 7.1 用户模型 / 7.2 注册 ===")
    r = requests.post(f"{BASE}/auth/register", json={
        "username": uname, "password": pwd, "display_name": "验收用户"})
    check("注册成功返回 200", r.status_code == 200, r.text)
    data = r.json()
    check("返回 access_token", bool(data.get("access_token")))
    check("返回 user 信息", data.get("user", {}).get("username") == uname)
    check("注册即分配个人工作区", data.get("workspace", {}).get("type") == "personal")
    reg_token = data["access_token"]

    line()
    print("=== 7.2 认证与会话：未登录/坏凭证 ===")
    r = requests.get(f"{BASE}/auth/me")
    check("未登录访问 /me → 401", r.status_code == 401, r.text)
    r = requests.post(f"{BASE}/auth/login", json={"username": uname, "password": "wrongpass"})
    check("错误密码登录 → 400", r.status_code == 400, r.text)
    r = requests.get(f"{BASE}/auth/me", headers={"Authorization": "Bearer bad.token"})
    check("伪造 token 访问 /me → 401", r.status_code == 401, r.text)

    line()
    print("=== 7.2 登录 / 本人 / 工作区 ===")
    r = requests.post(f"{BASE}/auth/login", json={"username": uname, "password": pwd})
    check("登录成功返回 200", r.status_code == 200, r.text)
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{BASE}/auth/me", headers=h)
    check("/me 返回当前账号", r.status_code == 200 and r.json()["username"] == uname, r.text)
    r = requests.get(f"{BASE}/auth/me/workspace", headers=h)
    check("/me/workspace 返回个人工作区", r.status_code == 200 and r.json()["type"] == "personal", r.text)

    line()
    print("=== 7.3 账号管理：改密 ===")
    r = requests.post(f"{BASE}/auth/password", headers=h,
                      json={"old_password": "bad", "new_password": "newpass1"})
    check("原密码错误改密 → 400", r.status_code == 400, r.text)
    r = requests.post(f"{BASE}/auth/password", headers=h,
                      json={"old_password": pwd, "new_password": "newpass1"})
    check("正确原密码改密 → 200", r.status_code == 200, r.text)
    r = requests.post(f"{BASE}/auth/login", json={"username": uname, "password": pwd})
    check("旧密码不再有效 → 400", r.status_code == 400, r.text)
    r = requests.post(f"{BASE}/auth/login", json={"username": uname, "password": "newpass1"})
    check("新密码登录成功 → 200", r.status_code == 200, r.text)
    token2 = r.json()["access_token"]
    h2 = {"Authorization": f"Bearer {token2}"}

    line()
    print("=== 7.3 账号管理：更新资料 ===")
    r = requests.patch(f"{BASE}/auth/me", headers=h2, json={"display_name": "新昵称"})
    check("更新资料返回新昵称", r.status_code == 200 and r.json()["display_name"] == "新昵称", r.text)

    line()
    print("=== 7.2 会话管理：列表 / 吊销 / 登出 ===")
    import time
    suname = f"r7s_{uuid.uuid4().hex[:8]}"
    requests.post(f"{BASE}/auth/register", json={"username": suname, "password": pwd})
    time.sleep(1.1)  # created_at 精确到秒，间隔保证会话创建时序可判定
    t_b1 = requests.post(f"{BASE}/auth/login", json={"username": suname, "password": pwd}).json()["access_token"]  # 会话 S1
    time.sleep(1.1)
    t_b2 = requests.post(f"{BASE}/auth/login", json={"username": suname, "password": pwd}).json()["access_token"]  # 会话 S2（最新）
    h_b1 = {"Authorization": f"Bearer {t_b1}"}
    h_b2 = {"Authorization": f"Bearer {t_b2}"}
    r = requests.get(f"{BASE}/auth/sessions", headers=h_b2)
    sessions = r.json()
    check("会话列表 = 3（注册自动登录+两次登录）", r.status_code == 200 and len(sessions) == 3, r.text)
    check("多个会话各自独立签发", len({s["id"] for s in sessions}) >= 2)
    # sessions 按 created_at 降序 → 最新一条即会话 S2（t_b2）
    newest_sid = sessions[0]["id"]
    r = requests.delete(f"{BASE}/auth/sessions/{newest_sid}", headers=h_b1)
    check("吊销指定会话 → 200", r.status_code == 200, r.text)
    r = requests.get(f"{BASE}/auth/me", headers=h_b2)
    check("被吊销 token 再访问 /me → 401", r.status_code == 401, r.text)
    r = requests.get(f"{BASE}/auth/me", headers=h_b1)
    check("未被吊销 token 仍可访问 /me → 200", r.status_code == 200, r.text)
    # 登出仍有效的 t_b1
    r = requests.post(f"{BASE}/auth/logout", headers=h_b1)
    check("登出 → 200", r.status_code == 200, r.text)
    r = requests.get(f"{BASE}/auth/me", headers=h_b1)
    check("登出后 token 访问 /me → 401", r.status_code == 401, r.text)

    line()
    print("=== 7.9 种子账号（7.1-7.3 范围；隔离在 7.7 验收）===")
    r = requests.post(f"{BASE}/auth/login", json={"username": "test_alice", "password": "test123456"})
    check("test_alice 默认密码登录成功", r.status_code == 200, r.text)
    ws_alice = r.json().get("workspace", {}).get("id")
    r = requests.post(f"{BASE}/auth/login", json={"username": "test_bob", "password": "test123456"})
    check("test_bob 默认密码登录成功", r.status_code == 200, r.text)
    ws_bob = r.json().get("workspace", {}).get("id")
    check("两个种子账号工作区互异（隔离基础）", ws_alice is not None and ws_alice != ws_bob)

    line()
    print(f"结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()