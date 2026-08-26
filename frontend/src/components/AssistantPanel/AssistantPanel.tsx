import { useEffect, useRef, useState } from "react";
import * as assistantApi from "../../api/assistant";
import type { AssistantSession, ChatMessageItem, Citation } from "../../types";
import "./AssistantPanel.css";

interface AssistantPanelProps {
  onClose: () => void;
}

const DEFAULT_WIDTH = 380;
const MIN_WIDTH = 320;
const MAX_WIDTH = 760;

type ToolKind = "md" | null;

export default function AssistantPanel({ onClose }: AssistantPanelProps) {
  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const [sessions, setSessions] = useState<AssistantSession[]>([]);
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessageItem[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [streamBuf, setStreamBuf] = useState("");
  const [streamCites, setStreamCites] = useState<Citation[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [tool, setTool] = useState<ToolKind>(null);
  const [toolValue, setToolValue] = useState("");
  const [runningTool, setRunningTool] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [showList, setShowList] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const bufRef = useRef("");
  const citesRef = useRef<Citation[]>([]);

  async function refreshSessions(selectId?: number) {
    try {
      const res = await assistantApi.listSessions();
      setSessions(res.sessions);
      if (selectId != null) {
        setSessionId(selectId);
        await loadMessages(selectId);
      } else if (res.sessions.length && sessionId == null) {
        setSessionId(res.sessions[0].id);
        await loadMessages(res.sessions[0].id);
      } else {
        setMessages([]);
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function loadMessages(id: number) {
    setError("");
    try {
      const res = await assistantApi.listMessages(id);
      setMessages(res.messages);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    refreshSessions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messages, streamBuf]);

  function newSession() {
    setSessionId(null);
    setMessages([]);
    setStreamBuf("");
    setShowList(false);
    setNotice("已新建会话（发送消息后自动创建）");
  }

  function selectSession(id: number) {
    setSessionId(id);
    setStreamBuf("");
    setRenaming(false);
    setShowList(false);
    setError("");
    setNotice("");
    loadMessages(id);
  }

  function startRename() {
    if (sessionId == null) return;
    setRenameValue(sessions.find((s) => s.id === sessionId)?.title || "");
    setRenaming(true);
  }

  function cancelRename() {
    setRenaming(false);
  }

  async function confirmRename() {
    const v = renameValue.trim();
    if (sessionId == null || !v) return;
    setError("");
    try {
      await assistantApi.renameSession(sessionId, v);
      setRenaming(false);
      await refreshSessions(sessionId);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function removeSession(id: number) {
    if (!window.confirm("确定删除该会话及其全部消息？")) return;
    setError("");
    try {
      await assistantApi.deleteSession(id);
      setRenaming(false);
      setStreamBuf("");
      setNotice("会话已删除");
      // 当前正查看的会话被删 → 回到首会话/空
      if (id === sessionId) {
        setSessionId(null);
        setMessages([]);
      }
      await refreshSessions();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function handleDelete() {
    if (sessionId == null) return;
    await removeSession(sessionId);
  }

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setError("");
    setNotice("");
    setStreamBuf("");
    setStreamCites([]);
    bufRef.current = "";
    citesRef.current = [];
    setBusy(true);
    // 先本地回显用户消息
    setMessages((prev) => [
      ...prev,
      { id: -Date.now(), role: "user", content: text, citations: null, created_at: "" },
    ]);
    const currentSession = sessionId;
    try {
      await assistantApi.streamChat(
        { session_id: currentSession, message: text },
        (ev) => {
          if (ev.type === "delta") {
            bufRef.current += ev.content;
            setStreamBuf(bufRef.current);
          } else if (ev.type === "stage") {
            setNotice(ev.text);
          } else if (ev.type === "citations") {
            citesRef.current = ev.citations;
            setStreamCites(ev.citations);
          } else if (ev.type === "done") {
            if (ev.session_id != null) setSessionId(ev.session_id);
          }
        },
      );
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
      return;
    }
    setBusy(false);
    // 结束流式，加入消息列表
    setMessages((prev) => [
      ...prev.filter((m) => m.id >= 0 || m.role === "user"),
      {
        id: -1,
        role: "assistant",
        content: bufRef.current || "（无内容）",
        citations: citesRef.current.length ? citesRef.current : null,
        created_at: "",
      },
    ]);
    setStreamBuf("");
    setStreamCites([]);
    bufRef.current = "";
    citesRef.current = [];
    setNotice("");
    await refreshSessions(sessionId ?? undefined);
  }

  // ---------- 工具：出题 / 检索导入区 / 生成 md ----------
  function openTool(kind: ToolKind) {
    setTool(tool === kind ? null : kind);
    setToolValue("");
  }

  async function runTool() {
    if (!tool || runningTool) return;
    setRunningTool(true);
    setError("");
    setNotice("");
    const value = toolValue.trim();
    try {
      if (tool === "md") {
        const digest = messages
          .map((m) => `${m.role === "user" ? "用户" : "助手"}：${m.content}`)
          .join("\n\n")
          .slice(-12000);
        const title = value || undefined;
        const res = await assistantApi.generateMd({ content: digest || "(空会话)", title });
        setNotice(`已生成 md：${res.path}`);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunningTool(false);
      setTool(null);
      setToolValue("");
    }
  }

  function onStartDrag(e: React.PointerEvent<HTMLDivElement>) {
    const startX = e.clientX;
    const startW = width;
    const move = (ev: PointerEvent) => {
      const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startW - (ev.clientX - startX)));
      setWidth(next);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  function renderMessage(m: ChatMessageItem) {
    const isLite = m.id === -1 || m.id === -Date.now();
    return (
      <div key={m.id} className={`msg-bubble ${m.role === "user" ? "user" : "assistant"}`}>
        {m.role === "user" ? (
          <div className="bubble-text user-text">{m.content}</div>
        ) : (
          <>
            <div className="bubble-text">{m.content}</div>
            {m.citations && m.citations.length > 0 && (
              <div className="citations">
                {m.citations.map((c) => (
                  <span className="cite" key={c.idx} title={c.note_path || c.title}>
                    来源{c.idx}·{c.title}
                  </span>
                ))}
              </div>
            )}
          </>
        )}
        {isLite && <span className="lite-flag">●</span>}
      </div>
    );
  }

  const currentTitle =
    sessions.find((s) => s.id === sessionId)?.title ||
    (sessionId == null ? "＋ 新会话（暂存）" : "会话已删除");

  return (
    <div className="assistant-panel" style={{ width }}>
      <div className="resize-handle" onPointerDown={onStartDrag} title="拖拽调整宽度" />
      <div className="assistant-head">
        <span className="assistant-title">🧠 AI 助手</span>
        <div className="assistant-head-actions">
          <button className="ghost" onClick={newSession} title="新会话">
            ＋新建
          </button>
          <button className="ghost" onClick={onClose} title="收起">
            ✕
          </button>
        </div>
      </div>

      <div className="session-bar">
        <button
          className="session-current"
          onClick={() => setShowList((v) => !v)}
          title={showList ? "收起会话列表" : "展开会话列表，切换或删除会话"}
        >
          <span className="cur-title">{currentTitle}</span>
          <span className="chevron">{showList ? "▲" : "▼"}</span>
        </button>
        <span className="session-ops">
          {sessionId != null && (
            <>
              <button
                className="ghost"
                onClick={renaming ? cancelRename : startRename}
                title={renaming ? "取消改名" : "重命名会话"}
              >
                {renaming ? "✕" : "✏️"}
              </button>
              <button className="ghost danger" onClick={handleDelete} title="删除当前会话">
                🗑️
              </button>
            </>
          )}
        </span>
      </div>

      {showList && (
        <div className="session-list">
          {sessions.length === 0 && <div className="session-empty">暂无会话，点击右上角「＋新建」</div>}
          {sessions.map((s) => (
            <div
              key={s.id}
              className={`session-item${s.id === sessionId ? " active" : ""}`}
              onClick={() => selectSession(s.id)}
              title={s.title}
            >
              <span className="item-title">{s.title}</span>
              <button
                className="item-del"
                onClick={(e) => {
                  e.stopPropagation();
                  removeSession(s.id);
                }}
                title="删除该会话"
              >
                🗑
              </button>
            </div>
          ))}
        </div>
      )}
      {renaming && sessionId != null && (
        <div className="rename-row">
          <input
            autoFocus
            placeholder="会话名称"
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && confirmRename()}
          />
          <button className="primary" onClick={confirmRename}>
            确定
          </button>
        </div>
      )}

      <div className="assistant-tools">
        <button onClick={() => openTool("md")} disabled={busy || messages.length === 0}>
          💾 生成 md
        </button>
      </div>

      {tool && (
        <div className="tool-input">
          <input
            autoFocus
            placeholder="文件标题（可空）"
            value={toolValue}
            onChange={(e) => setToolValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runTool()}
            disabled={runningTool}
          />
          <button className="primary" onClick={runTool} disabled={runningTool}>
            {runningTool ? "处理中…" : "确定"}
          </button>
        </div>
      )}

      {(error || notice) && (
        <div className={`assistant-msg ${error ? "error" : "info"}`}>{error || notice}</div>
      )}

      <div className="assistant-list" ref={listRef}>
        {messages.map(renderMessage)}
        {streamBuf !== "" && (
          <div className="msg-bubble assistant streaming">
            <div className="bubble-text">{streamBuf}</div>
            {streamCites.length > 0 && (
              <div className="citations">
                {streamCites.map((c) => (
                  <span className="cite" key={c.idx}>
                    来源{c.idx}·{c.title}
                  </span>
                ))}
              </div>
            )}
            <span className="stream-cursor">▌</span>
          </div>
        )}
        {messages.length === 0 && streamBuf === "" && (
          <div className="assistant-empty">
            <p>你好！我是第二大脑助手。</p>
            <p>
              直接在下方输入即可：提问（自动检索知识库与图谱）、让我出题、查找导入区文件或让其以导入区内容作答、生成 md 总结。我会根据问题自动决定检索范围。
            </p>
          </div>
        )}
      </div>

      <div className="assistant-input">
        <textarea
          rows={2}
          placeholder="输入消息，Enter 发送，Shift+Enter 换行"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          disabled={busy}
        />
        <button className="primary" onClick={send} disabled={busy || !input.trim()}>
          发送
        </button>
      </div>
    </div>
  );
}