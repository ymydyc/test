import { authFetch } from "./client";
import type {
  AssistantSession,
  ChatMessageItem,
  GenerateMdResult,
  QuestionsResult,
  RetrieveImportResult,
  SseEvent,
} from "../types";

const BASE = "/api/v1/assistant";

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

export async function listSessions(): Promise<{ sessions: AssistantSession[] }> {
  const res = await authFetch(`${BASE}/sessions`);
  return handle(res);
}

export async function createSession(
  title?: string,
): Promise<{ id: number; title: string; created_at: string }> {
  const res = await authFetch(`${BASE}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(title ? { title } : {}),
  });
  return handle(res);
}

export async function listMessages(
  sessionId: number,
): Promise<{ session_id: number; messages: ChatMessageItem[] }> {
  const res = await authFetch(`${BASE}/sessions/${sessionId}/messages`);
  return handle(res);
}

export async function renameSession(
  sessionId: number,
  title: string,
): Promise<{ id: number; title: string; created_at: string; updated_at: string }> {
  const res = await authFetch(`${BASE}/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  return handle(res);
}

export async function deleteSession(
  sessionId: number,
): Promise<{ ok: boolean; session_id: number }> {
  const res = await authFetch(`${BASE}/sessions/${sessionId}`, { method: "DELETE" });
  return handle(res);
}

/**
 * SSE 流式对话：POST /assistant/chat/stream，逐事件解析并回调。
 * onEvent 收到 {type:"delta"|"citations"|"done", ...}。
 */
export async function streamChat(
  body: { session_id: number | null; message: string; top_k?: number },
  onEvent: (ev: SseEvent) => void,
): Promise<void> {
  const res = await authFetch(`${BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    let detail = `请求失败（${res.status}）`;
    try {
      const b = await res.json();
      detail = b.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    // 按空行切分 SSE 帧
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      for (const line of frame.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const data = line.slice(5).trim();
        if (data === "[DONE]") return;
        try {
          onEvent(JSON.parse(data) as SseEvent);
        } catch {
          /* 忽略无法解析的事件 */
        }
      }
    }
  }
}

export async function generateQuestions(
  topic?: string,
  count = 5,
): Promise<QuestionsResult> {
  const res = await authFetch(`${BASE}/questions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ topic: topic || null, count }),
  });
  return handle(res);
}

export async function retrieveImport(
  query: string,
  limit = 10,
): Promise<RetrieveImportResult> {
  const res = await authFetch(`${BASE}/retrieve-import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, limit }),
  });
  return handle(res);
}

export async function generateMd(body: {
  content: string;
  title?: string;
  target_subpath?: string;
}): Promise<GenerateMdResult> {
  const res = await authFetch(`${BASE}/generate-md`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle(res);
}