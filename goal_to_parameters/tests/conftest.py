"""pytest configuration for goal_to_parameters tests.

Adds the package root to sys.path so submodule imports work without
triggering the heavy second_llm/__init__.py (which pulls in ML stacks
not required for unit tests of the patch architecture).

The trick: import the individual patch submodules *before* the package
__init__ has a chance to run its full import chain, by registering
stub shims for the unavailable heavy dependencies only when they are
genuinely absent.
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

# Ensure the package root is on sys.path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _stub_module(name: str) -> None:
    """Register an empty module stub so heavy optional imports don't crash."""
    if name not in sys.modules:
        parts = name.split(".")
        for i in range(1, len(parts) + 1):
            partial = ".".join(parts[:i])
            if partial not in sys.modules:
                sys.modules[partial] = types.ModuleType(partial)


# Stub out heavy optional dependencies that the patch-unit tests do not use.
_OPTIONAL_HEAVY = [
    "streamlit",
    "yaml",
    # sentence_transformers is needed by knowledge.embeddings
    "sentence_transformers",
    "torch",
    "transformers",
    "huggingface_hub",
    "tqdm",
]

for _mod in _OPTIONAL_HEAVY:
    _stub_module(_mod)
