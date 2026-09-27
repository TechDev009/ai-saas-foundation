from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, HTTPException, Path, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user
from .models import Membership, User, Workspace


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency wrapper around the app-scoped async session factory."""
    factory = request.app.state.session_factory
    async with factory() as session:
        yield session


@dataclass(frozen=True)
class WorkspaceAccess:
    workspace: Workspace
    membership: Membership
    user: User


async def protected_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    user = await get_current_user(request, session)
    limiter = request.app.state.rate_limiter
    settings = request.app.state.settings
    allowed, retry_after = await limiter.check(
        f"user:{user.id}", settings.rate_limit_per_minute, window_seconds=60
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    return user


async def workspace_access(
    workspace_id: str = Path(..., min_length=1, max_length=36),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(protected_user),
) -> WorkspaceAccess:
    """Every workspace resource goes through this tenant authorization check."""
    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == workspace_id, Workspace.deleted_at.is_(None))
    )
    membership = await session.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user.id,
        )
    )
    if workspace is None or membership is None:
        # Deliberately do not reveal whether another tenant exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workspace not found")
    return WorkspaceAccess(workspace=workspace, membership=membership, user=user)


def require_role(access: WorkspaceAccess, *roles: Literal["owner", "admin", "member"]) -> None:
    if access.membership.role not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="insufficient workspace role"
        )
