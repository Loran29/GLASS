"""Dense-embedding backends for RAG retrieval.

Defines a small :class:`Embedder` abstraction and three concrete
backends, auto-selected by availability:

  1. ``SentenceTransformersEmbedder`` — ``all-MiniLM-L6-v2`` via the
     ``sentence-transformers`` library.  Strong semantic retrieval,
     fully local, no API key required.  Preferred when installed.
  2. ``OllamaEmbedder`` — ``nomic-embed-text`` via the local Ollama
     HTTP API.  Used when sentence-transformers is unavailable but an
     Ollama server is running (the default second-LLM provider in this
     project).
  3. ``TfidfEmbedder`` — pure-numpy TF-IDF with sub-linear term
     frequency and cosine normalisation.  Zero external dependencies,
     used as the last-resort fallback so the retrieval pipeline
     remains importable on a bare venv.

Embeddings are L2-normalised so that cosine similarity reduces to a
dot product.  Corpus embeddings are cached to disk keyed by
``(backend_name, model_name, content_sha256)`` so repeated process
starts do not re-encode the full knowledge base.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache directory (relative to this file so it ships with the package)
# ---------------------------------------------------------------------------

_CACHE_DIR = Path(__file__).parent / "cache"


def _cache_path(backend: str, model: str, corpus_hash: str) -> Path:
    safe_model = re.sub(r"[^A-Za-z0-9._-]", "_", model)
    return _CACHE_DIR / f"{backend}__{safe_model}__{corpus_hash[:16]}.npz"


def _corpus_hash(texts: list[str]) -> str:
    h = hashlib.sha256()
    for t in texts:
        h.update(t.encode("utf-8", errors="replace"))
        h.update(b"\x00")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Tokenisation (shared by TF-IDF and BM25)
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on",
    "for", "with", "by", "at", "from", "as", "is", "are", "was", "were",
    "be", "been", "being", "it", "its", "this", "that", "these", "those",
    "we", "our", "you", "your", "they", "their", "he", "she", "his", "her",
    "not", "no", "so", "do", "does", "did", "can", "could", "will", "would",
    "should", "may", "might", "than", "then", "there", "here", "which",
    "who", "whom", "what", "when", "where", "why", "how", "into", "over",
    "under", "about", "such",
}

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


def tokenize(text: str) -> list[str]:
    """Lowercase, stopword-filter, basic stem-free tokeniser.

    Shared by TF-IDF and BM25 so lexical and fallback-dense scores
    operate over the same vocabulary.  Tokens must start with a letter
    so that pure-numeric strings (IDs, years) are discarded — they
    tend to hurt recall without helping precision on this corpus.
    """
    return [
        tok.lower() for tok in _TOKEN_RE.findall(text)
        if tok.lower() not in _STOPWORDS and len(tok) > 1
    ]


# ---------------------------------------------------------------------------
# Embedder abstraction
# ---------------------------------------------------------------------------

class Embedder(ABC):
    """Encode text into L2-normalised dense vectors."""

    #: Short backend identifier used in cache file names.
    backend_name: str = "abstract"
    #: Model identifier — version-stamps the cache so stale vectors
    #: aren't silently reused after a model swap.
    model_name: str = "abstract"

    @abstractmethod
    def encode(self, texts: list[str]) -> np.ndarray:
        """Return an ``(n_texts, dim)`` float32 matrix of L2-normalised rows."""

    def encode_corpus_cached(self, texts: list[str]) -> np.ndarray:
        """Encode a corpus, loading from disk cache when possible."""
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)

        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        corpus_hash = _corpus_hash(texts)
        path = _cache_path(self.backend_name, self.model_name, corpus_hash)

        if path.exists():
            try:
                with np.load(path) as data:
                    vecs = data["vectors"].astype(np.float32)
                if vecs.shape[0] == len(texts):
                    logger.debug("Loaded %d cached embeddings from %s", len(texts), path.name)
                    return vecs
            except (OSError, KeyError, ValueError) as exc:
                logger.warning("Embedding cache %s unreadable (%s) — re-encoding.", path.name, exc)

        vecs = self.encode(texts)
        try:
            np.savez_compressed(path, vectors=vecs)
        except OSError as exc:
            logger.warning("Could not write embedding cache %s: %s", path.name, exc)
        return vecs


def _l2_normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0.0, 1.0, norms)
    return (matrix / norms).astype(np.float32)


# ---------------------------------------------------------------------------
# 1. sentence-transformers backend (preferred when installed)
# ---------------------------------------------------------------------------

class SentenceTransformersEmbedder(Embedder):
    """Dense embeddings from a local ``sentence-transformers`` model."""

    backend_name = "st"

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer  # type: ignore

        self.model_name = model_name
        self._model = SentenceTransformer(model_name)

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 384), dtype=np.float32)
        vecs = self._model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vecs.astype(np.float32)


# ---------------------------------------------------------------------------
# 2. Ollama backend (falls back to this when ST is missing)
# ---------------------------------------------------------------------------

class OllamaEmbedder(Embedder):
    """Dense embeddings via a local Ollama server's ``/api/embeddings``."""

    backend_name = "ollama"

    def __init__(
        self,
        model_name: str = "nomic-embed-text",
        base_url: str = "http://localhost:11434",
        timeout: float = 30.0,
    ) -> None:
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._probe()

    def _probe(self) -> None:
        """Raise RuntimeError if Ollama is unreachable or the model is missing."""
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read())
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Ollama not reachable at {self.base_url}: {exc}") from exc

        models = {m.get("name", "").split(":", 1)[0] for m in payload.get("models", [])}
        if self.model_name.split(":", 1)[0] not in models:
            raise RuntimeError(
                f"Ollama model '{self.model_name}' not pulled. "
                f"Run: ollama pull {self.model_name}"
            )

    def _embed_one(self, text: str) -> list[float]:
        body = json.dumps({"model": self.model_name, "prompt": text}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read())
        return payload.get("embedding", [])

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 768), dtype=np.float32)
        vecs = [self._embed_one(t) for t in texts]
        arr = np.asarray(vecs, dtype=np.float32)
        return _l2_normalise(arr)


# ---------------------------------------------------------------------------
# 3. TF-IDF fallback (no deps — always works)
# ---------------------------------------------------------------------------

class TfidfEmbedder(Embedder):
    """Pure-numpy TF-IDF with sublinear TF + cosine normalisation.

    Fitted once on the corpus at encode time.  The vocabulary and IDF
    weights are stored on the instance so subsequent query encodings
    use the same projection.
    """

    backend_name = "tfidf"

    def __init__(self) -> None:
        self.model_name = "sublinear_tf_cosine"
        self._vocab: dict[str, int] = {}
        self._idf: np.ndarray | None = None
        self._fitted = False

    def _fit(self, texts: list[str]) -> None:
        df: Counter[str] = Counter()
        tokenised = [tokenize(t) for t in texts]
        for toks in tokenised:
            for term in set(toks):
                df[term] += 1

        # Keep only terms that appear in at least 1 doc but fewer than
        # 95 % of them (trivially filters noise + overly common words).
        n = max(len(texts), 1)
        ceiling = max(int(0.95 * n), 1)
        kept = [term for term, cnt in df.items() if 1 <= cnt <= ceiling]
        kept.sort()  # stable vocab ordering — reproducibility
        self._vocab = {term: i for i, term in enumerate(kept)}

        if not self._vocab:
            # Degenerate corpus (e.g. single stop-word doc): fall back
            # to a trivial 1-dim constant space.
            self._idf = np.zeros(1, dtype=np.float32)
            self._fitted = True
            return

        idf = np.zeros(len(self._vocab), dtype=np.float32)
        for term, idx in self._vocab.items():
            idf[idx] = math.log((n + 1) / (df[term] + 1)) + 1.0
        self._idf = idf
        self._fitted = True

    def _vectorise(self, tokens: list[str]) -> np.ndarray:
        if not self._vocab or self._idf is None:
            return np.zeros(max(len(self._vocab), 1), dtype=np.float32)

        vec = np.zeros(len(self._vocab), dtype=np.float32)
        tf = Counter(tokens)
        for term, count in tf.items():
            idx = self._vocab.get(term)
            if idx is None:
                continue
            # sublinear TF dampens high-frequency terms.
            vec[idx] = (1.0 + math.log(count)) * self._idf[idx]
        return vec

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            dim = max(len(self._vocab), 1)
            return np.zeros((0, dim), dtype=np.float32)

        if not self._fitted:
            self._fit(texts)

        matrix = np.vstack([self._vectorise(tokenize(t)) for t in texts])
        return _l2_normalise(matrix)


# ---------------------------------------------------------------------------
# Auto-selection entry point
# ---------------------------------------------------------------------------

def _try_sentence_transformers() -> Embedder | None:
    try:
        return SentenceTransformersEmbedder()
    except ImportError:
        return None
    except Exception as exc:  # network, torch, HF cache issues
        logger.info("sentence-transformers unavailable (%s) — trying next backend.", exc)
        return None


def _try_ollama() -> Embedder | None:
    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    model = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    try:
        return OllamaEmbedder(model_name=model, base_url=base)
    except Exception as exc:
        logger.info("Ollama embedder unavailable (%s) — trying next backend.", exc)
        return None


def build_default_embedder(
    *,
    prefer: str | None = None,
) -> Embedder:
    """Return the best available embedder for this host.

    Selection order: ``sentence-transformers → Ollama → TF-IDF``.
    Override with ``prefer={"st","ollama","tfidf"}`` or the
    ``RAG_EMBEDDER`` environment variable.
    """
    choice = (prefer or os.getenv("RAG_EMBEDDER") or "").lower().strip()

    if choice == "tfidf":
        return TfidfEmbedder()
    if choice in {"st", "sentence-transformers"}:
        return _try_sentence_transformers() or TfidfEmbedder()
    if choice == "ollama":
        return _try_ollama() or TfidfEmbedder()

    # Default auto-selection.
    emb = _try_sentence_transformers() or _try_ollama()
    if emb is not None:
        logger.info("RAG embedder: %s / %s", emb.backend_name, emb.model_name)
        return emb
    logger.info("RAG embedder: tfidf fallback (install sentence-transformers for semantic retrieval).")
    return TfidfEmbedder()


__all__ = [
    "Embedder",
    "OllamaEmbedder",
    "SentenceTransformersEmbedder",
    "TfidfEmbedder",
    "build_default_embedder",
    "tokenize",
]
