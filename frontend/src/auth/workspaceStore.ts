/** 阶段八 活动工作区（X-Workspace-Id）前端持久化。
 *
 * 用户注册后默认活动工作区 = 个人工作区；切换到任一已参与的工作区（个人/组）后，
 * 所有数据请求（导入区/知识库/图谱/向量）均经 authFetch 发送 X-Workspace-Id 按该工作区隔离。
 * 无本地记录时回退到登录返回的个人工作区。
 */
import { getStoredWorkspace } from "./token";

export interface ActiveWorkspace {
  id: number;
  name: string;
  type: string;
}

const ACTIVE_KEY = "sb_active_workspace";

export function getActiveWorkspace(): ActiveWorkspace {
  try {
    const raw = localStorage.getItem(ACTIVE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as ActiveWorkspace;
      if (parsed && typeof parsed.id === "number") return parsed;
    }
  } catch {
    /* fallthrough */
  }
  const home = getStoredWorkspace();
  return home
    ? { id: home.id, name: home.name, type: home.type }
    : { id: 1, name: "默认工作区", type: "personal" };
}

export function setActiveWorkspace(ws: ActiveWorkspace): void {
  localStorage.setItem(ACTIVE_KEY, JSON.stringify(ws));
}

export function clearActiveWorkspace(): void {
  localStorage.removeItem(ACTIVE_KEY);
}