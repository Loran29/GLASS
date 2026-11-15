# GLASS Stage 2: scenario generation
"""Whether GLASS uses its knowledge base (RAG) in Stage 2. Off by default.

One process-wide setting. It is read from ``config.yaml`` (``use_knowledge_base``, default false) and can be
overridden with :func:`set_knowledge_base` (the toggle in the Scenario Studio, the command-line flags
``--use-knowledge-base`` / ``--no-knowledge-base``).

When off, only knowledge-base retrieval is removed:

- :func:`knowledge.retrieval.build_second_llm_evidence` makes no call to
  :func:`knowledge.retrieval.retrieve_for_second_llm`; the evidence carries an empty retrieval result and
  no ``kb_json``, so the generation prompt has no knowledge-base section;
- the refinement feedback retrieves no alternative interventions and has no knowledge-base section.

Everything else is unchanged: the prompts' wording, the log, SIMOD and context evidence, the business context,
the validators, the merger, the loop, the scoring and the metrics. This is exactly the knowledge-base switch of
the real-life ablation's V_no_kb variant (and of the V_no_kb arm of the Stage 2 RAG comparison).
"""
from __future__ import annotations

from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
_enabled: bool | None = None

# Retrievals skipped because the option is off: generation evidence and refinement feedback.
skipped = {"retrieval": 0, "feedback": 0}


def config_default() -> bool:
    """``use_knowledge_base`` from ``config.yaml``; False when the key or the file is missing."""
    try:
        config = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except OSError:
        config = {}
    return bool(config.get("use_knowledge_base", False))


def knowledge_base_enabled() -> bool:
    """The current setting: the last :func:`set_knowledge_base`, else ``config.yaml``, else False."""
    global _enabled
    if _enabled is None:
        _enabled = config_default()
    return _enabled


def set_knowledge_base(enabled: bool | None) -> None:
    """Switch the knowledge base on or off for this process; None returns to ``config.yaml``."""
    global _enabled
    _enabled = None if enabled is None else bool(enabled)
