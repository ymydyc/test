/** 阶段七 鉴权请求封装：自动携带 Bearer token，遇 401 清除登录态回登录页。
 *
 * 复用现有的 `handle()` 模式：仅在此层注入 Authorization 头，并对全局 401 做兜底
 * （token 过期/被吊销 → 清空本地登录态并整页刷新，应用随即呈现登录页）。
 */
import { getToken, clearAuth } from "../auth/token";
import { getActiveWorkspace } from "../auth/workspaceStore";

const REDIRECT_FLAG = "sb_redirecting";

export async function authFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const token = getToken();
  const headers = new Headers(init?.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  // 活动工作区：切换后所有数据请求按该工作区隔离（组共享场景）
  headers.set("X-Workspace-Id", String(getActiveWorkspace().id));
  const res = await fetch(input, { ...init, headers });
  if (res.status === 401) {
    // 只触发一次刷新生效，避免 401 与页面 gating 相互循环刷新
    clearAuth();
    if (!sessionStorage.getItem(REDIRECT_FLAG)) {
      sessionStorage.setItem(REDIRECT_FLAG, "1");
      window.location.reload();
    }
  }
  return res;
}