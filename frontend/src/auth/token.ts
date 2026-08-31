/** 阶段七 前端认证降级存储：token / 用户 / 工作区 的 localStorage 读写。 */
export interface AuthUser {
  id: number;
  username: string;
  display_name?: string | null;
  status: number;
}

export interface AuthWorkspace {
  id: number;
  name: string;
  type: string;
  role?: string | null;
}

const TOKEN_KEY = "sb_access_token";
const USER_KEY = "sb_user";
const WS_KEY = "sb_workspace";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function getStoredUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as AuthUser) : null;
  } catch {
    return null;
  }
}

export function getStoredWorkspace(): AuthWorkspace | null {
  try {
    const raw = localStorage.getItem(WS_KEY);
    return raw ? (JSON.parse(raw) as AuthWorkspace) : null;
  } catch {
    return null;
  }
}

export function saveAuth(token: string, user: AuthUser, workspace: AuthWorkspace): void {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
  localStorage.setItem(WS_KEY, JSON.stringify(workspace));
}

export function clearAuth(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  localStorage.removeItem(WS_KEY);
}