"""Okapi BM25 lexical scorer.

Pure-Python implementation with standard parameters (``k1=1.5``,
``b=0.75``) — no external dependencies.  Used as the lexical arm of
the hybrid retriever so exact-term matches (e.g. "rework",
"gateway") are not lost when the dense embedder generalises them
away.

References:
  Robertson & Zaragoza (2009), *The Probabilistic Relevance
  Framework: BM25 and Beyond*.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Sequence

from knowledge.embeddings import tokenize


class BM25Index:
    """Fit a BM25Okapi index over a corpus of pre-tokenised documents."""

    def __init__(
        self,
        corpus: Sequence[str] | Sequence[list[str]],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self.k1 = k1
        self.b = b

        # Accept either raw strings or pre-tokenised lists.
        self._docs: list[list[str]] = [
            d if isinstance(d, list) else tokenize(d)  # type: ignore[arg-type]
            for d in corpus
        ]
        self._doc_lens = [len(d) for d in self._docs]
        self._avgdl = (sum(self._doc_lens) / len(self._doc_lens)) if self._doc_lens else 0.0
        self._doc_freqs = [Counter(d) for d in self._docs]

        df: Counter[str] = Counter()
        for d in self._docs:
            for term in set(d):
                df[term] += 1

        n = len(self._docs)
        self._idf: dict[str, float] = {
            term: math.log(1.0 + (n - cnt + 0.5) / (cnt + 0.5))
            for term, cnt in df.items()
        }

    def score(self, query: str | list[str]) -> list[float]:
        """Return a BM25 score per document for ``query`` (raw str or tokens)."""
        q_tokens = query if isinstance(query, list) else tokenize(query)
        if not q_tokens or not self._docs:
            return [0.0] * len(self._docs)

        scores: list[float] = []
        for doc_idx, tf in enumerate(self._doc_freqs):
            score = 0.0
            dl = self._doc_lens[doc_idx]
            norm = 1.0 - self.b + self.b * (dl / self._avgdl if self._avgdl else 1.0)
            for term in q_tokens:
                f = tf.get(term, 0)
                if f == 0:
                    continue
                idf = self._idf.get(term, 0.0)
                score += idf * (f * (self.k1 + 1)) / (f + self.k1 * norm)
            scores.append(score)
        return scores


__all__ = ["BM25Index"]
