"""Pre-built example bundles for the Scenario Studio.

Each example contains a realistic first-LLM output JSON and a SIMOD output
so the workspace can be populated with a single click.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

# Evaluation artifacts live two levels up from this file (goal_to_parameters/examples/)
_EVAL_DIR = Path(__file__).parent.parent.parent / "evaluation"


def _load_eval_example(log_name: str) -> "SecondLLMExample | None":
    """Load first-LLM JSON and SIMOD output from the evaluation directory."""
    kpi_path   = _EVAL_DIR / "stage1_kpis"  / f"{log_name}.json"
    simod_path = _EVAL_DIR / "simod_outputs" / log_name / "simod_raw.json"
    if not kpi_path.exists() or not simod_path.exists():
        return None
    try:
        simod_data = json.loads(simod_path.read_text())
        return {
            "label":          log_name,
            "first_llm_json": kpi_path.read_text(),
            "simod_output":   simod_data.get("json_text", ""),
            "bpmn_xml":       simod_data.get("bpmn_xml", ""),
        }
    except Exception:
        return None


class SecondLLMExample(TypedDict):
    label: str
    first_llm_json: str
    simod_output: str
    bpmn_xml: str


EXAMPLES: dict[str, SecondLLMExample] = {
    "BPIC 2017": _load_eval_example("bpic2017") or {},
    "BPIC 2012": _load_eval_example("bpic2012") or {},
    "Sepsis":    _load_eval_example("sepsis")    or {},
    "Insurance Claim": {
        "label": "Insurance Claim (Context-Aware)",
        "first_llm_json": json.dumps(
            {
                "simulation_goal_structured": (
                    "Primary objective: minimise average claim decision cycle time from "
                    "submission to customer notification (target direction: minimize). "
                    "Constraint: maintain responsive service across claim types, channels, "
                    "customer tiers, and priority levels. "
                    "Scope: end-to-end insurance claim process."
                ),
                "kpis": [
                    {
                        "name": "Claim Decision Cycle Time",
                        "description": "Total elapsed time from claim submission to customer notification of the decision.",
                        "category": "time",
                        "smart_breakdown": {
                            "specific": "Measures end-to-end elapsed time of the claim handling process.",
                            "measurable": "Difference between notification timestamp and submission timestamp, in hours.",
                            "achievable": "Current median is 72 hours; a target of 56 hours is feasible with faster document completeness checks.",
                            "relevant": "Directly addresses the primary goal of reducing claim decision cycle time.",
                            "time_bound": "Measured per claim, aggregated weekly.",
                        },
                        "target_direction": "minimize",
                        "suggested_formula": "end_timestamp(Notify Customer) - start_timestamp(Submit Claim)",
                        "supported_by_log": True,
                        "evidence_basis": "both",
                        "process_scope": "end_to_end",
                        "context_segmentation": [
                            {
                                "condition": "customer_tier = premium",
                                "target": "Cycle time <= 48 hours",
                                "rationale": "Premium customers have SLA commitments requiring faster turnaround.",
                                "evidence_factor": "customer_tier",
                                "evidence_metric": "median_cycle_time",
                                "adjusted_p_value": 0.002,
                                "effect_size": 0.58,
                                "sample_size": 95,
                                "observed_baseline": 54.0,
                                "target_type": "direct",
                            },
                            {
                                "condition": "claim_type = complex",
                                "target": "Cycle time <= 96 hours",
                                "rationale": "Complex claims require fraud review and additional assessment steps.",
                                "evidence_factor": "claim_type",
                                "evidence_metric": "median_cycle_time",
                                "adjusted_p_value": 0.001,
                                "effect_size": 0.72,
                                "sample_size": 60,
                                "observed_baseline": 108.0,
                                "target_type": "direct",
                            },
                        ],
                    },
                    {
                        "name": "Document Completeness Rate",
                        "description": "Percentage of claims submitted with all required documents on the first attempt.",
                        "category": "quality",
                        "smart_breakdown": {
                            "specific": "Tracks the proportion of claims that pass the initial document completeness check without requiring follow-up.",
                            "measurable": "Claims not triggering a 'request missing documents' activity divided by total claims, as a percentage.",
                            "achievable": "Current rate is 68%; improving to 75% is feasible with better submission guidance.",
                            "relevant": "Higher first-time completeness directly reduces cycle time by eliminating waiting for missing documents.",
                            "time_bound": "Measured per claim, aggregated monthly.",
                        },
                        "target_direction": "maximize",
                        "suggested_formula": "count(no_doc_request) / count(all_claims) * 100",
                        "supported_by_log": True,
                        "evidence_basis": "event_log_only",
                        "process_scope": "activity_level",
                        "context_segmentation": [],
                    },
                ],
                "reasoning": (
                    "Cycle time with context segmentation captures the primary goal while "
                    "respecting that different customer tiers and claim complexities have "
                    "different realistic targets. Document completeness is a key driver of "
                    "cycle time that can be improved upstream."
                ),
            },
            indent=2,
        ),
        "simod_output": json.dumps(
            {
                "process_name": "Insurance Claim Handling",
                "resource_profiles": {
                    "Claims Agent": {"count": 4, "cost_per_hour": 38.0, "calendar": "OfficeHours"},
                    "Claim Assessor": {"count": 3, "cost_per_hour": 52.0, "calendar": "OfficeHours"},
                    "Fraud Reviewer": {"count": 1, "cost_per_hour": 65.0, "calendar": "OfficeHours"},
                    "Supervisor": {"count": 2, "cost_per_hour": 70.0, "calendar": "OfficeHours"},
                },
                "calendars": {
                    "OfficeHours": {
                        "Monday-Friday": {"start": "08:30", "end": "17:30"},
                    }
                },
                "arrival_distribution": {
                    "type": "exponential",
                    "mean_inter_arrival_hours": 3.5,
                },
                "task_durations": {
                    "Submit Claim": {"distribution": "fixed", "mean_hours": 0.1},
                    "Check Documents": {"distribution": "normal", "mean_hours": 0.8, "std_hours": 0.25},
                    "Request Missing Documents": {"distribution": "exponential", "mean_hours": 24.0},
                    "Assess Claim": {"distribution": "normal", "mean_hours": 2.5, "std_hours": 0.8},
                    "Fraud Review": {"distribution": "normal", "mean_hours": 3.0, "std_hours": 1.0},
                    "Supervisor Decision": {"distribution": "normal", "mean_hours": 1.0, "std_hours": 0.3},
                    "Notify Customer": {"distribution": "fixed", "mean_hours": 0.15},
                },
                "gateway_probabilities": {
                    "Documents Complete?": {"yes": 0.68, "no": 0.32},
                    "Fraud Review Needed?": {"yes": 0.15, "no": 0.85},
                    "Decision": {"approved": 0.72, "rejected": 0.28},
                },
            },
            indent=2,
        ),
    },
    "Loan Application": {
        "label": "Loan Application",
        "first_llm_json": json.dumps(
            {
                "simulation_goal_structured": (
                    "Primary objective: minimise average cycle time from application "
                    "submission to final decision (target direction: minimize). "
                    "Secondary objective: maximise loan-officer utilisation during "
                    "business hours (target direction: maximize). "
                    "Constraint: maintain credit-check accuracy above 95%. "
                    "Scope: end-to-end loan application process."
                ),
                "kpis": [
                    {
                        "name": "Application-to-Decision Cycle Time",
                        "description": "Elapsed time from loan application submission to final approval or rejection decision.",
                        "category": "time",
                        "smart_breakdown": {
                            "specific": "Measures the total elapsed time from when a customer submits a loan request to when a senior manager issues the final decision.",
                            "measurable": "Calculated as the difference between the end timestamp of the final decision activity and the start timestamp of the submission activity, measured in hours.",
                            "achievable": "Current median is 48 hours; a 20% reduction to ~38 hours is feasible by reducing inter-activity waiting time.",
                            "relevant": "Directly addresses the primary simulation goal of reducing customer waiting time.",
                            "time_bound": "Measured per case over a rolling 30-day window.",
                        },
                        "target_direction": "minimize",
                        "suggested_formula": "end_timestamp(Final Decision) - start_timestamp(Submit Application)",
                        "supported_by_log": True,
                        "evidence_basis": "both",
                        "process_scope": "end_to_end",
                        "context_segmentation": [],
                    },
                    {
                        "name": "Loan Officer Utilisation Rate",
                        "description": "Proportion of available working hours during which loan officers are actively processing applications.",
                        "category": "utilization",
                        "smart_breakdown": {
                            "specific": "Measures the fraction of scheduled working hours that loan officers spend on active case work.",
                            "measurable": "Sum of active processing durations for loan officers divided by total available hours, expressed as a percentage.",
                            "achievable": "Current utilisation is around 62%; a target of 75-80% balances throughput with buffer for peak loads.",
                            "relevant": "Directly addresses the secondary goal of improving loan-officer utilisation.",
                            "time_bound": "Aggregated weekly.",
                        },
                        "target_direction": "maximize",
                        "suggested_formula": "sum(active_processing_time) / sum(available_hours) * 100",
                        "supported_by_log": True,
                        "evidence_basis": "event_log_only",
                        "process_scope": "activity_level",
                        "context_segmentation": [],
                    },
                ],
                "reasoning": "Cycle time and utilisation cover both objectives of the simulation goal.",
            },
            indent=2,
        ),
        "simod_output": json.dumps(
            {
                "process_name": "Loan Application",
                "resource_profiles": {
                    "Loan Officer": {"count": 3, "cost_per_hour": 45.0, "calendar": "BankHours"},
                    "Credit Assessment Team": {"count": 2, "cost_per_hour": 55.0, "calendar": "BankHours"},
                    "Risk Department": {"count": 2, "cost_per_hour": 60.0, "calendar": "BankHours"},
                    "Senior Manager": {"count": 1, "cost_per_hour": 85.0, "calendar": "BankHours"},
                },
                "calendars": {
                    "BankHours": {
                        "Monday-Friday": {"start": "08:00", "end": "17:00"},
                    }
                },
                "arrival_distribution": {"type": "exponential", "mean_inter_arrival_hours": 2.4},
                "task_durations": {
                    "Submit Application": {"distribution": "fixed", "mean_hours": 0.1},
                    "Review Application": {"distribution": "normal", "mean_hours": 1.5, "std_hours": 0.4},
                    "Credit Check": {"distribution": "normal", "mean_hours": 2.0, "std_hours": 0.6},
                    "Risk Evaluation": {"distribution": "normal", "mean_hours": 1.8, "std_hours": 0.5},
                    "Final Decision": {"distribution": "normal", "mean_hours": 0.5, "std_hours": 0.15},
                },
                "gateway_probabilities": {
                    "Documents Complete?": {"yes": 0.72, "no": 0.28},
                    "Approved?": {"approved": 0.65, "rejected": 0.35},
                },
            },
            indent=2,
        ),
    },
}

# Remove any entries that failed to load (e.g. evaluation files not present)
EXAMPLES = {k: v for k, v in EXAMPLES.items() if v}


def get_example_names() -> list[str]:
    """Return the list of available example names."""
    return list(EXAMPLES.keys())


def get_example(name: str) -> SecondLLMExample | None:
    """Return a specific example bundle, or None if not found."""
    return EXAMPLES.get(name)

