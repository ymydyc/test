import { useCallback, useEffect, useState } from "react";
import * as api from "../../api/advanced";
import type { HealthReport, ReviewGenerateResult, ReviewRecord } from "../../types";
import "./AdvancedPanel.css";

interface AdvancedPanelProps {
  onClose: () => void;
  /** 回顾生成后通知父级（知识库/图谱可能变化）刷新 */
  onDataChanged?: () => void;
}

type Tab = "review" | "health";

const PERIOD_LABEL: Record<string, string> = { week: "周报", month: "月报" };

export default function AdvancedPanel({ onClose, onDataChanged }: AdvancedPanelProps) {
  const [tab, setTab] = useState<Tab>("review");
  // 回顾
  const [reviews, setReviews] = useState<ReviewRecord[]>([]);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [reviewResult, setReviewResult] = useState<ReviewGenerateResult | null>(null);
  // 健康
  const [health, setHealth] = useState<HealthReport | null>(null);
  const [healthBusy, setHealthBusy] = useState(false);
  // 通用
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const loadReviews = useCallback(async () => {
    try {
      const res = await api.listReviews();
      setReviews(res.reviews);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    loadReviews();
  }, [loadReviews]);

  async function handleGenerate(periodType: "week" | "month") {
    setReviewBusy(true);
    setError("");
    setReviewResult(null);
    try {
      const res = await api.generateReview(periodType);
      setReviewResult(res);
      setNotice(
        res.status === "ok"
          ? `已生成 ${PERIOD_LABEL[periodType]}（${res.period_key}，对话 ${res.chat_count ?? 0} 导入 ${res.import_count ?? 0} 笔记 ${res.note_count}）`
          : `跳过：${res.reason ?? res.status}`,
      );
      await loadReviews();
      onDataChanged?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setReviewBusy(false);
    }
  }

  async function handleDeleteReview(id: number) {
    try {
      await api.deleteReview(id);
      setNotice("已删除回顾记录");
      await loadReviews();
      onDataChanged?.();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function handleHealth() {
    setHealthBusy(true);
    setError("");
    try {
      const rep = await api.runHealthReport();
      setHealth(rep);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setHealthBusy(false);
    }
  }

  const healthItems: { key: keyof HealthReport["summary"]; label: string }[] = [
    { key: "isolated_entities", label: "孤立实体" },
    { key: "stale_notes", label: "过时笔记" },
    { key: "missing_note_files", label: "缺失笔记文件" },
    { key: "missing_import_files", label: "缺失导入文件" },
    { key: "unregistered_files", label: "未登记文件" },
    { key: "no_backlink_notes", label: "无双链笔记" },
  ];

  return (
    <div className="adv-panel">
      <div className="adv-head">
        <span className="adv-title">🛠 进阶能力</span>
        <div className="adv-head-actions">
          <button className="ghost" onClick={onClose}>✕ 关闭</button>
        </div>
      </div>

      <div className="adv-tabs">
        <button className={tab === "review" ? "active" : ""} onClick={() => setTab("review")}>📅 周期回顾</button>
        <button className={tab === "health" ? "active" : ""} onClick={() => setTab("health")}>🩺 健康检查</button>
      </div>

      {error && <div className="adv-msg error">{error}</div>}
      {notice && <div className="adv-msg info">{notice}</div>}

      {tab === "review" && (
        <div className="adv-body">
          <div className="adv-section">
            <div className="adv-section-title">生成回顾（LLM 综合 AI 对话记录 + 导入文件 + 知识库笔记，汇总工作回顾报告）</div>
            <div className="adv-actions">
              <button className="primary" disabled={reviewBusy} onClick={() => handleGenerate("week")}>
                {reviewBusy ? "生成中…" : "📝 生成周报"}
              </button>
              <button className="primary" disabled={reviewBusy} onClick={() => handleGenerate("month")}>
                {reviewBusy ? "生成中…" : "📄 生成月报"}
              </button>
            </div>
            {reviewResult && reviewResult.status === "ok" && (
              <div className="adv-review-result">
                <div className="review-result-head">
                  已生成：{reviewResult.period_key}
                  <span className="review-stat">
                    {reviewResult.chat_count ? `对话 ${reviewResult.chat_count} ` : ""}
                    {reviewResult.import_count ? `导入 ${reviewResult.import_count} ` : ""}
                    {reviewResult.note_count ? `笔记 ${reviewResult.note_count} ` : ""}
                  </span>
                  <span className="review-path">{reviewResult.note_path}</span>
                </div>
                {reviewResult.summary_md && (
                  <pre className="review-summary">{reviewResult.summary_md}</pre>
                )}
              </div>
            )}
            <div className="adv-tip">
              定时任务已启用：周记每周一 08:00 · 月报每月 1 日 08:00（自动生成并入库）。
            </div>
          </div>

          <div className="adv-section">
            <div className="adv-section-title">历史回顾（{reviews.length}）</div>
            {reviews.length === 0 ? (
              <div className="adv-empty">暂无回顾记录，点击上方按钮生成。</div>
            ) : (
              <div className="review-list">
                {reviews.map((r) => (
                  <div key={r.id} className="review-item">
                    <div className="review-item-main">
                      <span className="review-type">{PERIOD_LABEL[r.period_type] ?? r.period_type}</span>
                      <span className="review-title">{r.title}</span>
                      <span className="review-meta">{r.period_key} · {r.note_count} 条 · {r.created_at?.slice(0, 16)}</span>
                    </div>
                    <button className="danger" title="删除回顾（同步删除笔记）" onClick={() => handleDeleteReview(r.id)}>🗑</button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {tab === "health" && (
        <div className="adv-body">
          <div className="adv-section">
            <div className="adv-section-title">知识健康检查（孤立节点 / 过时信息 / 双链缺失）</div>
            <div className="adv-actions">
              <button className="primary" disabled={healthBusy} onClick={handleHealth}>
                {healthBusy ? "检查中…" : "🔍 运行检查"}
              </button>
            </div>
          </div>

          {health && (
            <div className="adv-section">
              <div className={`health-banner ${health.healthy ? "ok" : "warn"}`}>
                {health.healthy ? "✅ 知识库健康，无发现问题" : `⚠️ 发现 ${health.total_issues} 个问题`}
                <span className="health-time">检查时间：{health.generated_at}</span>
              </div>
              <div className="health-grid">
                {healthItems.map((it) => (
                  <div key={it.key} className={`health-card ${health.summary[it.key] > 0 ? "warn" : "ok"}`}>
                    <div className="health-count">{health.summary[it.key]}</div>
                    <div className="health-label">{it.label}</div>
                  </div>
                ))}
              </div>
              {healthItems.map((it) => {
                const list = health[it.key] as { id?: number; name?: string; note_path?: string; title?: string; rel_path?: string }[];
                if (!list.length) return null;
                return (
                  <div key={it.key} className="health-detail">
                    <div className="health-detail-title">{it.label}（{list.length}）</div>
                    <div className="health-detail-list">
                      {list.slice(0, 10).map((x, i) => (
                        <div key={i} className="health-detail-item">
                          {x.name ?? x.note_path ?? x.rel_path ?? x.title}
                        </div>
                      ))}
                      {list.length > 10 && <div className="health-more">… 共 {list.length} 条</div>}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
