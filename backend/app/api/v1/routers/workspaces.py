"""工作区/组/邀请码路由（阶段八批次3）。

本批次聚焦：创建组（type=group，作为邀请码载体）、当前用户工作区列表、
组信息与成员、邀请码生成（仅 creator）与凭码加入。
组信息查看仅限组内成员；邀请码生成仅限组的创建者。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user
from app.db.engine import get_db
from app.db.models import User, Workspace, WorkspaceMember
from app.schemas.auth import WorkspaceInfo, WorkspaceListResponse
from app.schemas.workspace import (
    GroupCreateRequest,
    GroupUpdateRequest,
    InviteResponse,
    JoinRequest,
    MemberInfo,
    TransferRequest,
    WorkspaceDetailResponse,
)
from app.services.workspace_service import WorkspaceService

router = APIRouter()


def _ws_info(ws: Workspace, role: str | None = None) -> WorkspaceInfo:
    return WorkspaceInfo(id=ws.id, name=ws.name, type=ws.type, role=role)


@router.get("/workspaces", response_model=WorkspaceListResponse, summary="当前用户参与的所有工作区")
def list_workspaces(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    svc = WorkspaceService(db)
    wss = svc.list_user_workspaces(user.id)
    # 附带当前用户在每工作区的角色（owner/member），供前端列表展示与权限判断
    roles = {
        ws_id: role for (ws_id, role) in db.query(WorkspaceMember.workspace_id, WorkspaceMember.role)
        .filter(WorkspaceMember.user_id == user.id).all()
    }
    return WorkspaceListResponse(workspaces=[_ws_info(w, roles.get(w.id)) for w in wss])


@router.post("/workspaces", response_model=WorkspaceInfo, status_code=201, summary="创建组工作区")
def create_group(body: GroupCreateRequest, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    try:
        ws = WorkspaceService(db).create_group(body.name, user, body.description)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _ws_info(ws, role="owner")


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceDetailResponse,
            summary="查看组信息与成员列表")
def group_detail(workspace_id: int, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    svc = WorkspaceService(db)
    me = svc.member(workspace_id, user.id)
    if me is None:
        raise HTTPException(status_code=404, detail="工作区不存在或无权限访问")
    ws = svc.get(workspace_id)
    members = svc.list_members(workspace_id)
    return WorkspaceDetailResponse(
        id=ws.id, name=ws.name, type=ws.type, creator_id=ws.creator_id,
        description=ws.description, created_at=ws.created_at,
        members=[_member_info(m) for m in members],
        role=me.role,
    )


@router.put("/workspaces/{workspace_id}", response_model=WorkspaceDetailResponse,
            summary="编辑组信息（组名/描述，仅创建者）")
def update_group(workspace_id: int, body: GroupUpdateRequest,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    svc = WorkspaceService(db)
    try:
        svc.update_group(workspace_id, user, body.name, body.description)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return group_detail(workspace_id, user, db)


@router.post("/workspaces/{workspace_id}/transfer", response_model=WorkspaceDetailResponse,
             summary="转让组所有权给组内成员（仅创建者）")
def transfer_group(workspace_id: int, body: TransferRequest,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    svc = WorkspaceService(db)
    try:
        svc.transfer_group(workspace_id, user, body.new_owner_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return group_detail(workspace_id, user, db)


@router.delete("/workspaces/{workspace_id}", summary="解散组（仅创建者），级联解除成员")
def delete_group(workspace_id: int, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    svc = WorkspaceService(db)
    try:
        svc.delete_group(workspace_id, user)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"ok": True}


@router.get("/workspaces/{workspace_id}/invite", response_model=InviteResponse,
            summary="生成/刷新组邀请码（仅创建者）")
def get_invite(workspace_id: int, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    svc = WorkspaceService(db)
    try:
        inv = svc.get_invite(workspace_id, user)
    except (LookupError, PermissionError) as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return InviteResponse(workspace_id=inv.workspace_id, code=inv.code, expires_at=inv.expires_at)


@router.post("/workspaces/join", response_model=WorkspaceInfo, summary="凭邀请码加入组")
def join_group(body: JoinRequest, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    svc = WorkspaceService(db)
    try:
        ws = svc.redeem_invite(body.code, user)
    except LookupError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    me = svc.member(ws.id, user.id)
    return _ws_info(ws, role=me.role if me else None)


@router.post("/workspaces/{workspace_id}/leave", summary="成员退出组")
def leave_group(workspace_id: int, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    svc = WorkspaceService(db)
    try:
        svc.leave_group(workspace_id, user)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return {"ok": True}


@router.post("/workspaces/{workspace_id}/members/{user_id}/remove", summary="创建者移除成员")
def remove_member(workspace_id: int, user_id: int, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    svc = WorkspaceService(db)
    try:
        svc.remove_member(workspace_id, user, user_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return {"ok": True}


def _member_info(m: WorkspaceMember) -> MemberInfo:
    return MemberInfo(user_id=m.user_id, username=m.user.username if m.user else None,
                      display_name=m.user.display_name if m.user else None, role=m.role)