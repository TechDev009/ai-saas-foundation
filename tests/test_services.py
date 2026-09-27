import pytest
from fastapi import HTTPException

from knowledge_api.providers import (
    DeterministicEmbeddingProvider,
    DeterministicLLMProvider,
    ProviderAnswer,
    RetrievedContext,
)
from knowledge_api.schemas import Citation
from knowledge_api.services import (
    SearchHit,
    cosine_similarity,
    decode_document,
    split_chunks,
    verify_provider_answer,
)


def test_document_limits_and_safe_chunking() -> None:
    assert decode_document(b"a\x00b", "text/plain", 10) == "ab"
    chunks = split_chunks("one\n\ntwo\n\nthree", size=7, overlap=2)
    assert chunks and all(chunk for chunk in chunks)
    with pytest.raises(HTTPException):
        decode_document(b"x", "application/pdf", 10)


@pytest.mark.asyncio
async def test_deterministic_provider_is_repeatable_and_abstains() -> None:
    embedder = DeterministicEmbeddingProvider()
    assert await embedder.embed(["same"]) == await embedder.embed(["same"])
    context = RetrievedContext("chunk", "doc", "doc.txt", 0, "The policy is 90 days.", 0.9)
    provider = DeterministicLLMProvider()
    answer = await provider.answer("What is the policy?", [context])
    assert answer.abstained is False and answer.citation_chunk_ids == ["chunk"]
    unknown = await provider.answer("What is an unrelated color?", [context])
    assert unknown.abstained is True and unknown.citation_chunk_ids == []


def test_citations_are_allow_list_verified() -> None:
    context = RetrievedContext("allowed", "doc", "doc.txt", 0, "Evidence", 0.9)
    hit = SearchHit(
        context,
        Citation(
            chunk_id="allowed",
            document_id="doc",
            filename="doc.txt",
            chunk_index=0,
            excerpt="Evidence",
            score=0.9,
        ),
    )
    text, abstained, citations = verify_provider_answer(
        ProviderAnswer("answer", ["allowed", "forged"], False), [hit]
    )
    assert (
        text == "answer"
        and abstained is False
        and [item.chunk_id for item in citations] == ["allowed"]
    )
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
