"""Cross-encoder re-ranking for the hybrid RAG system.

Wraps sentence-transformers CrossEncoder for query↔document joint
scoring.  Applied after RRF fusion to re-rank the top candidates
before per-kind capping.

Model: cross-encoder/ms-marco-MiniLM-L-6-v2 (fast, ~22M params)
"""

from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)

_UNAVAILABLE_LOGGED = False


class CrossEncoderReranker:
    """Thin wrapper around sentence-transformers CrossEncoder.

    Lazy-loads the model on first use.  If sentence-transformers is
    not installed or the model fails to load, is_available() returns
    False and rerank() returns zeros — the caller falls back to pure
    RRF scores.
    """

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> None:
        self._model_name = model_name
        self._model = None
        self._available: bool | None = None  # None = not yet checked

    def is_available(self) -> bool:
        """Return True if the cross-encoder model can be loaded."""
        if self._available is not None:
            return self._available

        global _UNAVAILABLE_LOGGED
        try:
            from sentence_transformers import CrossEncoder  # type: ignore

            self._model = CrossEncoder(self._model_name)
            self._available = True
            logger.debug("CrossEncoderReranker loaded: %s", self._model_name)
        except Exception as exc:
            if not _UNAVAILABLE_LOGGED:
                logger.warning(
                    "Cross-encoder unavailable (%s) — falling back to pure RRF.", exc
                )
                _UNAVAILABLE_LOGGED = True
            self._available = False

        return self._available

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        """Score each (query, text) pair and return raw logit scores.

        Returns a list of ``len(texts)`` floats.  If the model is
        unavailable, returns ``[0.0] * len(texts)``.
        """
        if not texts:
            return []

        if not self.is_available() or self._model is None:
            return [0.0] * len(texts)

        try:
            pairs = [(query, t) for t in texts]
            scores = self._model.predict(pairs)
            return [float(s) for s in scores]
        except Exception as exc:
            logger.warning("CrossEncoder.predict failed: %s — returning zeros.", exc)
            return [0.0] * len(texts)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_default_reranker: CrossEncoderReranker | None = None


def get_default_reranker() -> CrossEncoderReranker:
    """Return a process-wide CrossEncoderReranker singleton."""
    global _default_reranker
    if _default_reranker is None:
        _default_reranker = CrossEncoderReranker()
    return _default_reranker


def sigmoid(x: float) -> float:
    """Numerically stable sigmoid."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    ex = math.exp(x)
    return ex / (1.0 + ex)
