"""Quick smoke test: V_full on bpic2017, 1 seed, max_retries=1."""
from __future__ import annotations
import json, os, sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "goal_to_parameters"))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / "goal_to_parameters" / ".env")

from llm.openrouter_provider import OpenRouterProvider
from second_llm.scenario_generator import generate_scenario_patch
from second_llm.multi_seed_evaluation import evaluate_multi_seed
from second_llm.simod_to_simubridge import build_baseline_scenario
from second_llm.scenario_evaluation import KPITarget, TargetDirection
from utils.log_processing import profile_event_log
from models.smart_kpi import SMARTKpi as SmartKPI

SIMOD_DIR = REPO_ROOT / "evaluation" / "simod_outputs"
KPI_DIR   = REPO_ROOT / "evaluation" / "stage1_kpis"
CSV_DIR   = REPO_ROOT / "evaluation" / "logs" / "csv"

api_key = os.getenv("OPENROUTER_API_KEY", "")
provider = OpenRouterProvider(api_key=api_key, model="openai/gpt-4o-mini")

CHAT_HISTORY = [
    {
        "role": "assistant",
        "content": (
            "SIMOD shows 3 resources assigned to W_Validate application — "
            "the activity with the longest average wait time in the baseline. "
            "Is that headcount fixed, or can we add the one additional specialist "
            "mentioned in the goal, and if so, would they cover W_Validate "
            "application only or also W_Assess potential fraud?"
        ),
    },
    {
        "role": "user",
        "content": (
            "At most one additional specialist total, shared across both "
            "W_Validate application and W_Assess potential fraud — they draw "
            "from the same resource pool."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "Confirmed: validation headcount is capped at +1 across both roles. "
            "The baseline also shows W_Complete application contributing to queue "
            "build-up on busy days — is there a monthly budget ceiling for "
            "overtime on these work-queue activities, or are weekday shift "
            "extensions possible within the current budget?"
        ),
    },
    {
        "role": "user",
        "content": (
            "Up to 10% overtime is allowed, but only on weekdays. "
            "No weekend shifts, and there is no separate overtime budget — "
            "the 10% cap is the hard limit."
        ),
    },
]

# Load bpic2017
simod = json.loads((SIMOD_DIR / "bpic2017" / "simod_raw.json").read_text())
kpi_data = json.loads((KPI_DIR / "bpic2017.json").read_text())

baseline_result = build_baseline_scenario(simod.get("json_content"), bpmn_xml=simod.get("bpmn_xml", ""))
assert baseline_result.scenario, f"Baseline failed: {baseline_result.errors}"
print(f"Baseline built: {len(baseline_result.scenario.models[0].modelParameter.activities)} activities")

with open(CSV_DIR / "bpic2017.csv", "rb") as fh:
    log_profile = profile_event_log(fh)
print("Log profile loaded")

kpis = [SmartKPI(**k) for k in kpi_data["kpis"]]
targets = [
    KPITarget(
        name=k.name,
        direction=TargetDirection(k.target_direction.value if hasattr(k.target_direction, "value") else k.target_direction),
        category=k.category.value if hasattr(k.category, "value") else str(k.category),
        measurable_as=k.measurable_as,
    )
    for k in kpis
]
print(f"Targets: {[t.name for t in targets]}")

print("\n--- Calling generate_scenario_patch ---")
kpi_json_text = json.dumps(kpi_data)
patch_result = generate_scenario_patch(
    provider=provider,
    first_llm_json=kpi_json_text,
    first_llm_parsed=kpi_data,
    simod_raw_text=simod.get("json_text", ""),
    simod_json_content=simod.get("json_content"),
    bpmn_xml=simod.get("bpmn_xml", ""),
    chat_history=CHAT_HISTORY,
    log_profile=log_profile,
    max_retries=1,
    temperature=0.3,
)
print(f"Patch ok={patch_result.success}, attempts={patch_result.attempts}")
if patch_result.error:
    print(f"Error: {patch_result.error}")
if patch_result.success and patch_result.proposal is not None:
    print(f"Proposal generated ok")
    from models import KPIGenerationResult
    kpi_result = KPIGenerationResult.model_validate(kpi_data)
    targets = [
        KPITarget(
            name=k.name,
            direction=TargetDirection(k.target_direction.value),
            category=k.category.value if hasattr(k.category, "value") else str(k.category),
            measurable_as=k.measurable_as,
        )
        for k in kpi_result.kpis
    ]

    proposal_scenario = patch_result.proposal.scenario if hasattr(patch_result.proposal, "scenario") else patch_result.proposal

    print("\n--- Running evaluate_multi_seed (1 seed) ---")
    multi = evaluate_multi_seed(
        baseline_scenario=baseline_result.scenario,
        proposed_scenario=proposal_scenario,
        bpmn_xml=simod.get("bpmn_xml", ""),
        targets=targets,
        num_seeds=1,
    )
    print(f"Multi-seed ok={multi.ok}")
    if multi.ok:
        for comp in multi.kpi_comparisons:
            print(f"  {comp.kpi_name}: improved={comp.improved}, delta={comp.mean_percentage_change:.1f}%")
