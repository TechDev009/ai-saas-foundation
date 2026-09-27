import re
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import (
    WorkspaceAccess,
    get_session,
    protected_user,
    require_role,
    workspace_access,
)
from ..models import Membership, User, Workspace
from ..schemas import MemberAdd, MembershipRead, UserRead, WorkspaceCreate, WorkspaceRead
from ..services import record_audit

router = APIRouter(prefix="/v1/workspaces", tags=["workspaces"])


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value[:120] or "workspace"


@router.post("", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreate,
    request: Request,
    user: User = Depends(protected_user),
    session: AsyncSession = Depends(get_session),
) -> WorkspaceRead:
    base = _slug(payload.slug or payload.name)
    slug = base
    while await session.scalar(select(Workspace.id).where(Workspace.slug == slug)) is not None:
        slug = f"{base}-{uuid4().hex[:8]}"
    workspace = Workspace(id=str(uuid4()), name=payload.name.strip(), slug=slug)
    session.add(workspace)
    await session.flush()
    session.add(Membership(workspace_id=workspace.id, user_id=user.id, role="owner"))
    await record_audit(
        session,
        workspace_id=workspace.id,
        user_id=user.id,
        action="workspace.created",
        resource_type="workspace",
        resource_id=workspace.id,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return WorkspaceRead.model_validate(workspace)


@router.get("", response_model=list[WorkspaceRead])
async def list_workspaces(
    user: User = Depends(protected_user),
    session: AsyncSession = Depends(get_session),
) -> list[WorkspaceRead]:
    workspaces = (
        (
            await session.execute(
                select(Workspace)
                .join(Membership, Membership.workspace_id == Workspace.id)
                .where(Membership.user_id == user.id, Workspace.deleted_at.is_(None))
                .order_by(Workspace.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [WorkspaceRead.model_validate(workspace) for workspace in workspaces]


@router.get("/{workspace_id}", response_model=WorkspaceRead)
async def get_workspace(access: WorkspaceAccess = Depends(workspace_access)) -> WorkspaceRead:
    return WorkspaceRead.model_validate(access.workspace)


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    request: Request,
    access: WorkspaceAccess = Depends(workspace_access),
    session: AsyncSession = Depends(get_session),
) -> None:
    require_role(access, "owner")
    access.workspace.deleted_at = datetime.now(UTC)
    await record_audit(
        session,
        workspace_id=access.workspace.id,
        user_id=access.user.id,
        action="workspace.deleted",
        resource_type="workspace",
        resource_id=access.workspace.id,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()


@router.get("/{workspace_id}/members", response_model=list[MembershipRead])
async def list_members(
    access: WorkspaceAccess = Depends(workspace_access),
    session: AsyncSession = Depends(get_session),
) -> list[MembershipRead]:
    rows = (
        await session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.workspace_id == access.workspace.id)
            .order_by(Membership.created_at)
        )
    ).all()
    return [
        MembershipRead(
            workspace_id=membership.workspace_id,
            user_id=membership.user_id,
            role=membership.role,
            created_at=membership.created_at,
            user=UserRead.model_validate(user),
        )
        for membership, user in rows
    ]


@router.post(
    "/{workspace_id}/members", response_model=MembershipRead, status_code=status.HTTP_201_CREATED
)
async def add_member(
    payload: MemberAdd,
    request: Request,
    access: WorkspaceAccess = Depends(workspace_access),
    session: AsyncSession = Depends(get_session),
) -> MembershipRead:
    require_role(access, "owner", "admin")
    user = await session.scalar(
        select(User).where(User.email == payload.email, User.is_active.is_(True))
    )
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    existing = await session.scalar(
        select(Membership).where(
            Membership.workspace_id == access.workspace.id,
            Membership.user_id == user.id,
        )
    )
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="user is already a member")
    membership = Membership(workspace_id=access.workspace.id, user_id=user.id, role=payload.role)
    session.add(membership)
    await record_audit(
        session,
        workspace_id=access.workspace.id,
        user_id=access.user.id,
        action="membership.created",
        resource_type="membership",
        resource_id=user.id,
        request_id=getattr(request.state, "request_id", None),
        metadata={"role": payload.role},
    )
    await session.commit()
    return MembershipRead(
        workspace_id=membership.workspace_id,
        user_id=membership.user_id,
        role=membership.role,
        created_at=membership.created_at,
        user=UserRead.model_validate(user),
    )
