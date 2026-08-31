/** 阶段七 登录态上下文：驱动 App 的路由拦截（未登录→登录页）。
 *
 * - 启动即有本地持久化 user/workspace，避免闪白；
 * - 若有 token 则后台用 /auth/me 校验，失败即清理登录态返回匿名；
 * - 提供 login/register/logout 并同步本地存储。
 */
import { createContext, useContext, useEffect, useState, type ReactNode, type FC } from "react";
import {
  getToken,
  getStoredUser,
  getStoredWorkspace,
  clearAuth,
  type AuthUser,
  type AuthWorkspace,
} from "./token";
import * as authApi from "../api/auth";

interface AuthContextValue {
  ready: boolean;
  user: AuthUser | null;
  workspace: AuthWorkspace | null;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string, displayName?: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export const AuthProvider: FC<{ children: ReactNode }> = ({ children }) => {
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [workspace, setWorkspace] = useState<AuthWorkspace | null>(null);

  // 启动校验：有 token 则查验当前用户
  useEffect(() => {
    const token = getToken();
    if (!token) {
      setReady(true);
      return;
    }
    const storedUser = getStoredUser();
    const storedWs = getStoredWorkspace();
    if (storedUser && storedWs) {
      setUser(storedUser);
      setWorkspace(storedWs);
      setReady(true);
    }
    // 后台校验 token 是否仍有效，无效则清登录态
    authApi
      .me()
      .then((mu) => {
        setUser(mu);
        if (!storedWs && !getStoredWorkspace()) {
          setWorkspace(null);
        }
      })
      .catch(() => {
        clearAuth();
        setUser(null);
        setWorkspace(null);
      })
      .finally(() => setReady(true));
  }, []);

  async function login(username: string, password: string) {
    const data = await authApi.login(username, password);
    setUser(data.user);
    setWorkspace(data.workspace);
  }

  async function register(username: string, password: string, displayName?: string) {
    const data = await authApi.register(username, password, displayName);
    setUser(data.user);
    setWorkspace(data.workspace);
  }

  async function logout() {
    await authApi.logout();
    setUser(null);
    setWorkspace(null);
  }

  return (
    <AuthContext.Provider value={{ ready, user, workspace, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
};

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth 必须在 <AuthProvider> 内使用");
  }
  return ctx;
}