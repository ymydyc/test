import { authFetch } from "./client";
import type { GraphExport, GraphNodeDetail, GraphStatus } from "../types";

const BASE = "/api/v1/graph";

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

/** 图谱就绪状态（前端据此决定图谱按钮可点） */
export async function getGraphStatus(): Promise<GraphStatus> {
  const res = await authFetch(`${BASE}/status`);
  return handle(res);
}

/** 导出图谱可视化数据源（节点+边） */
export async function exportGraph(limit = 1000): Promise<GraphExport> {
  const res = await authFetch(`${BASE}/export?limit=${limit}`);
  return handle(res);
}

/** 实体节点详情（点击节点后展示） */
export async function getNodeDetail(name: string): Promise<GraphNodeDetail> {
  const res = await authFetch(`${BASE}/node/${encodeURIComponent(name)}`);
  return handle(res);
}

/** 按笔记批量重建图谱（🚩 需要 DASHSCOPE_API_KEY，逐笔记调用 LLM 抽取实体/关系） */
export async function buildGraph(
  noteIds: number[],
): Promise<{ results: { note_id: number; status: string; reason?: string }[] }> {
  const res = await authFetch(`${BASE}/build`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ note_ids: noteIds }),
  });
  return handle(res);
}

/** 删除实体节点及其全部关系（MySQL 权威 + Neo4j 同步） */
export async function deleteGraphNode(name: string): Promise<{ name: string; deleted: boolean }> {
  const res = await authFetch(`${BASE}/node/${encodeURIComponent(name)}`, { method: "DELETE" });
  return handle(res);
}

/** 删除指定图谱关系边（MySQL 权威 + Neo4j 同步） */
export async function deleteGraphRelation(
  payload: { source: string; target: string; relation_type: string },
): Promise<{ source: string; target: string; relation_type: string; deleted: boolean }> {
  const res = await authFetch(`${BASE}/relation/delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return handle(res);
}