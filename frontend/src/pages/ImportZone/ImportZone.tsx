import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../../api/importFiles";
import * as advancedApi from "../../api/advanced";
import * as kbApi from "../../api/kb";
import type { KbWriteResponse, KbWriteResult, TreeNode } from "../../types";
import LoadingOverlay from "../../components/LoadingOverlay";
import "./ImportZone.css";

const INDENT = 16;

interface ImportZoneProps {
  onSelectFile?: (node: TreeNode) => void;
  /** 数据变更（写库/编辑保存）后通知父级联动刷新 */
  onDataChanged?: () => void;
}

// 右键菜单：node 为 null 表示在空白区域（目标为 selectedPath）
interface CtxState {
  x: number;
  y: number;
  node: TreeNode | null;
}

// 输入弹窗：create = 新建文件夹；rename = 重命名
interface InputState {
  kind: "create" | "rename";
  parent: string;
  node?: TreeNode;
}

interface MoveState {
  source: TreeNode;
}

/** 收集目录到扁平列表（含 depth），用于折叠初始化与移动目标选择。 */
function collectDirs(node: TreeNode, depth: number, out: { path: string; name: string; depth: number }[]) {
  for (const child of node.children ?? []) {
    if (child.is_dir) {
      out.push({ path: child.path, name: child.name, depth });
      collectDirs(child, depth + 1, out);
    }
  }
}

export default function ImportZone({ onSelectFile, onDataChanged }: ImportZoneProps) {
  const [tree, setTree] = useState<TreeNode | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selectedPath, setSelectedPath] = useState(""); // 目标目录
  const [expanded, setExpanded] = useState<string[]>([]); // 展开的目录集合
  const [ctx, setCtx] = useState<CtxState | null>(null);
  const [modal, setModal] = useState<InputState | null>(null);
  const [confirmNode, setConfirmNode] = useState<TreeNode | null>(null);
  const [moveState, setMoveState] = useState<MoveState | null>(null);
  const [moveTarget, setMoveTarget] = useState<string | null>(null);
  const [inputVal, setInputVal] = useState("");
  const [checked, setChecked] = useState<string[]>([]); // 勾选写入知识库的路径集合
  const [writeResult, setWriteResult] = useState<KbWriteResponse | null>(null);
  const [writing, setWriting] = useState(false);
  const [uploading, setUploading] = useState(false); // 导入文档中
  const [clipOpen, setClipOpen] = useState(false);
  const [clipUrl, setClipUrl] = useState("");
  const [clipping, setClipping] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setError("");
      const t = await api.fetchTree();
      setTree(t);
      const dirs: { path: string; name: string; depth: number }[] = [];
      collectDirs(t, 1, dirs);
      setExpanded(dirs.map((d) => d.path)); // 默认全部展开
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const close = () => setCtx(null);
    window.addEventListener("click", close);
    window.addEventListener("blur", close);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("blur", close);
    };
  }, []);

  // ---------- 右键菜单 ----------
  function openCtx(e: React.MouseEvent, node: TreeNode | null) {
    e.preventDefault();
    e.stopPropagation();
    if (node?.is_dir) setSelectedPath(node.path);
    setCtx({ x: e.clientX, y: e.clientY, node });
  }

  // ---------- 折叠 ----------
  function toggleExpand(path: string) {
    setExpanded((prev) =>
      prev.includes(path) ? prev.filter((p) => p !== path) : [...prev, path],
    );
  }

  // ---------- 上传（仅单个/多个文件） ----------
  function onPickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    if (!files.length) return;
    setUploading(true);
    setError("");
    api
      .uploadFiles(files, files.map((f) => f.name), selectedPath)
      .then((res) => {
        setNotice(res.message || `${files.length} 个文件导入成功`);
        load();
      })
      .catch((err) => setError((err as Error).message))
      .finally(() => setUploading(false));
    e.target.value = "";
  }

  // ---------- 网页剪藏（FR-12） ----------
  async function submitClip() {
    const url = clipUrl.trim();
    if (!url) return;
    setClipping(true);
    setError("");
    try {
      const res = await advancedApi.clipUrl(url, selectedPath);
      setNotice(`剪藏成功：${res.data.name}`);
      setClipOpen(false);
      setClipUrl("");
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setClipping(false);
    }
  }

  // ---------- 新建 / 重命名 ----------
  function openCreate(parent: string) {
    setInputVal("");
    setModal({ kind: "create", parent });
  }

  function openRename(node: TreeNode) {
    setInputVal(node.name);
    setModal({ kind: "rename", parent: node.path, node });
  }

  async function submitModal() {
    if (!modal) return;
    const val = inputVal.trim();
    if (!val) return;
    try {
      if (modal.kind === "create") {
        await api.createFolder(modal.parent, val);
        setNotice(`已新建文件夹：${modal.parent ? modal.parent + "/" : ""}${val}`);
      } else if (modal.node) {
        await api.renameItem(modal.node.path, val);
        setNotice(`已重命名：${modal.node.name} → ${val}`);
      }
      setModal(null);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function cancelModal() {
    setModal(null);
    setInputVal("");
  }

  // ---------- 移动 ----------
  function openMove(node: TreeNode) {
    setMoveTarget(null);
    setMoveState({ source: node });
  }

  async function submitMove() {
    if (!moveState || moveTarget === null) return;
    try {
      const res = await api.moveItem(moveState.source.path, moveTarget);
      setNotice(res.message);
      setError("");
      setMoveState(null);
      load();
    } catch (e) {
      setError((e as Error).message);
      setMoveState(null);
    }
  }

  function cancelMove() {
    setMoveState(null);
  }

  /** 移动目标目录列表（根可选；若是目录则排除源自身及其子目录）。 */
  function moveDirs(): { path: string; name: string; depth: number }[] {
    if (!tree) return [];
    const list: { path: string; name: string; depth: number }[] = [
      { path: "", name: "/ （根目录）", depth: 0 },
    ];
    collectDirs(tree, 1, list);
    const src = moveState?.source;
    if (!src || !src.is_dir) return list;
    const prefix = src.path + "/";
    return list.filter(
      (d) => d.path !== src.path && !d.path.startsWith(prefix),
    );
  }

  // ---------- 删除（自定义确认） ----------
  async function confirmDelete() {
    if (!confirmNode) return;
    const node = confirmNode;
    setConfirmNode(null);
    try {
      await api.deleteItem(node.path);
      setNotice(`已删除：${node.path}`);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  // ---------- 多选 / 写入知识库 ----------
  function toggleCheck(path: string) {
    setChecked((prev) =>
      prev.includes(path) ? prev.filter((p) => p !== path) : [...prev, path],
    );
  }

  async function handleWrite() {
    const paths = checked;
    if (!paths.length) return;
    setWriting(true);
    setError("");
    setWriteResult(null);
    try {
      const res = await kbApi.writeSelected(paths);
      setWriteResult(res);
      setNotice(
        `写入完成：新增 ${res.summary.written} · 跳过 ${res.summary.skipped} · 失败 ${res.summary.failed}`,
      );
      setChecked([]);
      load();
      onDataChanged?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setWriting(false);
    }
  }

  /** 递归渲染写入结果条目（目录含子项） */
  function renderWriteItem(item: KbWriteResult): React.ReactNode {
    const label =
      item.status === "success" ? "✅ 已写入" : item.status === "skipped" ? "⏭ 已跳过" : "❌ 失败";
    return (
      <div key={item.path} className={`write-item ${item.status}`}>
        <span className="write-status">{label}</span>
        <span className="write-path">
          {item.path}
          {item.type === "dir" ? "/" : ""}
        </span>
        {item.reason && <span className="write-reason">{item.reason}</span>}
        {item.note_path && <span className="write-note">→ {item.note_path}</span>}
        {item.items && <div className="write-children">{item.items.map((c) => renderWriteItem(c))}</div>}
      </div>
    );
  }

  // ---------- 渲染 ----------
  function renderNode(node: TreeNode, depth: number): React.ReactNode {
    const pad = { paddingLeft: `${depth * INDENT}px` };
    const isSel = node.is_dir && node.path === selectedPath;
    if (node.is_dir) {
      const isOpen = expanded.includes(node.path);
      return (
        <div key={node.path}>
          <div
            className={`tree-row dir ${isSel ? "selected" : ""}`}
            style={pad}
            onClick={() => setSelectedPath(node.path)}
            onContextMenu={(e) => openCtx(e, node)}
          >
            <span className="check" onClick={(e) => e.stopPropagation()}>
              <input type="checkbox" checked={checked.includes(node.path)} onChange={() => toggleCheck(node.path)} />
            </span>
            <span className="carat" onClick={(e) => { e.stopPropagation(); toggleExpand(node.path); }}>
              <span className="caret">{isOpen ? "▾" : "▸"}</span>
            </span>
            <span className="icon">{isOpen ? "📂" : "📁"}</span>
            <span className="name">{node.name}</span>
            {node.import_status === 1 && <span className="badge imported">已导入</span>}
            <span className="actions">
              <button title="上传文件到此" onClick={(e) => { e.stopPropagation(); setSelectedPath(node.path); fileInput.current?.click(); }}>📄</button>
              <button title="移动" onClick={(e) => { e.stopPropagation(); openMove(node); }}>↔️</button>
              <button title="重命名" onClick={(e) => { e.stopPropagation(); openRename(node); }}>✏️</button>
              <button title="删除" onClick={(e) => { e.stopPropagation(); setConfirmNode(node); }}>🗑</button>
            </span>
          </div>
          {isOpen && node.children?.map((child) => renderNode(child, depth + 1))}
        </div>
      );
    }
    return (
      <div
        key={node.path}
        className="tree-row file"
        style={pad}
        onClick={() => onSelectFile?.(node)}
        onContextMenu={(e) => openCtx(e, node)}
      >
        <span className="check" onClick={(e) => e.stopPropagation()}>
          <input type="checkbox" checked={checked.includes(node.path)} onChange={() => toggleCheck(node.path)} />
        </span>
        <span className="icon">📄</span>
        <span className="name">{node.name}</span>
        {node.import_status === 1 && <span className="badge imported">已导入</span>}
        <span className="actions">
          <button title="移动" onClick={(e) => { e.stopPropagation(); openMove(node); }}>↔️</button>
          <button title="重命名" onClick={(e) => { e.stopPropagation(); openRename(node); }}>✏️</button>
          <button title="删除" onClick={(e) => { e.stopPropagation(); setConfirmNode(node); }}>🗑</button>
        </span>
      </div>
    );
  }

  return (
    <div className="import-zone">
      <div className="toolbar-wrap">
        <div className="toolbar">
          <button onClick={() => fileInput.current?.click()}>📄 上传文件</button>
          <button onClick={() => setClipOpen(true)} title="粘贴网页 URL，转 Markdown 存入导入区">🔗 剪藏网页</button>
          <button onClick={load}>🔄 刷新</button>
          <button className="write-btn" disabled={!checked.length || writing} onClick={handleWrite}>
            📥 写入知识库{checked.length ? `（${checked.length}）` : ""}
          </button>
          <input
            ref={fileInput}
            type="file"
            multiple
            style={{ display: "none" }}
            onChange={onPickFiles}
          />
          <span className="tip">勾选文件/文件夹后写入知识库 · 目标：{selectedPath || "根目录"}</span>
        </div>

        {error && <div className="msg error">{error}</div>}
        {notice && <div className="msg info">{notice}</div>}

        {writeResult && (
          <div className="write-result">
            <div className="write-summary">
              写入结果：新增 {writeResult.summary.written} · 跳过 {writeResult.summary.skipped} · 失败{" "}
              {writeResult.summary.failed}
              <button className="ghost" onClick={() => setWriteResult(null)}>✕ 关闭</button>
            </div>
            <div className="write-list">{writeResult.results.map((r) => renderWriteItem(r))}</div>
          </div>
        )}
      </div>

      <div
        className="tree"
        onClick={(e) => { if (e.target === e.currentTarget) setSelectedPath(""); }}
        onContextMenu={(e) => openCtx(e, null)}
      >
        <div className="tree-title">项目文件（原始导入区）</div>
        {!tree ? (
          <div className="empty">加载中…</div>
        ) : tree.children?.length ? (
          tree.children.map((child) => renderNode(child, 1))
        ) : (
          <div className="empty">导入区为空，请在空白处右键新建文件夹，或上传文件。</div>
        )}
      </div>

      <div className="footer-info">
        当前目标目录：<code>{selectedPath || "/"}</code> · 右键任意文件/文件夹执行操作
      </div>

      {/* 右键菜单 */}
      {ctx && (
        <div className="ctx-menu" style={{ left: ctx.x, top: ctx.y }} onClick={(e) => e.stopPropagation()}>
          {(!ctx.node || ctx.node.is_dir) && (
            <>
              <button onClick={() => { setCtx(null); openCreate(ctx.node?.is_dir ? ctx.node.path : selectedPath); }}>
                📁 新建文件夹
              </button>
              <button onClick={() => { setCtx(null); setSelectedPath(ctx.node?.is_dir ? ctx.node.path : selectedPath); fileInput.current?.click(); }}>
                📄 上传文件到此
              </button>
              {!ctx.node && <button onClick={() => { setCtx(null); load(); }}>🔄 刷新</button>}
            </>
          )}
          {ctx.node && (
            <>
              <button onClick={() => { setCtx(null); openMove(ctx.node!); }}>↔️ 移动到…</button>
              <button onClick={() => { setCtx(null); openRename(ctx.node!); }}>✏️ 重命名</button>
              <button className="danger" onClick={() => { setCtx(null); setConfirmNode(ctx.node); }}>🗑 删除</button>
            </>
          )}
        </div>
      )}

      {/* 输入弹窗（新建/重命名） */}
      {modal && (
        <div className="modal-overlay" onClick={cancelModal}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-title">
              {modal.kind === "create" ? "新建文件夹" : "重命名"}
              {modal.parent && <span className="modal-sub"> 目标：{modal.parent}</span>}
            </div>
            <input
              autoFocus
              className="modal-input"
              value={inputVal}
              placeholder={modal.kind === "create" ? "请输入文件夹名称" : "请输入新名称"}
              onChange={(e) => setInputVal(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submitModal();
                if (e.key === "Escape") cancelModal();
              }}
            />
            <div className="modal-actions">
              <button className="ghost" onClick={cancelModal}>取消</button>
              <button className="primary" onClick={submitModal}>确定</button>
            </div>
          </div>
        </div>
      )}

      {/* 移动到…… 目录选择弹窗 */}
      {moveState && (
        <div className="modal-overlay" onClick={cancelMove}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-title">
              移动 «{moveState.source.name}» 到…
            </div>
            <div className="dir-picker">
              {moveDirs().map((d) => (
                <div
                  key={"dir:" + d.path}
                  className={`dir-option ${moveTarget === d.path ? "active" : ""}`}
                  style={{ paddingLeft: `${d.depth * 18 + 8}px` }}
                  onClick={() => setMoveTarget(d.path)}
                >
                  📁 {d.name || "/"}
                </div>
              ))}
            </div>
            <div className="modal-actions">
              <button className="ghost" onClick={cancelMove}>取消</button>
              <button className="primary" disabled={moveTarget === null} onClick={submitMove}>移动</button>
            </div>
          </div>
        </div>
      )}

      {/* 删除确认 */}
      {confirmNode && (
        <div className="modal-overlay" onClick={() => setConfirmNode(null)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-title">删除确认</div>
            <div className="modal-body">
              确认删除 <code>{confirmNode.name}</code> ?
              {confirmNode.is_dir && <div className="modal-sub warn">将递归删除其全部内容，不可恢复。</div>}
            </div>
            <div className="modal-actions">
              <button className="ghost" onClick={() => setConfirmNode(null)}>取消</button>
              <button className="danger" onClick={confirmDelete}>删除</button>
            </div>
          </div>
        </div>
      )}

      {/* 网页剪藏弹窗（FR-12） */}
      {clipOpen && (
        <div className="modal-overlay" onClick={() => { setClipOpen(false); setClipUrl(""); }}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-title">
              网页剪藏
              {selectedPath && <span className="modal-sub"> 目标目录：{selectedPath}</span>}
            </div>
            <input
              autoFocus
              className="modal-input"
              value={clipUrl}
              placeholder="粘贴网页 URL（http/https）"
              onChange={(e) => setClipUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submitClip();
                if (e.key === "Escape") { setClipOpen(false); setClipUrl(""); }
              }}
            />
            <div className="modal-sub" style={{ margin: "8px 0 0" }}>
              将自动提取网页正文并转为 Markdown，保存到导入区。
            </div>
            <div className="modal-actions">
              <button className="ghost" onClick={() => { setClipOpen(false); setClipUrl(""); }}>取消</button>
              <button className="primary" disabled={!clipUrl.trim() || clipping} onClick={submitClip}>
                {clipping ? "剪藏中…" : "剪藏"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 阻塞式加载提示：导入文档 / 写入知识库（未完成前禁止其他操作） */}
      <LoadingOverlay show={uploading} message="正在导入文档…" />
      <LoadingOverlay show={writing} message="正在写入知识库…" />
    </div>
  );
}