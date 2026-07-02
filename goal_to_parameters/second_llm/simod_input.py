"""Handling for raw SIMOD output.

For now this module simply wraps the raw text into a :class:`RawSimodInput`
model with minimal metadata (line count, non-empty flag).

TODO: Once the thesis defines how SIMOD output should be parsed or
      normalised, add structured extraction here. The raw text should
      still be preserved as a first-class input to the second LLM.
"""

from __future__ import annotations

from second_llm.models import RawSimodInput, SimodResult


def accept_simod_raw_input(raw_text: str, bpmn_xml: str = "") -> RawSimodInput:
    """Wrap raw SIMOD output text into a ``RawSimodInput`` model.

    No deep parsing is attempted — only basic metadata is computed.
    If ``bpmn_xml`` is provided it is stored in a ``SimodResult`` so that
    the iterative optimisation loop can access the BPMN without requiring
    a real SIMOD run (e.g. when loading a pre-built example).
    """
    stripped = raw_text.strip()
    lines = stripped.splitlines() if stripped else []

    simod_result = None
    if bpmn_xml.strip() or stripped:
        simod_result = SimodResult(
            bpmn_content=bpmn_xml.strip(),
            json_params_content=stripped,
        )

    return RawSimodInput(
        raw_text=stripped,
        line_count=len(lines),
        is_non_empty=bool(stripped),
        simod_result=simod_result,
    )
