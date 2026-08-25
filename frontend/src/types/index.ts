export interface TreeNode {
  name: string;
  path: string;
  type: "dir" | "file";
  is_dir: boolean;
  import_status: number;
  ext_type?: string;
  file_size?: number;
  note_id?: number | null;
  children?: TreeNode[];
}

export interface OperationData {
  ok: boolean;
  message: string;
  data?: Record<string, unknown>;
}

// ---------- 知识库（阶段二 FR-02 / FR-11）----------
export interface KbWriteResult {
  path: string;
  type: "file" | "dir";
  status: "success" | "skipped" | "error";
  reason?: string;
  note_path?: string;
  note_id?: number;
  items?: KbWriteResult[];
}

export interface KbWriteResponse {
  results: KbWriteResult[];
  summary: { written: number; skipped: number; failed: number };
}

export interface KbNote {
  id: number;
  note_path: string;
  title: string;
  origin_import_id: number | null;
  origin_rel_path: string | null;
  updated_at: string | null;
}

export interface KbNoteDetail extends KbNote {
  content_md: string;
}

export interface KbPreview {
  rel_path: string;
  content_md: string;
  text_based: boolean;
  ext: string;
}
