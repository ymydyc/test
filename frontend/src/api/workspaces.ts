/** 阶段八 工作区/组/邀请码 API（分组优先；list 走个人首页接口）。 */
import { authFetch } from "./client";
import type { InviteInfo, WorkspaceDetail, WorkspaceInfo } from "../types";

const BASE = "/api/v1/workspaces";

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

/** 当前用户参与的所有工作区（个人 + 组） */
export async function listWorkspaces(): Promise<WorkspaceInfo[]> {
  const res = await authFetch(BASE);
  const data = await handle<{ workspaces: WorkspaceInfo[] }>(res);
  return data.workspaces;
}

/** 创建组工作区 */
export async function createGroup(name: string, description?: string): Promise<WorkspaceInfo> {
  const res = await authFetch(BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, description: description || null }),
  });
  return handle<WorkspaceInfo>(res);
}

/** 组详情（含成员） */
export async function getWorkspace(id: number): Promise<WorkspaceDetail> {
  const res = await authFetch(`${BASE}/${id}`);
  return handle<WorkspaceDetail>(res);
}

/** 编辑组信息（组名/描述，仅创建者） */
export async function updateGroup(
  id: number,
  name?: string,
  description?: string,
): Promise<WorkspaceDetail> {
  const res = await authFetch(`${BASE}/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name ?? null, description: description ?? null }),
  });
  return handle<WorkspaceDetail>(res);
}

/** 解散组（仅创建者） */
export async function deleteGroup(id: number): Promise<void> {
  const res = await authFetch(`${BASE}/${id}`, { method: "DELETE" });
  await handle(res);
}

/** 生成/刷新组邀请码（仅创建者） */
export async function getInvite(id: number): Promise<InviteInfo> {
  const res = await authFetch(`${BASE}/${id}/invite`);
  return handle<InviteInfo>(res);
}

/** 凭邀请码加入组 */
export async function joinGroup(code: string): Promise<WorkspaceInfo> {
  const res = await authFetch(`${BASE}/join`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code: code.trim() }),
  });
  return handle<WorkspaceInfo>(res);
}

/** 成员退出组 */
export async function leaveGroup(id: number): Promise<void> {
  const res = await authFetch(`${BASE}/${id}/leave`, { method: "POST" });
  await handle(res);
}

/** 创建者移除成员 */
export async function removeMember(id: number, userId: number): Promise<void> {
  const res = await authFetch(`${BASE}/${id}/members/${userId}/remove`, { method: "POST" });
  await handle(res);
}

/** 转让组所有权给组内成员（仅创建者） */
export async function transferGroup(id: number, newOwnerId: number): Promise<WorkspaceDetail> {
  const res = await authFetch(`${BASE}/${id}/transfer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ new_owner_id: newOwnerId }),
  });
  return handle<WorkspaceDetail>(res);
}