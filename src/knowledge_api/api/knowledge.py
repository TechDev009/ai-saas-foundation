from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import WorkspaceAccess, get_session, workspace_access
from ..models import Conversation, Message
from ..schemas import AnswerRequest, AnswerResponse, SearchRequest, SearchResponse
from ..services import (
    check_usage_quota,
    new_id,
    record_audit,
    record_usage,
    search_chunks,
    verify_provider_answer,
)

router = APIRouter(prefix="/v1/workspaces/{workspace_id}", tags=["knowledge"])


@router.post("/search", response_model=SearchResponse)
async def search(
    payload: SearchRequest,
    request: Request,
    access: WorkspaceAccess = Depends(workspace_access),
    session: AsyncSession = Depends(get_session),
) -> SearchResponse:
    settings = request.app.state.settings
    hits = await search_chunks(
        session,
        access.workspace.id,
        payload.query[: settings.max_search_chars],
        min(payload.top_k, settings.max_top_k),
        request.app.state.embedding_provider,
    )
    await record_usage(
        session,
        access.workspace.id,
        access.user.id,
        "search",
        1,
        getattr(request.state, "request_id", None),
    )
    await record_audit(
        session,
        workspace_id=access.workspace.id,
        user_id=access.user.id,
        action="search.executed",
        resource_type="workspace",
        resource_id=access.workspace.id,
        request_id=getattr(request.state, "request_id", None),
        metadata={"result_count": len(hits)},
    )
    await session.commit()
    return SearchResponse(query=payload.query, results=[hit.citation for hit in hits])


@router.post("/answers", response_model=AnswerResponse)
async def answer(
    payload: AnswerRequest,
    request: Request,
    access: WorkspaceAccess = Depends(workspace_access),
    session: AsyncSession = Depends(get_session),
) -> AnswerResponse:
    settings = request.app.state.settings
    await check_usage_quota(session, access.workspace.id, settings.monthly_usage_quota)
    hits = await search_chunks(
        session,
        access.workspace.id,
        payload.question[: settings.max_question_chars],
        min(payload.top_k, settings.max_top_k),
        request.app.state.embedding_provider,
    )
    provider_answer = await request.app.state.llm_provider.answer(
        payload.question[: settings.max_question_chars], [hit.context for hit in hits]
    )
    answer_text, abstained, citations = verify_provider_answer(provider_answer, hits)

    conversation: Conversation | None = None
    if payload.conversation_id:
        conversation = await session.scalar(
            __import__("sqlalchemy")
            .select(Conversation)
            .where(
                Conversation.id == payload.conversation_id,
                Conversation.workspace_id == access.workspace.id,
                Conversation.user_id == access.user.id,
                Conversation.deleted_at.is_(None),
            )
        )
        if conversation is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="conversation not found")
    if conversation is None:
        conversation = Conversation(
            id=new_id(),
            workspace_id=access.workspace.id,
            user_id=access.user.id,
            title=payload.question[:80],
        )
        session.add(conversation)
        await session.flush()
    user_message = Message(
        id=new_id(),
        conversation_id=conversation.id,
        workspace_id=access.workspace.id,
        role="user",
        content=payload.question,
    )
    assistant_message = Message(
        id=new_id(),
        conversation_id=conversation.id,
        workspace_id=access.workspace.id,
        role="assistant",
        content=answer_text,
        citations=[citation.model_dump(mode="json") for citation in citations] or None,
    )
    session.add_all([user_message, assistant_message])
    await record_usage(
        session,
        access.workspace.id,
        access.user.id,
        "answer",
        max(1, len(payload.question) // 250),
        getattr(request.state, "request_id", None),
        metadata={"abstained": abstained, "citation_count": len(citations)},
    )
    await record_audit(
        session,
        workspace_id=access.workspace.id,
        user_id=access.user.id,
        action="answer.generated",
        resource_type="message",
        resource_id=assistant_message.id,
        request_id=getattr(request.state, "request_id", None),
        metadata={"abstained": abstained, "citation_count": len(citations)},
    )
    await session.commit()
    return AnswerResponse(
        answer=answer_text,
        abstained=abstained,
        citations=citations,
        conversation_id=conversation.id,
        message_id=assistant_message.id,
        request_id=getattr(request.state, "request_id", ""),
    )
