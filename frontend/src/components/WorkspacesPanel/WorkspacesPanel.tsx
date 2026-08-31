import { useCallback, useEffect, useMemo, useState } from "react";
import * as api from "../../api/workspaces";
import { getActiveWorkspace, setActiveWorkspace, clearActiveWorkspace } from "../../auth/workspaceStore";
import { useAuth } from "../../auth/AuthContext";
import type { WorkspaceDetail, WorkspaceInfo } from "../../types";
import "./WorkspacesPanel.css";

interface WorkspacesPanelProps {
  onClose: () => void;
}

function fmtCountdown(msLeft: number): string {
  if (msLeft <= 0) return "已过期";
  const total = Math.floor(msLeft / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export default function WorkspacesPanel({ onClose }: WorkspacesPanelProps) {
  const { user } = useAuth();
  const [workspaces, setWorkspaces] = useState<WorkspaceInfo[]>([]);
  const [active, setActive] = useState(getActiveWorkspace());
  const [detail, setDetail] = useState<WorkspaceDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  // 创建组
  const [createName, setCreateName] = useState("");
  const [createDesc, setCreateDesc] = useState("");
  // 加入组
  const [joinCode, setJoinCode] = useState("");
  // 邀请码
  const [invite, setInvite] = useState<{ code: string; expires_at: string } | null>(null);
  const [inviteLeft, setInviteLeft] = useState(0);
  // 改名
  const [editName, setEditName] = useState("");
  const [editDesc, setEditDesc] = useState("");

  const load = useCallback(async () => {
    try {
      const list = await api.listWorkspaces();
      setWorkspaces(list);
      setActive(getActiveWorkspace());
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 邀请码倒计时
  useEffect(() => {
    if (!invite) return;
    const tick = () =>
      setInviteLeft(new Date(invite.expires_at).getTime() - Date.now());
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [invite]);

  function switchTo(ws: WorkspaceInfo) {
    setActiveWorkspace({ id: ws.id, name: ws.name, type: ws.type });
    // 数据按工作区隔离，切区后整页刷新
    window.location.reload();
  }

  async function handleCreate() {
    if (!createName.trim()) {
      setError("请输入组名称");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.createGroup(createName.trim(), createDesc.trim() || undefined);
      setCreateName("");
      setCreateDesc("");
      setNotice("创建成功，可在列表中点击该组管理并生成邀请码");
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleJoin() {
    if (!joinCode.trim()) {
      setError("请输入邀请码");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const joined = await api.joinGroup(joinCode.trim());
      setJoinCode("");
      setNotice(`已加入「${joined.name}」`);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function openDetail(id: number) {
    setError("");
    setNotice("");
    setInvite(null);
    try {
      const d = await api.getWorkspace(id);
      setDetail(d);
      setEditName(d.name);
      setEditDesc(d.description ?? "");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function handleInvite() {
    if (!detail) return;
    setBusy(true);
    setError("");
    try {
      const inv = await api.getInvite(detail.id);
      setInvite(inv);
      setNotice("已生成邀请码（5 分钟内有效，刷新即作废旧码）");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function copyInvite() {
    if (!invite) return;
    void navigator.clipboard?.writeText(invite.code).then(() => setNotice("邀请码已复制"));
  }

  async function handleEdit() {
    if (!detail) return;
    setBusy(true);
    setError("");
    try {
      setDetail(await api.updateGroup(detail.id, editName.trim() || undefined, editDesc.trim() || undefined));
      setNotice("已保存组信息");
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleTransfer(memberId: number) {
    if (!detail) return;
    if (!window.confirm("确认将组所有权转让给该成员？原创建者将降为普通成员，且现有邀请码作废。")) return;
    setBusy(true);
    setError("");
    try {
      setDetail(await api.transferGroup(detail.id, memberId));
      setNotice("所有权已转让");
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove(memberId: number) {
    if (!detail) return;
    if (!window.confirm("确认移除该成员？被移除后将无法访问本组数据。")) return;
    setBusy(true);
    setError("");
    try {
      await api.removeMember(detail.id, memberId);
      setDetail(await api.getWorkspace(detail.id));
      setNotice("已移除成员");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleLeave() {
    if (!detail) return;
    if (!window.confirm("确认退出该组？")) return;
    await _postGroupAction(() => api.leaveGroup(detail.id), "已退出该组");
  }

  async function handleDissolve() {
    if (!detail) return;
    if (!window.confirm("确认解散该组？将级联解除全部成员关系且不可恢复。")) return;
    await _postGroupAction(() => api.deleteGroup(detail.id), detail.id === active.id ? "该组已解散" : "已解散该组");
  }

  async function _postGroupAction(action: () => Promise<void>, msg: string) {
    setBusy(true);
    setError("");
    try {
      await action();
      if (detail && detail.id === active.id) {
        // 退出/解散了当前活动组 → 回到个人工作区
        clearActiveWorkspace();
        window.location.reload();
        return;
      }
      setNotice(msg);
      setDetail(null);
      setInvite(null);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const isOwner = useMemo(() => Boolean(detail && detail.role === "owner"), [detail]);
  const isActive = (id: number) => active.id === id;

  return (
    <div className="wsp-panel">
      <div className="wsp-head">
        <span className="wsp-title">
          {detail ? <>📋 {detail.name} · 管理</> : <>🗂 工作区</>}
        </span>
        <div className="wsp-head-actions">
          {detail ? (
            <button className="ghost" onClick={() => { setDetail(null); setInvite(null); setNotice(""); }}>
              ← 返回列表
            </button>
          ) : null}
          <button className="ghost" onClick={onClose}>✕ 关闭</button>
        </div>
      </div>

      {error && <div className="wsp-msg error">{error}</div>}
      {notice && <div className="wsp-msg info">{notice}</div>}

      {!detail ? (
        <>
          {/* 创建组 */}
          <div className="wsp-section">
            <div className="wsp-section-title">创建组（组内共享同一工作区）</div>
            <div className="wsp-inline-form">
              <input
                className="wsp-input"
                placeholder="组名称"
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
              />
              <button className="primary" disabled={busy} onClick={handleCreate}>
                {busy ? "…" : "创建"}
              </button>
            </div>
            <input
              className="wsp-input"
              placeholder="可选：组描述"
              value={createDesc}
              onChange={(e) => setCreateDesc(e.target.value)}
            />
          </div>

          {/* 加入组 */}
          <div className="wsp-section">
            <div className="wsp-section-title">凭邀请码加入组</div>
            <div className="wsp-inline-form">
              <input
                className="wsp-input wsp-code-input"
                placeholder="输入 8 位邀请码"
                value={joinCode}
                onChange={(e) => setJoinCode(e.target.value)}
              />
              <button className="primary" disabled={busy} onClick={handleJoin}>
                {busy ? "…" : "加入"}
              </button>
            </div>
          </div>

          {/* 工作区列表 */}
          <div className="wsp-section">
            <div className="wsp-section-title">我的工作区（{workspaces.length}）</div>
            {workspaces.length === 0 ? (
              <div className="wsp-empty">暂无工作区。</div>
            ) : (
              <div className="wsp-list">
                {workspaces.map((ws) => (
                  <div key={ws.id} className={`wsp-item ${isActive(ws.id) ? "active" : ""}`}>
                    <span className={`wsp-type ${ws.type}`}>{ws.type === "group" ? "组" : "个人"}</span>
                    <div className="wsp-item-main">
                      <div className="wsp-item-name">{ws.name}</div>
                      <div className="wsp-item-sub">
                        {ws.role === "owner" ? "创建者" : ws.role === "member" ? "成员" : ""}
                        {isActive(ws.id) ? " · 当前" : ""}
                      </div>
                    </div>
                    <div className="wsp-item-actions">
                      <button
                        className="ghost"
                        disabled={isActive(ws.id)}
                        onClick={() => switchTo(ws)}
                        title="切换为活动工作区"
                      >
                        {isActive(ws.id) ? "✓ 当前" : "切换"}
                      </button>
                      {ws.type === "group" && (
                        <button className="ghost" onClick={() => void openDetail(ws.id)}>
                          管理
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      ) : (
        <>
          {/* 组详情 */}
          <div className="wsp-section">
            <div className="wsp-detail-title">
              <span className={`wsp-type group`}>组</span>
              {detail.name}
              <span className="wsp-role-chip">{isOwner ? "我是创建者" : "我是成员"}</span>
            </div>
            {detail.description && <div className="wsp-desc">{detail.description}</div>}

            {isOwner && (
              <div className="wsp-invite-box">
                <div className="wsp-section-title">邀请码（有效期 5 分钟）</div>
                {invite ? (
                  <>
                    <div className="wsp-invite-row">
                      <code className="wsp-invite-code">{invite.code}</code>
                      <span className={`wsp-invite-count ${inviteLeft <= 0 ? "expired" : ""}`}>
                        {inviteLeft <= 0 ? "已过期，请刷新" : fmtCountdown(inviteLeft)}
                      </span>
                    </div>
                    <div className="wsp-invite-actions">
                      <button className="primary" onClick={copyInvite}>复制</button>
                      <button className="ghost" disabled={busy} onClick={handleInvite}>刷新（作废旧码）</button>
                    </div>
                  </>
                ) : (
                  <button className="primary" disabled={busy} onClick={handleInvite}>
                    {busy ? "生成中…" : "生成邀请码"}
                  </button>
                )}
              </div>
            )}

            {/* 编辑组信息（仅创建者） */}
            {isOwner && (
              <div className="wsp-edit-box">
                <div className="wsp-section-title">修改组信息</div>
                <input className="wsp-input" placeholder="组名称" value={editName}
                  onChange={(e) => setEditName(e.target.value)} />
                <input className="wsp-input" placeholder="组描述（可选）" value={editDesc}
                  onChange={(e) => setEditDesc(e.target.value)} />
                <button className="primary" disabled={busy} onClick={handleEdit}>保存</button>
              </div>
            )}

            {/* 成员列表 */}
            <div className="wsp-section-title">成员（{detail.members.length}）</div>
            <div className="wsp-member-list">
              {detail.members.map((m) => (
                <div key={m.user_id} className="wsp-member">
                  <span className="wsp-member-name">
                    {(m.display_name || m.username || "成员")}
                    <span className="wsp-member-role">
                      {m.user_id === detail.creator_id ? "创建者" : m.role === "owner" ? "owner" : "成员"}
                    </span>
                  </span>
                  {m.user_id === user?.id && <span className="wsp-me">我</span>}
                  <div className="wsp-member-actions">
                    {isOwner && m.user_id !== user?.id && (
                      <>
                        <button
                          className="ghost"
                          disabled={busy}
                          title="转让所有权"
                          onClick={() => void handleTransfer(m.user_id)}
                        >
                          转让
                        </button>
                        <button
                          className="danger"
                          disabled={busy}
                          title="移除成员"
                          onClick={() => void handleRemove(m.user_id)}
                        >
                          移除
                        </button>
                      </>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* 危险操作 */}
          <div className="wsp-section">
            <div className="wsp-section-title">危险操作</div>
            <div className="wsp-danger-actions">
              {!isOwner && (
                <button className="danger" disabled={busy} onClick={handleLeave}>退出组</button>
              )}
              {isOwner && (
                <button className="danger" disabled={busy} onClick={handleDissolve}>解散组</button>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}