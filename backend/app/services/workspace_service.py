"""工作区服务：个人工作区初始化 + 组工作区（阶段八批次3）创建、邀请码、成员管理。"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import User, Workspace, WorkspaceInvite, WorkspaceMember

log = get_logger("services.workspace")

# 邀请码生命周期：5 分钟有效，过期作废需重新生成
INVITE_TTL_SECONDS = 300
# 随机短码字符集（去掉易混淆字符，减少抄录错误）
INVITE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class WorkspaceService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ---------- 个人工作区 ----------
    def ensure_personal(self, user: User) -> Workspace:
        """返回/创建该用户的 type=personal 工作区（注册时自动拥有）。"""
        ws = (
            self.db.query(Workspace)
            .filter(Workspace.creator_id == user.id, Workspace.type == "personal")
            .first()
        )
        if ws is not None:
            # 确保创建者本身在成员表中（兜底）
            if not self._is_member(ws.id, user.id):
                self.db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
                self.db.commit()
            return ws
        ws = Workspace(
            name=f"{user.display_name or user.username} 的个人空间",
            type="personal",
            creator_id=user.id,
            description="个人空间",
        )
        self.db.add(ws)
        self.db.flush()
        self.db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
        self.db.commit()
        log.info("已为用户 %s 创建个人工作区 %s", user.username, ws.id)
        return ws

    def get(self, workspace_id: int) -> Workspace | None:
        return self.db.query(Workspace).filter(Workspace.id == workspace_id).first()

    def _is_member(self, workspace_id: int, user_id: int) -> bool:
        return (
            self.db.query(WorkspaceMember)
            .filter(WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user_id)
            .first() is not None
        )

    def member(self, workspace_id: int, user_id: int) -> WorkspaceMember | None:
        return (
            self.db.query(WorkspaceMember)
            .filter(WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user_id)
            .first()
        )

    # ---------- 组工作区（阶段八批次3） ----------
    def create_group(self, name: str, creator: User, description: str | None = None) -> Workspace:
        """创建组工作区（type=group）并让创建者以 owner 身份成为成员。"""
        if not (name and name.strip()):
            raise ValueError("组名称不能为空")
        ws = Workspace(
            name=name.strip(),
            type="group",
            creator_id=creator.id,
            description=description,
        )
        self.db.add(ws)
        self.db.flush()
        self.db.add(WorkspaceMember(workspace_id=ws.id, user_id=creator.id, role="owner"))
        self.db.commit()
        log.info("创建组工作区 %s（creator=%s）", ws.id, creator.username)
        return ws

    def list_user_workspaces(self, user_id: int) -> list[Workspace]:
        """当前用户作为成员参与的所有工作区（个人 + 组）。"""
        rows = (
            self.db.query(Workspace)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .filter(WorkspaceMember.user_id == user_id)
            .order_by(Workspace.type.desc(), Workspace.id.asc())
            .all()
        )
        return rows

    def list_members(self, workspace_id: int) -> list[WorkspaceMember]:
        return (
            self.db.query(WorkspaceMember)
            .filter(WorkspaceMember.workspace_id == workspace_id)
            .all()
        )

    def get_invite(self, workspace_id: int, creator: User) -> WorkspaceInvite:
        """creator 生成/刷新邀请码（每次调用作废旧码，生成新码）。

        权限：仅组创建者可生成邀请码（非 creator → ValueError）。
        """
        ws = self.get(workspace_id)
        if ws is None or ws.type != "group":
            raise LookupError("组不存在")
        if ws.creator_id != creator.id:
            raise PermissionError("仅组的创建者可生成邀请码")
        # 作废旧码（同一组只保留一个有效邀请码）
        self.db.query(WorkspaceInvite).filter(
            WorkspaceInvite.workspace_id == workspace_id).delete()
        code = "".join(secrets.choice(INVITE_CODE_ALPHABET)
                       for _ in range(8))
        inv = WorkspaceInvite(
            workspace_id=workspace_id,
            creator_id=creator.id,
            code=code,
            expires_at=datetime.now() + timedelta(seconds=INVITE_TTL_SECONDS),
        )
        self.db.add(inv)
        self.db.commit()
        return inv

    def redeem_invite(self, code: str, user: User) -> Workspace:
        """受邀用户凭码加入成为组员（member）。

        校验：码存在、未过期、未使用。用后即失效（原子标记 used_by/used_at）。
        若已是成员则幂等返回；非 group 空间拒绝。
        """
        code = (code or "").strip().upper()
        inv = self.db.query(WorkspaceInvite).filter(WorkspaceInvite.code == code).first()
        if inv is None:
            raise LookupError("邀请码不存在")
        now = datetime.now()
        if inv.used_by is not None or inv.used_at is not None:
            raise LookupError("邀请码已被使用")
        if inv.expires_at < now:
            raise LookupError("邀请码已过期，请让创建者重新生成")
        ws = self.get(inv.workspace_id)
        if ws is None or ws.type != "group":
            raise LookupError("邀请码对应的组不存在")
        if self._is_member(ws.id, user.id):
            return ws
        self.db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="member"))
        inv.used_by = user.id
        inv.used_at = now
        self.db.commit()
        log.info("用户 %s 凭邀请码加入组 %s", user.username, ws.id)
        return ws

    def leave_group(self, workspace_id: int, user: User) -> None:
        """成员主动退出组。仅普通成员可退出；owner（组的创建者）不可直接退出（需先转让/解散）。"""
        me = self.member(workspace_id, user.id)
        if me is None:
            raise LookupError("你不在该工作区")
        ws = self.get(workspace_id)
        if ws is not None and ws.creator_id == user.id:
            raise PermissionError("创建者不能退出自己的组，请先转让或解散")
        self.db.delete(me)
        self.db.commit()
        log.info("用户 %s 退出组 %s", user.username, workspace_id)

    def remove_member(self, workspace_id: int, creator: User, user_id: int) -> None:
        """创建者移除某个成员（被移除者失去该组空间访问权）。"""
        ws = self.get(workspace_id)
        if ws is None or ws.type != "group":
            raise LookupError("组不存在")
        if ws.creator_id != creator.id:
            raise PermissionError("仅组的创建者可移除成员")
        me = self.member(workspace_id, user_id)
        if me is None:
            raise LookupError("该用户不是组成员")
        if user_id == creator.id:
            raise PermissionError("创建者不能移除自己")
        self.db.delete(me)
        self.db.commit()
        log.info("创建者 %s 移除成员 %s（组 %s）", creator.username, user_id, workspace_id)

    # ---------- 组工作区（阶段八批次2b：编辑/转让/解散） ----------
    def update_group(self, workspace_id: int, user: User,
                     name: str | None = None, description: str | None = None) -> Workspace:
        """编辑组信息（组名/描述）。仅组的创建者可修改。"""
        ws = self.get(workspace_id)
        if ws is None or ws.type != "group":
            raise LookupError("组不存在")
        if ws.creator_id != user.id:
            raise PermissionError("仅组的创建者可修改组信息")
        if name is not None:
            if not (name and name.strip()):
                raise ValueError("组名称不能为空")
            ws.name = name.strip()
        if description is not None:
            ws.description = description
        self.db.commit()
        log.info("组 %s 信息已更新（creator=%s）", workspace_id, user.username)
        return ws

    def transfer_group(self, workspace_id: int, user: User, new_owner_id: int) -> Workspace:
        """转让组所有权给组内某成员。仅当前创建者（owner）可转让。

        转让后：目标成员 role→owner、workspace.creator_id→目标；原创建者降为普通成员；
        组内既有邀请码一并作废（由新创建者重新生成）。
        """
        ws = self.get(workspace_id)
        if ws is None or ws.type != "group":
            raise LookupError("组不存在")
        if ws.creator_id != user.id:
            raise PermissionError("仅组的创建者可转让")
        if new_owner_id == user.id:
            raise ValueError("不能转让给自己")
        target = self.member(workspace_id, new_owner_id)
        if target is None:
            raise LookupError("目标用户在转让前需先加入该组")
        cur = self.member(workspace_id, ws.creator_id)
        if cur is not None:
            cur.role = "member"
        target.role = "owner"
        ws.creator_id = target.user_id
        # 所有权变更后作废旧邀请码（仅新 owner 可重新发码）
        self.db.query(WorkspaceInvite).filter(
            WorkspaceInvite.workspace_id == workspace_id).delete()
        self.db.commit()
        log.info("组 %s 所有权已由 %s 转让给 user=%s", workspace_id, user.username, new_owner_id)
        return ws

    def delete_group(self, workspace_id: int, user: User) -> None:
        """解散组（仅创建者）：级联解除全部成员关系、作废邀请码，并删除工作区。

        注意：组内已共享到知识库/图谱/导入区的数据按其 workspace_id 保留（成为不可达数据），
        不在此处级联物理删除，以避免误删共享内容；如需清理数据请另走单条删除接口。
        """
        ws = self.get(workspace_id)
        if ws is None or ws.type != "group":
            raise LookupError("组不存在")
        if ws.creator_id != user.id:
            raise PermissionError("仅组的创建者可解散组")
        self.db.query(WorkspaceMember).filter(
            WorkspaceMember.workspace_id == workspace_id).delete()
        self.db.query(WorkspaceInvite).filter(
            WorkspaceInvite.workspace_id == workspace_id).delete()
        self.db.delete(ws)
        self.db.commit()
        log.info("组 %s 已解散（creator=%s）", workspace_id, user.username)