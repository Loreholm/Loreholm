"""Dashboard-local embedding service.

ArcadeDB has no in-database embedding procedure, and the multi-model engine
is the substrate for the staging reconciler — so embedding generation runs
in the dashboard container, serving the ArcadeDB write path and the
reconciler without depending on any in-database procedure.

Primary model: `microsoft/harrier-oss-v1-270m` (640-dim, last-token
pooling, multilingual, distilled from a larger teacher). Chosen for
reconciler quality — dedup thresholds are tight and embedding quality is
the first-order driver of false-merge rate.

Fallback model: `sentence-transformers/all-MiniLM-L6-v2` (384-dim,
English, ~22M params). Opt-in via `DASHBOARD_EMBEDDING_MODEL=minilm` for
hosts where Harrier's p99 exceeds the Phase 0.5 latency gate.

The fallback is **not** a runtime auto-select. Switching after a
database is populated requires re-embedding every `Entity.embedding`,
`Memory.embedding`, and `Staging.embedding` property (dimensions and
semantics change). The active model is recorded in `databases.json`
alongside the dimensions so the ArcadeDB index config always matches
the active encoder.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable, Iterable, List, Literal, Optional

from ..core.config import DASHBOARD_EMBEDDING_MODEL


logger = logging.getLogger(__name__)


ModelKey = Literal["harrier-270m", "minilm"]


@dataclass(frozen=True)
class _ModelSpec:
    key: ModelKey
    hf_id: str
    dimensions: int


_MODEL_SPECS: dict[ModelKey, _ModelSpec] = {
    "harrier-270m": _ModelSpec(
        key="harrier-270m",
        hf_id="microsoft/harrier-oss-v1-270m",
        dimensions=640,
    ),
    "minilm": _ModelSpec(
        key="minilm",
        hf_id="sentence-transformers/all-MiniLM-L6-v2",
        dimensions=384,
    ),
}


def _resolve_model_key(name: Optional[str]) -> ModelKey:
    normalized = (name or "").strip().lower()
    if normalized in _MODEL_SPECS:
        return normalized  # type: ignore[return-value]
    # Unknown value → default to Harrier. The config loader logs a warning.
    return "harrier-270m"


class EmbeddingService:
    """Lazy-loaded CPU embedder. Thread-safe for `embed` / `embed_batch`.

    The model loads on the first call. We do not eagerly load in the
    constructor because a cold import of `transformers` + a forward pass
    can take 10+ seconds on modest hardware.
    """

    def __init__(self, model_name: Optional[str] = None) -> None:
        key = _resolve_model_key(model_name or DASHBOARD_EMBEDDING_MODEL)
        self._spec = _MODEL_SPECS[key]
        self._lock = threading.Lock()
        self._embed_fn: Optional[Callable[[str], List[float]]] = None
        self._embed_batch_fn: Optional[Callable[[List[str]], List[List[float]]]] = None

    @property
    def dimensions(self) -> int:
        return self._spec.dimensions

    @property
    def model_key(self) -> ModelKey:
        return self._spec.key

    @property
    def hf_id(self) -> str:
        return self._spec.hf_id

    def embed(self, text: str) -> List[float]:
        self._ensure_loaded()
        assert self._embed_fn is not None
        return self._embed_fn(text)

    def embed_batch(self, texts: Iterable[str]) -> List[List[float]]:
        items = list(texts)
        if not items:
            return []
        self._ensure_loaded()
        assert self._embed_batch_fn is not None
        return self._embed_batch_fn(items)

    # ------------------------------------------------------------------
    # Lazy loading
    # ------------------------------------------------------------------
    def _ensure_loaded(self) -> None:
        if self._embed_fn is not None:
            return
        with self._lock:
            if self._embed_fn is not None:
                return
            if self._spec.key == "harrier-270m":
                self._load_harrier()
            else:
                self._load_minilm()

    def _load_harrier(self) -> None:
        # Decoder-only model with last-token pooling + L2 normalization.
        # `sentence-transformers` doesn't target decoder-only embedders,
        # so we use `transformers` directly and implement the pooling.
        from transformers import AutoModel, AutoTokenizer  # type: ignore
        import torch  # type: ignore

        logger.info("Loading embedding model %s", self._spec.hf_id)
        tokenizer = AutoTokenizer.from_pretrained(self._spec.hf_id)
        model = AutoModel.from_pretrained(self._spec.hf_id)
        model.eval()
        device = torch.device("cpu")
        model.to(device)

        def _forward(texts: List[str]) -> List[List[float]]:
            with torch.no_grad():
                tokens = tokenizer(
                    texts, return_tensors="pt", padding=True, truncation=True
                ).to(device)
                output = model(**tokens)
                last_hidden = output.last_hidden_state
                last_index = tokens["attention_mask"].sum(dim=1) - 1
                pooled = last_hidden[torch.arange(last_hidden.size(0)), last_index]
                normalized = torch.nn.functional.normalize(pooled, p=2, dim=1)
            return normalized.tolist()

        def _embed(text: str) -> List[float]:
            return _forward([text])[0]

        def _embed_batch(texts: List[str]) -> List[List[float]]:
            return _forward(texts)

        self._embed_fn = _embed
        self._embed_batch_fn = _embed_batch

    def _load_minilm(self) -> None:
        from sentence_transformers import SentenceTransformer  # type: ignore

        logger.info("Loading embedding model %s", self._spec.hf_id)
        model = SentenceTransformer(self._spec.hf_id, device="cpu")

        def _embed(text: str) -> List[float]:
            return model.encode(text, normalize_embeddings=True).tolist()

        def _embed_batch(texts: List[str]) -> List[List[float]]:
            return model.encode(
                texts, normalize_embeddings=True, batch_size=32
            ).tolist()

        self._embed_fn = _embed
        self._embed_batch_fn = _embed_batch


# ---------------------------------------------------------------------------
# Process-level singleton
# ---------------------------------------------------------------------------

_service_lock = threading.Lock()
_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    """Return the process-wide `EmbeddingService`. Lazy-constructed.

    Constructing the service is cheap (it does not load the model until
    the first embed call), so we can make one per process and reuse it.
    """
    global _service
    if _service is not None:
        return _service
    with _service_lock:
        if _service is None:
            _service = EmbeddingService()
    return _service
