"""Domain services shared by HTTP routers.

All retrieval queries require a workspace_id supplied by the authorization
boundary. Provider output is verified against retrieved chunks before it is
returned or persisted.
"""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import numpy as np
from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    AuditEvent,
    Document,
    DocumentChunk,
    UsageRecord,
    utcnow,
)
from .providers import (
    EmbeddingProvider,
    ProviderAnswer,
    RetrievedContext,
)
from .schemas import Citation
from .storage import ObjectStorage

_ALLOWED_CONTENT_TYPES = {"text/plain", "text/markdown", "application/json", "text/csv"}
_INJECTION_LINE = re.compile(
    r"^\s*(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous|prior|above)|"
    r"^\s*(?:system|assistant|developer)\s*:",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SearchHit:
    context: RetrievedContext
    citation: Citation


def new_id() -> str:
    return str(uuid4())


def normalize_filename(filename: str | None) -> str:
    raw = (filename or "document.txt").replace("\\", "/").split("/")[-1]
    raw = re.sub(r"[^A-Za-z0-9._ -]", "_", raw).strip(" .")
    if not raw:
        raw = "document.txt"
    return raw[:255]


def decode_document(data: bytes, content_type: str, max_chars: int) -> str:
    normalized_type = content_type.split(";", 1)[0].lower().strip()
    if normalized_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="only UTF-8 text documents are supported by the local processor",
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="document must be valid UTF-8 text") from exc
    if len(text) > max_chars:
        raise HTTPException(status_code=413, detail="document exceeds the character limit")
    # Keep newlines and tabs, but never pass control bytes into storage/retrieval prompts.
    text = "".join(character for character in text if character in "\n\r\t" or ord(character) >= 32)
    if not text.strip():
        raise HTTPException(status_code=422, detail="document is empty")
    return text


def split_chunks(text: str, size: int = 1_200, overlap: int = 150) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if not paragraphs:
        return []
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 1 <= size:
            current = f"{current}\n{paragraph}".strip()
            continue
        if current:
            chunks.append(current)
        if len(paragraph) <= size:
            current = paragraph
        else:
            start = 0
            while start < len(paragraph):
                end = min(start + size, len(paragraph))
                chunks.append(paragraph[start:end])
                if end == len(paragraph):
                    break
                start = max(end - overlap, start + 1)
            current = ""
    if current:
        chunks.append(current)
    return chunks


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def process_document(
    session: AsyncSession,
    storage: ObjectStorage,
    embedder: EmbeddingProvider,
    document: Document,
    max_chars: int,
) -> None:
    """Process one document synchronously for local use or from a worker job."""
    document.status = "processing"
    document.processing_error = None
    await session.flush()
    try:
        raw = await storage.get(document.storage_key)
        text = decode_document(raw, document.content_type, max_chars)
        chunks = split_chunks(text)
        if not chunks:
            raise ValueError("document has no processable text")
        vectors = await embedder.embed(chunks)
        await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
        for index, (content, vector) in enumerate(zip(chunks, vectors, strict=True)):
            session.add(
                DocumentChunk(
                    id=new_id(),
                    document_id=document.id,
                    workspace_id=document.workspace_id,
                    chunk_index=index,
                    content=content,
                    content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    embedding=vector,
                )
            )
        document.status = "ready"
        document.processed_at = utcnow()
        await session.flush()
    except HTTPException:
        document.status = "failed"
        document.processing_error = "document validation failed"
        await session.flush()
        raise
    except Exception as exc:
        # Keep provider/storage internals out of API responses and logs.
        document.status = "failed"
        document.processing_error = "processing failed"
        await session.flush()
        raise RuntimeError("document processing failed") from exc


def cosine_similarity(left: Sequence[float] | None, right: Sequence[float]) -> float:
    if not left:
        return 0.0
    left_array = np.asarray(left, dtype=np.float32)
    right_array = np.asarray(right, dtype=np.float32)
    left_norm = float(np.linalg.norm(left_array))
    right_norm = float(np.linalg.norm(right_array))
    if not left_norm or not right_norm:
        return 0.0
    return float(np.dot(left_array, right_array) / (left_norm * right_norm))


def lexical_score(query: str, content: str) -> float:
    query_terms = {term for term in re.findall(r"[a-z0-9_]+", query.lower()) if len(term) > 2}
    content_terms = set(re.findall(r"[a-z0-9_]+", content.lower()))
    if not query_terms:
        return 0.0
    return len(query_terms & content_terms) / len(query_terms)


async def search_chunks(
    session: AsyncSession,
    workspace_id: str,
    query: str,
    top_k: int,
    embedder: EmbeddingProvider,
) -> list[SearchHit]:
    query_vector = (await embedder.embed([query]))[0]
    rows = (
        await session.execute(
            select(DocumentChunk, Document)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                DocumentChunk.workspace_id == workspace_id,
                Document.workspace_id == workspace_id,
                Document.deleted_at.is_(None),
                Document.status == "ready",
            )
        )
    ).all()
    ranked: list[tuple[float, DocumentChunk, Document]] = []
    for chunk, document in rows:
        semantic = max(0.0, cosine_similarity(chunk.embedding, query_vector))
        lexical = lexical_score(query, chunk.content)
        score = min(1.0, 0.7 * lexical + 0.3 * semantic)
        ranked.append((score, chunk, document))
    ranked.sort(key=lambda value: value[0], reverse=True)
    hits: list[SearchHit] = []
    for score, chunk, document in ranked[:top_k]:
        excerpt = chunk.content[:2000]
        citation = Citation(
            chunk_id=chunk.id,
            document_id=document.id,
            filename=document.filename,
            chunk_index=chunk.chunk_index,
            excerpt=excerpt,
            score=round(score, 6),
        )
        hits.append(
            SearchHit(
                context=RetrievedContext(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    filename=document.filename,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    score=score,
                ),
                citation=citation,
            )
        )
    return hits


def verify_provider_answer(
    provider_answer: ProviderAnswer,
    hits: Sequence[SearchHit],
) -> tuple[str, bool, list[Citation]]:
    """Only expose citations belonging to this exact workspace retrieval set."""
    allowed = {hit.context.chunk_id: hit for hit in hits}
    citations = [
        allowed[chunk_id].citation
        for chunk_id in provider_answer.citation_chunk_ids
        if chunk_id in allowed
    ]
    if provider_answer.abstained or not citations:
        return "I don't have enough evidence in this workspace to answer that.", True, []
    return provider_answer.answer, False, citations


async def check_usage_quota(
    session: AsyncSession,
    workspace_id: str,
    quota: int,
) -> None:
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = await session.scalar(
        select(func.coalesce(func.sum(UsageRecord.units), 0)).where(
            UsageRecord.workspace_id == workspace_id,
            UsageRecord.created_at >= start,
        )
    )
    if int(used or 0) >= quota:
        raise HTTPException(status_code=429, detail="workspace usage quota exceeded")


async def record_usage(
    session: AsyncSession,
    workspace_id: str,
    user_id: str,
    kind: str,
    units: int,
    request_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> UsageRecord:
    record = UsageRecord(
        id=new_id(),
        workspace_id=workspace_id,
        user_id=user_id,
        kind=kind,
        units=max(1, units),
        request_id=request_id,
        metadata_json=metadata,
    )
    session.add(record)
    await session.flush()
    return record


async def record_audit(
    session: AsyncSession,
    *,
    workspace_id: str | None,
    user_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    request_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    """Write metadata-only audit records; never include secrets or raw content."""
    event = AuditEvent(
        id=new_id(),
        workspace_id=workspace_id,
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=request_id,
        metadata_json=metadata,
    )
    session.add(event)
    await session.flush()
    return event
