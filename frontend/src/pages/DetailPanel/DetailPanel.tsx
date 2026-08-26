import { useCallback, useEffect, useRef, useState } from "react";
import type Vditor from "vditor";
import * as kbApi from "../../api/kb";
import type { KbNote, TreeNode } from "../../types";
import VditorEditor from "../../components/VditorEditor";
import GraphView from "./GraphView";
import "./DetailPanel.css";

type Tab = "content" | "kb" | "graph";

/** 当前正在编辑/预览的文档（内容区文件 或 知识库笔记）。 */
interface EditorState {
  kind: "kb" | "file";
  key: string; // 切换文档时作为 React key 重建 Vditor
  title: string;
  md: string;
  textBased: boolean;
  noteId?: number;
  relPath?: string;
  ext?: string;
}

interface DetailPanelProps {
  selectedFile: TreeNode | null;
  /** 导入区/知识库数据版本号（变更时联动刷新笔记列表） */
  dataVersion: number;
  /** 图谱按钮点击信号：值递增时切换到图谱标签页 */
  graphSignal?: number;
  onDataChanged?: () => void;
}

/** 可直存回导入区的文本扩展名（其余文本格式仅预览，编辑请写库后改笔记） */
const DIRECT_EDIT_EXTS = ["txt", "md", "markdown"];

export default function DetailPanel({ selectedFile, dataVersion, graphSignal, onDataChanged }: DetailPanelProps) {
  const [tab, setTab] = useState<Tab>("content");
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [notes, setNotes] = useState<KbNote[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  // 知识库多选删除
  const [checkedNotes, setCheckedNotes] = useState<Set<number>>(new Set());
  const [confirmBulkDelete, setConfirmBulkDelete] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const vditorRef = useRef<Vditor | null>(null);

  // 图谱按钮触发切换到图谱标签页
  useEffect(() => {
    if (graphSignal && graphSignal > 0) setTab("graph");
  }, [graphSignal]);

  const loadNotes = useCallback(async () => {
    try {
      const res = await kbApi.listNotes();
      setNotes(res.notes);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  // 知识库标签页：加载/联动刷新笔记列表
  useEffect(() => {
    if (tab === "kb") loadNotes();
  }, [tab, dataVersion, loadNotes]);

  // 内容标签页：选中导入区文件 → 解析为 Markdown 供预览/编辑
  useEffect(() => {
    if (tab !== "content") return;
    const f = selectedFile;
    if (!f || f.is_dir) {
      setEditor(null);
      setError("");
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    setNotice("");
    kbApi
      .previewFile(f.path)
      .then((p) => {
        if (cancelled) return;
        setEditor({
          kind: "file",
          key: `file:${f.path}`,
          title: f.name,
          md: p.content_md,
          textBased: p.text_based,
          relPath: f.path,
          ext: p.ext,
        });
      })
      .catch((e) => {
        if (!cancelled) setError((e as Error).message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tab, selectedFile]);

  // ---------- 打开 / 保存 / 删除 ----------
  async function openNote(note: KbNote) {
    setError("");
    setNotice("");
    try {
      const d = await kbApi.getNote(note.id);
      setEditor({
        kind: "kb",
        key: `kb:${d.id}`,
        title: d.title || note.note_path,
        md: d.content_md,
        textBased: true,
        noteId: d.id,
      });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function onSaveEditor() {
    const vd = vditorRef.current;
    if (!vd || !editor) return;
    const md = vd.getValue();
    setSaving(true);
    setError("");
    setNotice("");
    try {
      if (editor.kind === "kb" && editor.noteId != null) {
        const r = await kbApi.saveNote(editor.noteId, md);
        setNotice(
          `笔记已保存${r.import_status_reset ? "，来源导入文件标记已重置（需重新写入知识库）" : ""}`,
        );
        setEditor({ ...editor, md });
        await loadNotes();
      } else if (editor.kind === "file" && editor.relPath) {
        await kbApi.saveFileContent(editor.relPath, md);
        setNotice("已保存到导入区，其“已导入”标记已重置（需重新写入知识库）");
        setEditor({ ...editor, md });
        onDataChanged?.();
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function onWriteFile() {
    if (!editor || editor.kind !== "file" || !editor.relPath) return;
    setSaving(true);
    setError("");
    try {
      const res = await kbApi.writeSelected([editor.relPath]);
      const r = res.results[0];
      const msg =
        r?.status === "success"
          ? "已写入知识库"
          : r?.status === "skipped"
            ? `已跳过：${r.reason ?? ""}`
            : `失败：${r?.reason ?? ""}`;
      setNotice(`写入知识库：${msg}`);
      onDataChanged?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function onConfirmDeleteNote() {
    if (!editor || editor.kind !== "kb" || editor.noteId == null) return;
    setSaving(true);
    setError("");
    try {
      await kbApi.deleteNote(editor.noteId);
      setNotice("笔记已删除（级联清理向量块与孤立实体）");
      setEditor(null);
      setConfirmDelete(false);
      await loadNotes();
      onDataChanged?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  // ---------- 知识库：多选删除 ----------
  function toggleNoteCheck(id: number) {
    setCheckedNotes((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleCheckAll() {
    setCheckedNotes((prev) =>
      prev.size === notes.length ? new Set<number>() : new Set(notes.map((n) => n.id)),
    );
  }

  async function onConfirmBulkDeleteNotes() {
    if (checkedNotes.size === 0) return;
    setBulkDeleting(true);
    setError("");
    setNotice("");
    try {
      const ids = Array.from(checkedNotes);
      const res = await kbApi.bulkDeleteNotes(ids);
      const failed = res.results.filter((r) => r.status === "error").length;
      setNotice(
        failed > 0
          ? `已删除 ${res.deleted} 篇笔记，${failed} 篇删除失败`
          : `已删除 ${res.deleted} 篇笔记（级联清理向量块与孤立实体）`,
      );
      setCheckedNotes(new Set());
      if (editor?.kind === "kb" && editor.noteId != null && ids.includes(editor.noteId)) {
        setEditor(null); // 当前正在编辑的笔记被删除 → 关闭编辑器
      }
      setConfirmBulkDelete(false);
      await loadNotes();
      onDataChanged?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBulkDeleting(false);
    }
  }

  // ---------- 渲染 ----------
  function renderEditor() {
    if (!editor) return null;
    const canSaveDirect =
      editor.kind === "file" &&
      editor.textBased &&
      (editor.ext ? DIRECT_EDIT_EXTS.includes(editor.ext) : false);
    return (
      <div className="editor-wrap">
        <div className="editor-toolbar">
          <span className="editor-title">
            {editor.kind === "kb" ? "📝 知识库笔记" : "📄 导入文件"} · {editor.title}
          </span>
          <div className="editor-actions">
            {editor.kind === "file" && (
              <button className="ghost" onClick={onWriteFile} disabled={saving}>
                📥 写入知识库
              </button>
            )}
            {editor.kind === "file" && canSaveDirect && (
              <button className="primary" onClick={onSaveEditor} disabled={saving}>
                保存到导入区
              </button>
            )}
            {editor.kind === "kb" && (
              <>
                <button className="danger-ghost" onClick={() => setConfirmDelete(true)} disabled={saving}>
                  🗑 删除
                </button>
                <button className="primary" onClick={onSaveEditor} disabled={saving}>
                  保存
                </button>
              </>
            )}
            <button className="ghost" onClick={() => setEditor(null)}>
              关闭
            </button>
          </div>
        </div>
        <div className="editor-body">
          <VditorEditor
            key={editor.key}
            initialValue={editor.md}
            height="calc(100vh - 235px)"
            onReady={(vd) => {
              vditorRef.current = vd;
            }}
          />
        </div>
        {!canSaveDirect && editor.kind === "file" && (
          <div className="editor-hint">该格式仅预览，编辑请先「写入知识库」后在笔记中修改。</div>
        )}
      </div>
    );
  }

  function renderContent() {
    if (editor?.kind === "file") return renderEditor();
    if (!selectedFile) {
      return (
        <div className="placeholder">
          内容可视化 / 编辑
          <p className="placeholder-sub">请在左侧选择一个文件查看与编辑内容。</p>
        </div>
      );
    }
    if (selectedFile.is_dir) {
      return (
        <div className="placeholder">
          文件夹：{selectedFile.path || "/"}
          <p className="placeholder-sub">
            文件夹无法直接编辑。请选中其中的文件，或前往「知识库」标签页管理笔记。
          </p>
        </div>
      );
    }
    if (loading) {
      return (
        <div className="placeholder">
          <span className="spinner" />
          <span className="loading-label">正在解析 {selectedFile.name}…</span>
          <p className="placeholder-sub">将通过解析器转换为 Markdown。</p>
        </div>
      );
    }
    return renderEditor();
  }

  function renderKb() {
    if (editor?.kind === "kb") return renderEditor();
    return (
      <div className="kb-view">
        <div className="kb-head">
          <span>
            知识库笔记（{notes.length}
            {checkedNotes.size > 0 ? ` · 已选 ${checkedNotes.size}` : ""}）
          </span>
          <span className="kb-head-actions">
            <label className="kb-check-all" title="全选 / 取消全选">
              <input
                type="checkbox"
                checked={notes.length > 0 && checkedNotes.size === notes.length}
                onChange={toggleCheckAll}
              />
              全选
            </label>
            <button
              className="danger-ghost"
              disabled={checkedNotes.size === 0 || bulkDeleting}
              onClick={() => setConfirmBulkDelete(true)}
            >
              {bulkDeleting ? "删除中…" : `🗑 删除选中（${checkedNotes.size}）`}
            </button>
            <button className="ghost" onClick={loadNotes}>
              🔄 刷新
            </button>
          </span>
        </div>
        {notes.length === 0 ? (
          <div className="placeholder">
            知识库为空
            <p className="placeholder-sub">在左侧导入区选中文件/文件夹后，点击「写入知识库」。</p>
          </div>
        ) : (
          <div className="kb-list">
            {notes.map((n) => (
              <div key={n.id} className={`kb-item${checkedNotes.has(n.id) ? " checked" : ""}`}>
                <span
                  className="kb-item-check"
                  onClick={(e) => e.stopPropagation()}
                  title="勾选以批量删除"
                >
                  <input
                    type="checkbox"
                    checked={checkedNotes.has(n.id)}
                    onChange={() => toggleNoteCheck(n.id)}
                  />
                </span>
                <div className="kb-item-main" onClick={() => openNote(n)}>
                  <div className="kb-item-title">{n.title || n.note_path}</div>
                  <div className="kb-item-meta">
                    <code>{n.note_path}</code>
                    {n.origin_rel_path && <span> · 来源：{n.origin_rel_path}</span>}
                    {n.updated_at && (
                      <span> · 更新：{n.updated_at.replace("T", " ").slice(0, 19)}</span>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="detail-panel">
      <div className="detail-tabs">
        <button className={tab === "content" ? "active" : ""} onClick={() => setTab("content")}>
          内容
        </button>
        <button className={tab === "kb" ? "active" : ""} onClick={() => setTab("kb")}>
          知识库
        </button>
        <button className={tab === "graph" ? "active" : ""} onClick={() => setTab("graph")}>
          图谱
        </button>
      </div>

      {error && <div className="msg error">{error}</div>}
      {notice && <div className="msg info">{notice}</div>}

      <div className="detail-body">
        {tab === "content" && renderContent()}
        {tab === "kb" && renderKb()}
        {tab === "graph" && <GraphView />}
      </div>

      {confirmDelete && editor?.kind === "kb" && (
        <div className="modal-overlay" onClick={() => setConfirmDelete(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-title">删除笔记</div>
            <div className="modal-body">
              确认删除笔记 <code>{editor.title}</code> ？
              <div className="modal-sub warn">
                将级联清理其向量块与孤立实体（共享实体保留），不可恢复。
              </div>
            </div>
            <div className="modal-actions">
              <button className="ghost" onClick={() => setConfirmDelete(false)}>
                取消
              </button>
              <button className="danger" onClick={onConfirmDeleteNote} disabled={saving}>
                删除
              </button>
            </div>
          </div>
        </div>
      )}
    {confirmBulkDelete && checkedNotes.size > 0 && (
        <div className="modal-overlay" onClick={() => setConfirmBulkDelete(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-title">批量删除笔记</div>
            <div className="modal-body">
              确认删除选中的 <code>{checkedNotes.size}</code> 篇笔记 ？
              <div className="modal-sub warn">
                将逐篇级联清理其向量块与孤立实体（共享实体保留），不可恢复。
              </div>
            </div>
            <div className="modal-actions">
              <button className="ghost" onClick={() => setConfirmBulkDelete(false)}>
                取消
              </button>
              <button className="danger" onClick={onConfirmBulkDeleteNotes} disabled={bulkDeleting}>
                {bulkDeleting ? "删除中…" : "删除"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
