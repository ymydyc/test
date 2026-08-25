import type { OperationData, TreeNode } from "../types";

const BASE = "/api/v1/import-files";

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

export async function fetchTree(): Promise<TreeNode> {
  const res = await fetch(`${BASE}/tree`);
  return handle(res);
}

export async function uploadFiles(
  files: File[],
  relPaths: string[],
  targetDir = "",
): Promise<OperationData> {
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  form.append("paths", JSON.stringify(relPaths));
  form.append("target_dir", targetDir);
  const res = await fetch(`${BASE}/upload`, { method: "POST", body: form });
  return handle(res);
}

export async function createFolder(
  targetDir: string,
  name: string,
): Promise<OperationData> {
  const res = await fetch(`${BASE}/folders`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target_dir: targetDir, name }),
  });
  return handle(res);
}

export async function renameItem(
  relPath: string,
  newName: string,
): Promise<OperationData> {
  const res = await fetch(`${BASE}/rename`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rel_path: relPath, new_name: newName }),
  });
  return handle(res);
}

export async function moveItem(
  relPath: string,
  targetDir: string,
): Promise<OperationData> {
  const res = await fetch(`${BASE}/move`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rel_path: relPath, target_dir: targetDir }),
  });
  return handle(res);
}

export async function deleteItem(relPath: string): Promise<OperationData> {
  const suffix = encodeURI(relPath.split("/").map(encodeURIComponent).join("/"));
  const res = await fetch(`${BASE}/${suffix}`, { method: "DELETE" });
  return handle(res);
}