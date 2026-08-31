import { authFetch } from "./client";
import { saveAuth, clearAuth, type AuthUser, type AuthWorkspace } from "../auth/token";

const BASE = "/api/v1/auth";

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
  workspace: AuthWorkspace;
}

interface SessionInfo {
  id: number;
  label?: string | null;
  created_at?: string | null;
  last_seen?: string | null;
  expires_at?: string | null;
  revoked: number;
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `请求失败（${res.status}）`;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

/** 登录：成功后持久化 token/user/workspace */
export async function login(username: string, password: string): Promise<TokenResponse> {
  const res = await authFetch(`${BASE}/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const data = await handle<TokenResponse>(res);
  saveAuth(data.access_token, data.user, data.workspace);
  return data;
}

/** 注册：自动创建个人工作区并登录 */
export async function register(
  username: string,
  password: string,
  displayName?: string,
): Promise<TokenResponse> {
  const res = await authFetch(`${BASE}/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username,
      password,
      display_name: displayName || null,
    }),
  });
  const data = await handle<TokenResponse>(res);
  saveAuth(data.access_token, data.user, data.workspace);
  return data;
}

/** 登出：吊销当前令牌并清理本地登录态 */
export async function logout(): Promise<void> {
  try {
    await authFetch(`${BASE}/logout`, { method: "POST" });
  } catch {
    /* 即便吊销失败也清理本地态 */
  }
  clearAuth();
}

/** 当前账号信息（用于登录态刷新/校验） */
export async function me(): Promise<AuthUser> {
  const res = await authFetch(`${BASE}/me`);
  return handle(res);
}

/** 修改密码 */
export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  const res = await authFetch(`${BASE}/password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });
  await handle(res);
}

/** 更新资料（显示名称） */
export async function updateProfile(displayName: string): Promise<AuthUser> {
  const res = await authFetch(`${BASE}/me`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ display_name: displayName || null }),
  });
  return handle(res);
}

/** 活动会话列表 */
export async function listSessions(): Promise<SessionInfo[]> {
  const res = await authFetch(`${BASE}/sessions`);
  return handle(res);
}

/** 吊销指定会话 */
export async function revokeSession(sessionId: number): Promise<void> {
  const res = await authFetch(`${BASE}/sessions/${sessionId}`, { method: "DELETE" });
  await handle(res);
}