from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import WorkspaceAccess, get_session, require_role, workspace_access
from ..models import Document, DocumentChunk, IdempotencyKey
from ..schemas import DocumentRead
from ..services import (
    new_id,
    normalize_filename,
    process_document,
    record_audit,
    sha256_bytes,
)

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/documents", tags=["documents"])


async def _get_document(session: AsyncSession, workspace_id: str, document_id: str) -> Document:
    document = await session.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.workspace_id == workspace_id,
            Document.deleted_at.is_(None),
        )
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return document


@router.post("", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    access: WorkspaceAccess = Depends(workspace_access),
    session: AsyncSession = Depends(get_session),
) -> DocumentRead:
    settings = request.app.state.settings
    if idempotency_key:
        if len(idempotency_key) > 200:
            raise HTTPException(status_code=400, detail="Idempotency-Key is too long")
        previous = await session.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.workspace_id == access.workspace.id,
                IdempotencyKey.user_id == access.user.id,
                IdempotencyKey.key == idempotency_key,
                IdempotencyKey.operation == "document.upload",
            )
        )
        if previous is not None:
            return DocumentRead.model_validate(previous.response_json)
    content_length = request.headers.get("content-length")
    if (
        content_length
        and content_length.isdigit()
        and int(content_length) > settings.max_upload_bytes + 100_000
    ):
        raise HTTPException(status_code=413, detail="upload exceeds the byte limit")
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="upload exceeds the byte limit")
    if not data:
        raise HTTPException(status_code=422, detail="upload is empty")
    filename = normalize_filename(file.filename)
    content_type = (file.content_type or "text/plain").split(";", 1)[0].lower()
    document = Document(
        id=new_id(),
        workspace_id=access.workspace.id,
        uploaded_by=access.user.id,
        filename=filename,
        content_type=content_type,
        byte_size=len(data),
        sha256=sha256_bytes(data),
        storage_key=f"workspaces/{access.workspace.id}/documents/{new_id()}/{filename}",
        status="uploaded",
    )
    storage_key = document.storage_key
    await request.app.state.storage.put(storage_key, data, content_type)
    session.add(document)
    await session.flush()
    try:
        await process_document(
            session,
            request.app.state.storage,
            request.app.state.embedding_provider,
            document,
            settings.max_document_chars,
        )
    except HTTPException:
        await session.rollback()
        await request.app.state.storage.delete(storage_key)
        raise
    except RuntimeError as exc:
        await session.rollback()
        await request.app.state.storage.delete(storage_key)
        raise HTTPException(status_code=422, detail="document processing failed") from exc
    response = DocumentRead.model_validate(document)
    await record_audit(
        session,
        workspace_id=access.workspace.id,
        user_id=access.user.id,
        action="document.uploaded",
        resource_type="document",
        resource_id=document.id,
        request_id=getattr(request.state, "request_id", None),
        metadata={"filename": filename, "bytes": len(data), "status": document.status},
    )
    if idempotency_key:
        session.add(
            IdempotencyKey(
                id=new_id(),
                workspace_id=access.workspace.id,
                user_id=access.user.id,
                key=idempotency_key,
                operation="document.upload",
                response_json=response.model_dump(mode="json"),
            )
        )
    await session.commit()
    return response


@router.get("", response_model=list[DocumentRead])
async def list_documents(
    access: WorkspaceAccess = Depends(workspace_access),
    session: AsyncSession = Depends(get_session),
) -> list[DocumentRead]:
    documents = (
        (
            await session.execute(
                select(Document)
                .where(Document.workspace_id == access.workspace.id, Document.deleted_at.is_(None))
                .order_by(Document.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [DocumentRead.model_validate(document) for document in documents]


@router.get("/{document_id}", response_model=DocumentRead)
async def get_document(
    document_id: str,
    access: WorkspaceAccess = Depends(workspace_access),
    session: AsyncSession = Depends(get_session),
) -> DocumentRead:
    return DocumentRead.model_validate(
        await _get_document(session, access.workspace.id, document_id)
    )


@router.post("/{document_id}/process", response_model=DocumentRead)
async def reprocess_document(
    document_id: str,
    request: Request,
    access: WorkspaceAccess = Depends(workspace_access),
    session: AsyncSession = Depends(get_session),
) -> DocumentRead:
    document = await _get_document(session, access.workspace.id, document_id)
    require_role(access, "owner", "admin", "member")
    try:
        await process_document(
            session,
            request.app.state.storage,
            request.app.state.embedding_provider,
            document,
            request.app.state.settings.max_document_chars,
        )
    except HTTPException:
        await session.commit()
        raise
    except RuntimeError as exc:
        await session.commit()
        raise HTTPException(status_code=422, detail="document processing failed") from exc
    await record_audit(
        session,
        workspace_id=access.workspace.id,
        user_id=access.user.id,
        action="document.processed",
        resource_type="document",
        resource_id=document.id,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return DocumentRead.model_validate(document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str,
    request: Request,
    access: WorkspaceAccess = Depends(workspace_access),
    session: AsyncSession = Depends(get_session),
) -> None:
    document = await _get_document(session, access.workspace.id, document_id)
    require_role(access, "owner", "admin", "member")
    document.deleted_at = datetime.now(UTC)
    document.status = "deleted"
    await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
    await request.app.state.storage.delete(document.storage_key)
    await record_audit(
        session,
        workspace_id=access.workspace.id,
        user_id=access.user.id,
        action="document.deleted",
        resource_type="document",
        resource_id=document.id,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
