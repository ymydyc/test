import type {
  KbNote,
  KbNoteDetail,
  KbPreview,
  KbWriteResponse,
} from "../types";

const BASE = "/api/v1/kb";

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

/** 选择性写入知识库（已导入自动跳过，返回逐项结果） */
export async function writeSelected(paths: string[]): Promise<KbWriteResponse> {
  const res = await fetch(`${BASE}/write`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths }),
  });
  return handle(res);
}

/** 知识库笔记列表 */
export async function listNotes(): Promise<{ notes: KbNote[] }> {
  const res = await fetch(`${BASE}/notes`);
  return handle(res);
}

/** 笔记详情（含正文） */
export async function getNote(noteId: number): Promise<KbNoteDetail> {
  const res = await fetch(`${BASE}/notes/${noteId}`);
  return handle(res);
}

/** 保存笔记编辑（来源导入文件标记联动置 0） */
export async function saveNote(
  noteId: number,
  contentMd: string,
): Promise<{ note_id: number; note_path: string; import_status_reset: boolean }> {
  const res = await fetch(`${BASE}/notes/${noteId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content_md: contentMd }),
  });
  return handle(res);
}

/** 删除笔记（级联清理向量块/孤立实体，来源标记置 0） */
export async function deleteNote(
  noteId: number,
): Promise<{ note_id: number; deleted: boolean }> {
  const res = await fetch(`${BASE}/notes/${noteId}`, { method: "DELETE" });
  return handle(res);
}

/** 批量删除笔记（逐条级联清理，缺失跳过） */
export async function bulkDeleteNotes(
  noteIds: number[],
): Promise<{ deleted: number; results: { note_id: number; status: string; reason?: string }[] }> {
  const res = await fetch(`${BASE}/notes/bulk-delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ note_ids: noteIds }),
  });
  return handle(res);
}

/** 解析导入区文件为 Markdown（内容区预览/编辑底稿） */
export async function previewFile(relPath: string): Promise<KbPreview> {
  const res = await fetch(`${BASE}/preview?rel_path=${encodeURIComponent(relPath)}`);
  return handle(res);
}

/** 保存导入区文本文件编辑（标记置 0） */
export async function saveFileContent(
  relPath: string,
  content: string,
): Promise<{ rel_path: string; import_status: number }> {
  const res = await fetch(`${BASE}/file-content`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rel_path: relPath, content }),
  });
  return handle(res);
}
