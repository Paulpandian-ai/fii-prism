"""Embedding client.

Primary: Voyage AI `voyage-finance-2` (1024-dim). Voyage was acquired by Anthropic in 2024;
separate API, separate key (VOYAGE_API_KEY).

Fallback: AWS Bedrock `amazon.titan-embed-text-v2` (1024-dim). Kept on-AWS so the fallback
path doesn't break the Anthropic + AWS stack commitment.

Both return 1024-dim vectors so they match the filing_chunks.embedding column.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Protocol

import structlog

log = structlog.get_logger(__name__)

VOYAGE_MODEL = "voyage-finance-2"
BEDROCK_TITAN_V2 = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIM = 1024


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    tokens: int = 0
    cost_usd: float = 0.0


class Embedder(Protocol):
    model: str

    async def embed(self, texts: list[str], *, input_type: str = "document") -> EmbeddingResult: ...


# --- Voyage -------------------------------------------------------------------------------


class VoyageEmbedder:
    model = VOYAGE_MODEL
    # Voyage voyage-finance-2: $0.12 per 1M tokens (as of 2025-Q4).
    cost_per_1m_tokens_usd = 0.12

    def __init__(self, *, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("VOYAGE_API_KEY")
        if not self._api_key:
            raise RuntimeError("VOYAGE_API_KEY not set")
        # Lazy import keeps the module import cheap for callers that only use Bedrock.
        import voyageai

        self._client = voyageai.AsyncClient(api_key=self._api_key)
        self._log = log.bind(provider="voyage")

    async def embed(self, texts: list[str], *, input_type: str = "document") -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=[], model=self.model)
        start = time.perf_counter()
        result = await self._client.embed(
            texts=texts, model=self.model, input_type=input_type, output_dimension=EMBEDDING_DIM
        )
        duration_ms = int((time.perf_counter() - start) * 1000)
        tokens = int(getattr(result, "total_tokens", 0) or 0)
        cost = tokens / 1_000_000 * self.cost_per_1m_tokens_usd
        self._log.info(
            "voyage_embed",
            count=len(texts),
            tokens=tokens,
            cost_usd=round(cost, 6),
            duration_ms=duration_ms,
        )
        return EmbeddingResult(
            vectors=list(result.embeddings), model=self.model, tokens=tokens, cost_usd=cost
        )


# --- Bedrock Titan v2 ---------------------------------------------------------------------


class BedrockTitanEmbedder:
    model = BEDROCK_TITAN_V2
    # Bedrock Titan text embed v2: $0.02 per 1M tokens (us-east-1, 2025-Q4).
    cost_per_1m_tokens_usd = 0.02

    def __init__(self, *, region_name: str | None = None) -> None:
        import boto3

        self._region = region_name or os.environ.get("AWS_REGION", "us-east-1")
        self._client = boto3.client("bedrock-runtime", region_name=self._region)
        self._log = log.bind(provider="bedrock", model=self.model)

    async def embed(self, texts: list[str], *, input_type: str = "document") -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=[], model=self.model)
        # Bedrock's embed API is one-at-a-time; run them concurrently but respect sane fanout.
        sem = asyncio.Semaphore(8)

        async def one(text: str) -> tuple[list[float], int]:
            async with sem:
                return await asyncio.to_thread(self._embed_one, text)

        start = time.perf_counter()
        pairs = await asyncio.gather(*(one(t) for t in texts))
        duration_ms = int((time.perf_counter() - start) * 1000)

        vectors = [p[0] for p in pairs]
        tokens = sum(p[1] for p in pairs)
        cost = tokens / 1_000_000 * self.cost_per_1m_tokens_usd
        self._log.info(
            "bedrock_embed",
            count=len(texts),
            tokens=tokens,
            cost_usd=round(cost, 6),
            duration_ms=duration_ms,
        )
        return EmbeddingResult(vectors=vectors, model=self.model, tokens=tokens, cost_usd=cost)

    def _embed_one(self, text: str) -> tuple[list[float], int]:
        import json

        body = json.dumps({"inputText": text, "dimensions": EMBEDDING_DIM, "normalize": True})
        resp = self._client.invoke_model(modelId=self.model, body=body)
        payload = json.loads(resp["body"].read())
        return list(payload["embedding"]), int(payload.get("inputTextTokenCount") or 0)


# --- Factory ------------------------------------------------------------------------------


def make_embedder(*, prefer: str = "auto") -> Embedder:
    """Construct an embedder based on available credentials.

    prefer: 'voyage' | 'bedrock' | 'auto'. 'auto' tries Voyage first, falls back to Bedrock.
    """
    if prefer in ("voyage", "auto"):
        try:
            return VoyageEmbedder()
        except Exception as exc:
            if prefer == "voyage":
                raise
            log.info("voyage_unavailable_falling_back_to_bedrock", error=str(exc))
    return BedrockTitanEmbedder()
