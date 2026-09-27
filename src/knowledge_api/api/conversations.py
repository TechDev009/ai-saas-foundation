from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import WorkspaceAccess, get_session, workspace_access
from ..models import Conversation, Feedback, Message, UsageRecord
from ..schemas import (
    ConversationCreate,
    ConversationRead,
    FeedbackCreate,
    FeedbackRead,
    MessageCreate,
    MessageRead,
)
from ..services import new_id, record_audit

router = APIRouter(prefix="/v1/workspaces/{workspace_id}", tags=["conversations"])


async def _conversation_for_user(
    session: AsyncSession, workspace_id: str, user_id: str, conversation_id: str
) -> Conversation:
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.workspace_id == workspace_id,
            Conversation.user_id == user_id,
            Conversation.deleted_at.is_(None),
        )
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    return conversation


@router.post("/conversations", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate,
    request: Request,
    access: WorkspaceAccess = Depends(workspace_access),
    session: AsyncSession = Depends(get_session),
) -> ConversationRead:
    conversation = Conversation(
        id=new_id(),
        workspace_id=access.workspace.id,
        user_id=access.user.id,
        title=payload.title.strip(),
    )
    session.add(conversation)
    await record_audit(
        session,
        workspace_id=access.workspace.id,
        user_id=access.user.id,
        action="conversation.created",
        resource_type="conversation",
        resource_id=conversation.id,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return ConversationRead.model_validate(conversation)


@router.get("/conversations", response_model=list[ConversationRead])
async def list_conversations(
    access: WorkspaceAccess = Depends(workspace_access),
    session: AsyncSession = Depends(get_session),
) -> list[ConversationRead]:
    conversations = (
        (
            await session.execute(
                select(Conversation)
                .where(
                    Conversation.workspace_id == access.workspace.id,
                    Conversation.user_id == access.user.id,
                    Conversation.deleted_at.is_(None),
                )
                .order_by(Conversation.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [ConversationRead.model_validate(item) for item in conversations]


@router.get("/conversations/{conversation_id}", response_model=ConversationRead)
async def get_conversation(
    conversation_id: str,
    access: WorkspaceAccess = Depends(workspace_access),
    session: AsyncSession = Depends(get_session),
) -> ConversationRead:
    return ConversationRead.model_validate(
        await _conversation_for_user(session, access.workspace.id, access.user.id, conversation_id)
    )


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageRead])
async def list_messages(
    conversation_id: str,
    access: WorkspaceAccess = Depends(workspace_access),
    session: AsyncSession = Depends(get_session),
) -> list[MessageRead]:
    conversation = await _conversation_for_user(
        session, access.workspace.id, access.user.id, conversation_id
    )
    messages = (
        (
            await session.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.workspace_id == access.workspace.id,
                )
                .order_by(Message.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [MessageRead.model_validate(item) for item in messages]


@router.post(
    "/conversations/{conversation_id}/messages", response_model=MessageRead, status_code=201
)
async def create_message(
    conversation_id: str,
    payload: MessageCreate,
    request: Request,
    access: WorkspaceAccess = Depends(workspace_access),
    session: AsyncSession = Depends(get_session),
) -> MessageRead:
    conversation = await _conversation_for_user(
        session, access.workspace.id, access.user.id, conversation_id
    )
    message = Message(
        id=new_id(),
        conversation_id=conversation.id,
        workspace_id=access.workspace.id,
        role="user",
        content=payload.content,
    )
    conversation.updated_at = datetime.now(UTC)
    session.add(message)
    await record_audit(
        session,
        workspace_id=access.workspace.id,
        user_id=access.user.id,
        action="message.created",
        resource_type="message",
        resource_id=message.id,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return MessageRead.model_validate(message)


@router.post("/messages/{message_id}/feedback", response_model=FeedbackRead, status_code=201)
async def submit_feedback(
    message_id: str,
    payload: FeedbackCreate,
    request: Request,
    access: WorkspaceAccess = Depends(workspace_access),
    session: AsyncSession = Depends(get_session),
) -> FeedbackRead:
    message = await session.scalar(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.id == message_id,
            Message.workspace_id == access.workspace.id,
            Conversation.workspace_id == access.workspace.id,
            Conversation.user_id == access.user.id,
        )
    )
    if message is None:
        raise HTTPException(status_code=404, detail="message not found")
    feedback = await session.scalar(
        select(Feedback).where(
            Feedback.message_id == message_id, Feedback.user_id == access.user.id
        )
    )
    if feedback is None:
        feedback = Feedback(
            id=new_id(),
            workspace_id=access.workspace.id,
            message_id=message_id,
            user_id=access.user.id,
            rating=payload.rating,
            comment=payload.comment,
        )
        session.add(feedback)
    else:
        feedback.rating = payload.rating
        feedback.comment = payload.comment
    await record_audit(
        session,
        workspace_id=access.workspace.id,
        user_id=access.user.id,
        action="feedback.submitted",
        resource_type="feedback",
        resource_id=feedback.id,
        request_id=getattr(request.state, "request_id", None),
        metadata={"rating": payload.rating},
    )
    await session.commit()
    return FeedbackRead.model_validate(feedback)


@router.get("/usage", response_model=dict)
async def usage(
    access: WorkspaceAccess = Depends(workspace_access),
    session: AsyncSession = Depends(get_session),
) -> dict:
    period_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = (
        await session.execute(
            select(UsageRecord.kind, func.sum(UsageRecord.units))
            .where(
                UsageRecord.workspace_id == access.workspace.id,
                UsageRecord.created_at >= period_start,
            )
            .group_by(UsageRecord.kind)
        )
    ).all()
    by_kind = {kind: int(total or 0) for kind, total in rows}
    return {
        "workspace_id": access.workspace.id,
        "period_start": period_start,
        "total_units": sum(by_kind.values()),
        "by_kind": by_kind,
    }
