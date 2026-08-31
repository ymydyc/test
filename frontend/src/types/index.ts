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

// ---------- 图谱（阶段四 FR-06） ----------
export interface GraphStatus {
  dashscope: boolean;
  llm: { model: string; embedding_model: string; embedding_dim: number };
  graph: boolean;
  vector: { chunk_count: number; entity_count: number };
  ready: boolean;
}

export interface GraphNode {
  id: string;
  name: string;
  entity_type?: string;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  relation_type?: string;
  description?: string;
}

export interface GraphExport {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface GraphEdgeDetail {
  source: string;
  target: string;
  relation_type: string;
  description: string | null;
}

export interface GraphNodeDetail {
  name: string;
  entity_type: string;
  description: string | null;
  source_note_ids: string | null;
  edges: GraphEdgeDetail[];
  documents: { note_id: number; title: string; note_path: string }[];
}

// ---------- AI 助手（阶段四 FR-07/FR-08） ----------
export interface AssistantSession {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface Citation {
  idx: number;
  title: string;
  note_id?: number;
  note_path?: string;
  chunk_id?: number;
}

export interface ChatMessageItem {
  id: number;
  role: "user" | "assistant";
  content: string;
  citations: Citation[] | null;
  created_at: string;
}

export type SseEvent =
  | { type: "delta"; content: string }
  | { type: "citations"; citations: Citation[] }
  | { type: "stage"; node: string; text: string }
  | { type: "done"; session_id: number; message_id: number | null };

export interface QuestionsResult {
  ok: boolean;
  questions: string;
  count: number;
}

export interface RetrieveImportResult {
  ok: boolean;
  files: { path: string; name: string; dir: string; snippet: string }[];
  total: number;
}

export interface GenerateMdResult {
  ok: boolean;
  path: string;
  abs_path: string;
}

// ---------- 阶段八 工作区/组/邀请码 ----------
export interface WorkspaceInfo {
  id: number;
  name: string;
  type: string;
  role?: string | null;
}

export interface WorkspaceMember {
  user_id: number;
  username: string | null;
  display_name: string | null;
  role: string;
}

export interface WorkspaceDetail {
  id: number;
  name: string;
  type: string;
  creator_id: number;
  description: string | null;
  created_at: string | null;
  members: WorkspaceMember[];
  role: string | null;
}

export interface InviteInfo {
  workspace_id: number;
  code: string;
  expires_at: string;
}

// ---------- 阶段五（FR-09 周期回顾 / FR-10 健康检查 / FR-12 网页剪藏）----------
export interface ReviewRecord {
  id: number;
  period_type: string;
  period_key: string;
  title: string;
  note_path: string;
  note_id: number | null;
  note_count: number;
  created_at: string;
  summary_md?: string;
}

export interface ReviewGenerateResult {
  status: string;
  period_type: string;
  period_key: string;
  note_count: number;
  chat_count?: number;
  import_count?: number;
  note_id?: number;
  note_path?: string;
  summary_md?: string;
  reason?: string;
}

export interface HealthReport {
  generated_at: string;
  summary: {
    isolated_entities: number;
    stale_notes: number;
    missing_note_files: number;
    missing_import_files: number;
    unregistered_files: number;
    no_backlink_notes: number;
  };
  total_issues: number;
  healthy: boolean;
  isolated_entities: { id: number; name: string; entity_type: string; description: string | null }[];
  stale_notes: { id: number; note_path: string; title: string }[];
  missing_note_files: { id: number; note_path: string; title: string }[];
  missing_import_files: { id: number; rel_path: string; file_name: string }[];
  unregistered_files: { rel_path: string; file_size: number }[];
  no_backlink_notes: { id: number; note_path: string; title: string }[];
}

