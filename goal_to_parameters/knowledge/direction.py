"""Direction of a knowledge-base entry's stated effect on a metric.

The knowledge base states each entry's effect in prose ("Reduce waiting time by ...", "utilization
improves as a side-effect of ...", "raised overall average utilization by 8.5 %"). For refinement, a
strategy is only useful if it moves the KPI the way the KPI's target direction asks. This module derives
that direction from the entry's own text; no field has to be maintained in the entries.

Rule
----
The text is split into clauses (sentences, "while", "but", commas, dashes, brackets). In each clause, an
effect word within ``WINDOW`` tokens of a word for the metric casts a vote:
  increase  increase, raise, rise/rose, higher, boost, maximize, upward, grow, improve (for metrics where
            higher is better: utilization, throughput), keep ... high
  decrease  reduce, decrease, lower, minimize, cut, shorten, drop, fall/fell, below, less, downward, relieve,
            eliminate, improve (for time and cost metrics); for utilization also "without / avoid / prevent
            overloading"
A word for the inverse of the metric flips the vote (for utilization: "idle", so "reduce idle time" counts
as increasing utilization). The entry's direction is "increase" or "decrease" when all votes agree, "mixed"
when they disagree, and ``None`` when the text states no effect on the metric.

``direction_rank`` turns this into a ranking for a KPI: 0 = the stated effect is the one the KPI's
direction asks for, 1 = no derivable direction (kept, ranked last), ``None`` = the opposite or a mixed
effect (dropped).
"""
from __future__ import annotations

import re

WINDOW = 6

# metric category -> (words for the metric, words for its inverse, higher is better)
METRICS: dict[str, tuple[str, str | None, bool]] = {
    "resource_utilisation": (r"utili[sz]\w*|workload|busy|saturat\w*|occupancy|load", r"idle\w*|underus\w*", True),
    "waiting_time": (r"wait\w*|queu\w*|delay\w*", None, False),
    "processing_time": (r"cycle|lead|processing|duration|turnaround|length|end-to-end|sojourn", None, False),
    "cost": (r"costs?|expens\w*|spend\w*", None, False),
    "throughput": (r"throughput|capacity|output|served|examined|processed", None, True),
}
_UP = (r"increas\w*|rais\w*|rise[sn]?|rising|rose|higher|boost\w*|maximi[sz]\w*|upward\w*|grow\w*|grew|"
       r"enlarg\w*|expand\w*")
_DOWN = (r"reduc\w*|decreas\w*|lower\w*|minimi[sz]\w*|cut\w*|shorten\w*|drop\w*|fall\w*|fell|below|less|"
         r"downward\w*|reliev\w*|eliminat\w*")
_IMPROVE = r"improv\w*|optimi[sz]\w*|better"
_NO_OVERLOAD = re.compile(r"\b(without|avoid\w*|prevent\w*|no)\b(\s+\w+)?\s+overload\w*", re.I)
_KEEP_HIGH = re.compile(r"\b(keep|kept|keeping|maintain\w*|stay\w*)\b(\s+\w+){0,3}\s+(high|higher)\b", re.I)
_CLAUSE = re.compile(r"(?<!\d)[.;!?](?!\d)|\s[—–-]\s|[()]|,|\bwhile\b|\bbut\b|\bwhereas\b", re.I)
_TOKEN = re.compile(r"[A-Za-z][A-Za-z\-]*")


def _matches(pattern: str, token: str) -> bool:
    return re.fullmatch(pattern, token, re.I) is not None


def stated_effect(text: str, metric: str) -> str | None:
    """'increase', 'decrease', 'mixed' or None: the effect the text states on ``metric``."""
    spec = METRICS.get(metric)
    if spec is None or not text:
        return None
    words, inverse, higher_better = spec
    votes: set[str] = set()
    for clause in _CLAUSE.split(text):
        if not clause or not clause.strip():
            continue
        tokens = [t.lower() for t in _TOKEN.findall(clause)]
        hits = [(i, False) for i, t in enumerate(tokens) if _matches(words, t)]
        hits += [(i, True) for i, t in enumerate(tokens) if inverse and _matches(inverse, t)]
        if metric == "resource_utilisation" and _NO_OVERLOAD.search(clause):
            votes.add("decrease")
        if not hits:
            continue
        if _KEEP_HIGH.search(clause) and any(not inv for _, inv in hits):
            votes.add("increase")
        for j, t in enumerate(tokens):
            if _matches(_UP, t):
                eff = "increase"
            elif _matches(_DOWN, t):
                eff = "decrease"
            elif _matches(_IMPROVE, t):
                eff = "increase" if higher_better else "decrease"
            else:
                continue
            near = sorted((abs(i - j), inv) for i, inv in hits if abs(i - j) <= WINDOW and i != j)
            if not near:
                continue
            flip = near[0][1]                    # the nearest metric word decides (inverse words flip)
            votes.add({"increase": "decrease", "decrease": "increase"}[eff] if flip else eff)
    if not votes:
        return None
    return votes.pop() if len(votes) == 1 else "mixed"


def direction_rank(text: str, metric: str, target_direction: str) -> int | None:
    """0 = stated effect matches the KPI's direction, 1 = no derivable direction, None = drop."""
    wanted = {"minimize": "decrease", "maximize": "increase"}.get((target_direction or "").lower())
    if wanted is None:
        return 1
    effect = stated_effect(text, metric)
    if effect is None:
        return 1
    return 0 if effect == wanted else None
