from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import WorkspaceAccess, get_session, workspace_access
from ..models import AuditEvent
from ..schemas import AuditEventRead

router = APIRouter(prefix="/v1/workspaces/{workspace_id}", tags=["audit"])


@router.get("/audit-events", response_model=list[AuditEventRead])
async def audit_events(
    access: WorkspaceAccess = Depends(workspace_access),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AuditEventRead]:
    events = (
        (
            await session.execute(
                select(AuditEvent)
                .where(AuditEvent.workspace_id == access.workspace.id)
                .order_by(AuditEvent.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [AuditEventRead.model_validate(event) for event in events]
