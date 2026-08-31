import { authFetch } from "./client";
import type { HealthReport, ReviewRecord, ReviewGenerateResult } from "../types";

const BASE = "/api/v1";

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

// ---------- 网页剪藏（FR-12）----------
export async function clipUrl(
  url: string,
  targetDir = "",
): Promise<{ ok: boolean; message: string; data: { path: string; name: string; title: string; source_url: string } }> {
  const res = await authFetch(`${BASE}/import-files/clip`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, target_dir: targetDir }),
  });
  return handle(res);
}

// ---------- 周期回顾（FR-09）----------
export async function generateReview(
  periodType: "week" | "month",
  periodKey?: string,
): Promise<ReviewGenerateResult> {
  const res = await authFetch(`${BASE}/review/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ period_type: periodType, period_key: periodKey ?? null }),
  });
  return handle(res);
}

export async function listReviews(): Promise<{ reviews: ReviewRecord[] }> {
  const res = await authFetch(`${BASE}/review/list`);
  return handle(res);
}

export async function getReview(reviewId: number): Promise<ReviewRecord> {
  const res = await authFetch(`${BASE}/review/${reviewId}`);
  return handle(res);
}

export async function deleteReview(reviewId: number): Promise<{ ok: boolean }> {
  const res = await authFetch(`${BASE}/review/${reviewId}`, { method: "DELETE" });
  return handle(res);
}

// ---------- 健康检查（FR-10）----------
export async function runHealthReport(): Promise<HealthReport> {
  const res = await authFetch(`${BASE}/health/report`);
  return handle(res);
}
