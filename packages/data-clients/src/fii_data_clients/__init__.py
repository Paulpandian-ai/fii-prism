"""FII-PRISM data-provider clients."""

from fii_data_clients.base import (
    BaseHttpClient,
    CostTally,
    ProviderError,
    RateLimitedError,
    TokenBucket,
    UpstreamError,
)
from fii_data_clients.edgar import EdgarClient, FilingRecord, InsiderTrade
from fii_data_clients.embeddings import (
    EMBEDDING_DIM,
    BedrockTitanEmbedder,
    Embedder,
    EmbeddingResult,
    VoyageEmbedder,
    make_embedder,
)
from fii_data_clients.finnhub import FinnhubClient
from fii_data_clients.fmp import FMPClient
from fii_data_clients.fred import DEFAULT_MACRO_SERIES, FredClient
from fii_data_clients.polygon import PolygonClient

__all__ = [
    "DEFAULT_MACRO_SERIES",
    "EMBEDDING_DIM",
    "BaseHttpClient",
    "BedrockTitanEmbedder",
    "CostTally",
    "EdgarClient",
    "Embedder",
    "EmbeddingResult",
    "FMPClient",
    "FilingRecord",
    "FinnhubClient",
    "FredClient",
    "InsiderTrade",
    "PolygonClient",
    "ProviderError",
    "RateLimitedError",
    "TokenBucket",
    "UpstreamError",
    "VoyageEmbedder",
    "make_embedder",
]
