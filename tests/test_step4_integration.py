"""Integration test for Step 4: RAG retrieval pipeline."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "goal_to_parameters"))

from knowledge.retrieval import (
    build_second_llm_evidence,
    filter_simod_baseline,
    filter_log_evidence,
    filter_context_evidence,
    _match_goal_categories,
    SecondLLMEvidence,
)
from knowledge.models import GoalCategory

# -------------------------------------------------------------------
# Test data: Loan Application (from examples)
# -------------------------------------------------------------------

FIRST_LLM_PARSED = {
    "simulation_goal_structured": "Minimize loan processing cycle time while maintaining approval accuracy",
    "kpis": [
        {
            "name": "Average Loan Processing Time",
            "category": "time",
            "target_direction": "minimize",
            "process_scope": "end-to-end",
            "suggested_formula": "avg(case_duration)",
            "context_segmentation": [
                {
                    "condition": "loan_amount > 50000",
                    "target": "< 3 days",
                    "evidence_factor": "loan_amount",
                }
            ],
        },
        {
            "name": "Approval Accuracy Rate",
            "category": "quality",
            "target_direction": "maintain",
            "process_scope": "end-to-end",
            "suggested_formula": "1 - (rework_cases / total_cases)",
        },
    ],
}

SIMOD_JSON = {
    "process_name": "LoanApplication",
    "task_durations": {
        "Receive Application": {"mean_hours": 0.5, "distribution": "exponential"},
        "Verify Documents": {"mean_hours": 4.2, "distribution": "normal"},
        "Credit Check": {"mean_hours": 2.1, "distribution": "normal"},
        "Risk Assessment": {"mean_hours": 6.0, "distribution": "normal"},
        "Manager Approval": {"mean_hours": 8.5, "distribution": "exponential"},
        "Notify Applicant": {"mean_hours": 0.3, "distribution": "exponential"},
    },
    "resource_profiles": {
        "Loan Officer": {"count": 3, "cost_per_hour": 45},
        "Risk Analyst": {"count": 2, "cost_per_hour": 65},
        "Branch Manager": {"count": 1, "cost_per_hour": 85},
    },
    "gateway_probabilities": {
        "GW_DocumentCheck": {"pass": 0.85, "fail_rework": 0.15},
        "GW_RiskLevel": {"low": 0.60, "medium": 0.30, "high": 0.10},
    },
    "arrival_time_distribution": {"type": "exponential", "mean_hours": 2.0},
    "resource_calendars": {
        "default": {"Monday-Friday": "09:00-17:00"},
    },
}

LOG_PROFILE = {
    "summary": {"total_cases": 1500, "total_events": 12000, "avg_duration_hours": 72},
    "measurable_signals": ["cycle_time", "rework_rate", "resource_utilization"],
    "duration_indicators": {"mean_hours": 72, "median_hours": 48, "p90_hours": 120},
    "top_activities": [
        {"name": "Manager Approval", "count": 1500, "avg_duration_hours": 8.5},
        {"name": "Risk Assessment", "count": 1500, "avg_duration_hours": 6.0},
        {"name": "Verify Documents", "count": 1650, "avg_duration_hours": 4.2},
        {"name": "Credit Check", "count": 1500, "avg_duration_hours": 2.1},
        {"name": "Receive Application", "count": 1500, "avg_duration_hours": 0.5},
        {"name": "Notify Applicant", "count": 1500, "avg_duration_hours": 0.3},
    ],
    "top_resources": [
        {"name": "Loan Officer", "event_count": 5000},
        {"name": "Risk Analyst", "event_count": 3000},
        {"name": "Branch Manager", "event_count": 1500},
    ],
    "top_variants": [
        {"variant": "A>B>C>D>E>F", "count": 900},
        {"variant": "A>B>C>B>C>D>E>F", "count": 350},
    ],
    "top_transitions": [
        {"from": "Verify Documents", "to": "Credit Check", "count": 1400},
        {"from": "Credit Check", "to": "Risk Assessment", "count": 1500},
    ],
    "rework_activity_case_counts": [
        {"name": "Verify Documents", "rework_cases": 150},
    ],
}

CONTEXT_PROFILE = {
    "summary": {"total_factors": 3, "significant_count": 2},
    "detected_factors": [
        {"name": "loan_amount", "scope": "case_level"},
        {"name": "day_of_week", "scope": "temporal"},
    ],
    "analysis": {
        "significance_threshold": 0.05,
        "fdr_method": "benjamini-hochberg",
        "significant_relationships": [
            {
                "factor": "loan_amount",
                "metric": "cycle_time",
                "p_value": 0.001,
                "effect_size": 0.45,
            },
            {
                "factor": "day_of_week",
                "metric": "wait_time",
                "p_value": 0.02,
                "effect_size": 0.30,
            },
        ],
    },
}


# -------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------

def test_goal_category_matching():
    cats = _match_goal_categories(
        FIRST_LLM_PARSED["simulation_goal_structured"],
        FIRST_LLM_PARSED["kpis"],
    )
    assert GoalCategory.WAITING_TIME in cats, f"Expected WAITING_TIME in {cats}"
    assert GoalCategory.QUALITY_COMPLIANCE in cats, f"Expected QUALITY_COMPLIANCE in {cats}"
    print(f"  goal categories: {sorted(c.value for c in cats)}")


def test_simod_filtering():
    cats = _match_goal_categories(
        FIRST_LLM_PARSED["simulation_goal_structured"],
        FIRST_LLM_PARSED["kpis"],
    )
    filtered = filter_simod_baseline(SIMOD_JSON, cats, FIRST_LLM_PARSED["kpis"])
    assert "_annotations" in filtered, "Expected _annotations in filtered SIMOD"
    bottlenecks = filtered["_annotations"].get("bottleneck_activities", [])
    assert len(bottlenecks) > 0, "Expected bottleneck activities"
    assert bottlenecks[0] == "Manager Approval", f"Top bottleneck should be Manager Approval, got {bottlenecks[0]}"
    print(f"  bottlenecks: {bottlenecks}")
    rework = filtered["_annotations"].get("probable_rework_gateways", [])
    print(f"  rework gateways: {rework}")
    print(f"  sections: {[k for k in filtered if not k.startswith('_')]}")


def test_log_filtering():
    cats = _match_goal_categories(
        FIRST_LLM_PARSED["simulation_goal_structured"],
        FIRST_LLM_PARSED["kpis"],
    )
    filtered = filter_log_evidence(LOG_PROFILE, cats, FIRST_LLM_PARSED["kpis"])
    assert "duration_indicators" in filtered, "Expected duration_indicators in log evidence"
    assert "summary" in filtered, "Expected summary"
    kpi_acts = filtered.get("_kpi_relevant_activities", [])
    print(f"  log sections: {[k for k in filtered if not k.startswith('_')]}")
    print(f"  KPI-relevant activities: {kpi_acts}")


def test_context_filtering():
    cats = _match_goal_categories(
        FIRST_LLM_PARSED["simulation_goal_structured"],
        FIRST_LLM_PARSED["kpis"],
    )
    filtered = filter_context_evidence(CONTEXT_PROFILE, cats, FIRST_LLM_PARSED["kpis"])
    assert filtered is not None, "Expected non-None context filtering result"
    sig = filtered.get("significant_relationships", [])
    assert len(sig) > 0, "Expected at least one significant relationship"
    # loan_amount should be boosted to front (KPI-referenced)
    first_factor = sig[0].get("factor", "")
    assert first_factor == "loan_amount", f"Expected loan_amount first, got {first_factor}"
    print(f"  significant relationships: {len(sig)}")
    print(f"  first factor (boosted): {first_factor}")


def test_full_evidence_pipeline():
    evidence = build_second_llm_evidence(
        goal_structured=FIRST_LLM_PARSED["simulation_goal_structured"],
        kpis=FIRST_LLM_PARSED["kpis"],
        simod_json=SIMOD_JSON,
        log_profile=LOG_PROFILE,
        context_profile=CONTEXT_PROFILE,
    )
    assert isinstance(evidence, SecondLLMEvidence)
    assert len(evidence.kb_json) > 0, "KB JSON should not be empty"
    assert len(evidence.simod_json) > 0, "SIMOD JSON should not be empty"
    assert len(evidence.log_json) > 0, "Log JSON should not be empty"
    assert len(evidence.context_json) > 0, "Context JSON should not be empty"
    assert len(evidence.matched_goal_categories) >= 2
    assert len(evidence.retrieval_notes) >= 4

    print(f"  matched categories: {evidence.matched_goal_categories}")
    print(f"  KB JSON size: {len(evidence.kb_json)} chars")
    print(f"  SIMOD JSON size: {len(evidence.simod_json)} chars")
    print(f"  Log JSON size: {len(evidence.log_json)} chars")
    print(f"  Context JSON size: {len(evidence.context_json)} chars")
    print(f"  Retrieval notes:")
    for note in evidence.retrieval_notes:
        print(f"    - {note}")


if __name__ == "__main__":
    tests = [
        ("Goal-category matching", test_goal_category_matching),
        ("SIMOD filtering", test_simod_filtering),
        ("Log evidence filtering", test_log_filtering),
        ("Context evidence filtering", test_context_filtering),
        ("Full evidence pipeline", test_full_evidence_pipeline),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        print(f"\n[TEST] {name}")
        try:
            fn()
            print(f"  PASSED")
            passed += 1
        except Exception as e:
            print(f"  FAILED: {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)}")
    if failed == 0:
        print("All Step 4 integration tests passed!")
