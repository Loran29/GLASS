"""
Stage-2 prerequisite verification.

Checks (in order):
  1. File existence  -- simod_raw.json, stage1_kpis/*.json, CSV logs
  2. Baseline build  -- build_baseline_scenario succeeds, no errors, activity names readable
  3. Name matching   -- every measurable_as value found in the BPMN-resolved activity list
  4. Prosimos smoke  -- runs 1 seed / 100 cases on bpic2017; checks activity column format
  5. KPI matching    -- compute_kpis + _match_kpi_value finds every target in the computed set
  6. Sepsis BPMN     -- if simod_raw.json exists, checks activity names vs measurable_as

Usage (from Thesis/goal_to_parameters/):
    python ../evaluation/verify_stage2_prereqs.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "goal_to_parameters"))

SIMOD_DIR  = REPO_ROOT / "evaluation" / "simod_outputs"
KPI_DIR    = REPO_ROOT / "evaluation" / "stage1_kpis"
CSV_DIR    = REPO_ROOT / "evaluation" / "logs" / "csv"
LOGS       = ["bpic2017", "bpic2012", "sepsis"]

PASS  = "  [PASS]"
FAIL  = "  [FAIL]"
WARN  = "  [WARN]"
SKIP  = "  [SKIP]"
HEAD  = lambda t: print(f"\n{'='*60}\n{t}\n{'='*60}".encode("ascii", "replace").decode("ascii"))

failures: list[str] = []


def ok(msg: str) -> None:
    print(f"{PASS} {msg}")


def fail(msg: str) -> None:
    print(f"{FAIL} {msg}")
    failures.append(msg)


def warn(msg: str) -> None:
    print(f"{WARN} {msg}")


def skip(msg: str) -> None:
    print(f"{SKIP} {msg}")


# -----------------------------------------------------------------------
# Check 1 -- File existence
# -----------------------------------------------------------------------

HEAD("CHECK 1 -- File existence")

for log in LOGS:
    simod = SIMOD_DIR / log / "simod_raw.json"
    kpi   = KPI_DIR  / f"{log}.json"
    csv   = CSV_DIR  / f"{log}.csv"

    if simod.exists():
        ok(f"{log}: simod_raw.json found")
    else:
        fail(f"{log}: simod_raw.json MISSING -- run run_simod_baselines.py first")

    if kpi.exists():
        d = json.loads(kpi.read_text())
        n = len(d.get("kpis", []))
        all_ma = all(k.get("measurable_as") for k in d.get("kpis", []))
        ok(f"{log}: stage1_kpis/{log}.json found ({n} KPIs, all measurable_as set={all_ma})")
        if not all_ma:
            fail(f"{log}: some KPIs missing measurable_as")
    else:
        fail(f"{log}: stage1_kpis/{log}.json MISSING")

    if csv.exists():
        size_mb = csv.stat().st_size / 1e6
        ok(f"{log}: {log}.csv found ({size_mb:.1f} MB)")
    else:
        fail(f"{log}: {log}.csv MISSING")


# -----------------------------------------------------------------------
# Check 2 -- build_baseline_scenario
# -----------------------------------------------------------------------

HEAD("CHECK 2 -- build_baseline_scenario (BPMN -> human activity names)")

from second_llm.simod_to_simubridge import build_baseline_scenario

baselines: dict[str, object] = {}

for log in ["bpic2017", "bpic2012"]:
    raw_path = SIMOD_DIR / log / "simod_raw.json"
    if not raw_path.exists():
        skip(f"{log}: skipped (simod_raw.json missing)")
        continue

    d = json.loads(raw_path.read_text())
    result = build_baseline_scenario(d.get("json_content"), bpmn_xml=d.get("bpmn_xml", ""))

    if not result.ok or result.scenario is None:
        fail(f"{log}: build_baseline_scenario failed -- {result.errors}")
        continue

    acts = result.scenario.models[0].modelParameter.activities if result.scenario.models else []
    names = [a.name for a in acts]
    uuid_count = sum(1 for n in names if n.startswith("node_") and len(n) > 20)

    ok(f"{log}: baseline built ok, {len(acts)} activities")
    if uuid_count > 0:
        fail(f"{log}: {uuid_count}/{len(acts)} activities still have UUID names "
             f"(BPMN name resolution failed)")
    else:
        ok(f"{log}: all activity names are human-readable")

    if result.notes:
        for note in result.notes[:5]:
            warn(f"{log}: {note}")

    baselines[log] = result.scenario
    print(f"         Activity names: {sorted(names)[:8]}{'...' if len(names) > 8 else ''}")


# -----------------------------------------------------------------------
# Check 3 -- measurable_as names exist in the activity set
# -----------------------------------------------------------------------

HEAD("CHECK 3 -- measurable_as targets vs resolved BPMN activity names")

GENERIC_KPIS = {
    "average cycle time", "average waiting time", "average processing time",
    "throughput", "resource utilization", "cost per case",
}

for log in ["bpic2017", "bpic2012"]:
    kpi_path = KPI_DIR / f"{log}.json"
    raw_path = SIMOD_DIR / log / "simod_raw.json"
    if not kpi_path.exists() or not raw_path.exists():
        skip(f"{log}: skipped (files missing)")
        continue

    d_raw = json.loads(raw_path.read_text())
    result = build_baseline_scenario(d_raw.get("json_content"), bpmn_xml=d_raw.get("bpmn_xml", ""))
    if not result.scenario:
        skip(f"{log}: skipped (baseline build failed)")
        continue

    acts = result.scenario.models[0].modelParameter.activities if result.scenario.models else []
    act_names_lower = {a.name.lower() for a in acts}

    d_kpi = json.loads(kpi_path.read_text())
    for kpi in d_kpi["kpis"]:
        ma = kpi.get("measurable_as", "")
        if not ma:
            fail(f"{log} / {kpi['name']}: measurable_as is empty")
            continue

        if ma.lower() in GENERIC_KPIS:
            ok(f"{log} / '{ma}': generic aggregate KPI (always computed)")
            continue

        # Activity-specific: expect "{activity_name} Waiting Time"
        if ma.lower().endswith(" waiting time"):
            act_part = ma[: -len(" Waiting Time")].strip()
            if act_part.lower() in act_names_lower:
                ok(f"{log} / '{ma}': activity '{act_part}' found in BPMN OK")
            else:
                fail(f"{log} / '{ma}': activity '{act_part}' NOT found in BPMN. "
                     f"Available: {sorted(a.name for a in acts if 'W_' in a.name or 'w_' in a.name.lower())}")
        else:
            warn(f"{log} / '{ma}': unrecognised measurable_as pattern -- manual check needed")


# -----------------------------------------------------------------------
# Check 4 -- Prosimos smoke test (Docker required)
# -----------------------------------------------------------------------

HEAD("CHECK 4 -- Prosimos smoke test (1 seed, 100 cases on bpic2017)")

from second_llm.prosimos_runner import is_docker_available, run_prosimos_simulation

if not is_docker_available():
    skip("Docker not available -- skipping Prosimos smoke test")
    warn("Build the Prosimos image first:  docker build -t glass/prosimos -f docker/Dockerfile.prosimos .")
    prosimos_log = None
else:
    if "bpic2017" not in baselines:
        skip("bpic2017 baseline not built -- skipping Prosimos smoke test")
        prosimos_log = None
    else:
        d_raw = json.loads((SIMOD_DIR / "bpic2017" / "simod_raw.json").read_text())
        bpmn_xml = d_raw.get("bpmn_xml", "")
        print("  Running Prosimos on bpic2017 (100 cases, seed=42) ...")

        pres = run_prosimos_simulation(
            baselines["bpic2017"],
            bpmn_xml,
            total_cases=100,
            seed=42,
        )

        if pres.error:
            fail(f"Prosimos simulation failed: {pres.error}")
            prosimos_log = None
        else:
            prosimos_log = pres.simulated_log
            ok(f"Prosimos ran successfully: {len(prosimos_log)} event rows")

            # Check activity column content
            acts_in_log = prosimos_log["activity"].unique().tolist()
            uuid_acts = [a for a in acts_in_log if str(a).startswith("node_") and len(str(a)) > 20]

            if uuid_acts:
                fail(f"Prosimos output contains UUID activity names: {uuid_acts[:5]}")
                fail("Activity-specific KPIs (W_Validate application Waiting Time etc.) WILL fail to match")
            else:
                ok(f"Prosimos output uses human-readable activity names OK")
                print(f"         Activities in log: {sorted(str(a) for a in acts_in_log)[:8]}")

            # Check row completeness
            required_cols = {"case_id", "activity", "resource", "start_time", "end_time"}
            missing_cols = required_cols - set(prosimos_log.columns)
            if missing_cols:
                fail(f"Simulated log missing columns: {missing_cols}")
            else:
                ok(f"Simulated log has all required columns")


# -----------------------------------------------------------------------
# Check 5 -- compute_kpis + _match_kpi_value for all targets
# -----------------------------------------------------------------------

HEAD("CHECK 5 -- compute_kpis output + _match_kpi_value matching")

if prosimos_log is None:
    skip("Skipped (no Prosimos output from Check 4)")
else:
    from second_llm.kpi_computation import compute_kpis
    from second_llm.scenario_evaluation import _match_kpi_value, KPITarget, TargetDirection

    kpi_result = compute_kpis(prosimos_log)

    if kpi_result.error:
        fail(f"compute_kpis failed: {kpi_result.error}")
    else:
        computed_names = [k.name for k in kpi_result.kpis]
        ok(f"compute_kpis produced {len(computed_names)} KPIs")
        print(f"         Computed names: {sorted(computed_names)}")

        # Check matching for bpic2017 targets against this log
        d_kpi = json.loads((KPI_DIR / "bpic2017.json").read_text())
        print()
        print("  Matching bpic2017 KPI targets:")
        for kpi in d_kpi["kpis"]:
            name = kpi["name"]
            ma   = kpi.get("measurable_as")
            cat  = kpi.get("category", "")
            match = _match_kpi_value(name, kpi_result, target_category=cat, measurable_as=ma)
            if match:
                ok(f"  '{name}' -> matched '{match.name}' (value={match.value})")
            else:
                fail(f"  '{name}' (measurable_as='{ma}') -> NO MATCH -- will be not_computable in evaluation")


# -----------------------------------------------------------------------
# Check 6 -- Sepsis BPMN names (if simod_raw.json exists)
# -----------------------------------------------------------------------

HEAD("CHECK 6 -- Sepsis BPMN activity names")

sepsis_raw = SIMOD_DIR / "sepsis" / "simod_raw.json"
if not sepsis_raw.exists():
    skip("sepsis/simod_raw.json not found -- run SIMOD on sepsis first (requires Docker)")
    warn("Once Docker is running:  cd goal_to_parameters && python ../evaluation/run_simod_baselines.py")
else:
    d = json.loads(sepsis_raw.read_text())
    result = build_baseline_scenario(d.get("json_content"), bpmn_xml=d.get("bpmn_xml", ""))

    if not result.scenario:
        fail(f"sepsis: build_baseline_scenario failed -- {result.errors}")
    else:
        acts = result.scenario.models[0].modelParameter.activities if result.scenario.models else []
        act_names_lower = {a.name.lower() for a in acts}
        ok(f"sepsis: baseline built, {len(acts)} activities: {sorted(a.name for a in acts)}")

        d_kpi = json.loads((KPI_DIR / "sepsis.json").read_text())
        for kpi in d_kpi["kpis"]:
            ma = kpi.get("measurable_as", "")
            if ma.lower() in GENERIC_KPIS:
                ok(f"sepsis / '{ma}': generic aggregate KPI OK")
            elif ma.lower().endswith(" waiting time"):
                act_part = ma[: -len(" Waiting Time")].strip()
                if act_part.lower() in act_names_lower:
                    ok(f"sepsis / '{ma}': activity '{act_part}' found OK")
                else:
                    fail(f"sepsis / '{ma}': activity '{act_part}' NOT found in BPMN. "
                         f"Available: {sorted(a.name for a in acts)}")


# -----------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------

HEAD("SUMMARY")

if failures:
    print(f"\n  {len(failures)} issue(s) found:\n")
    for i, f_msg in enumerate(failures, 1):
        print(f"  {i}. {f_msg}")
    print()
    sys.exit(1)
else:
    print("\n  All checks passed. Stage 2 is ready to run.\n")
    sys.exit(0)
