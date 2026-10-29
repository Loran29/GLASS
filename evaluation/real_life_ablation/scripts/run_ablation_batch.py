"""
Real-life ablation, Stage 2: every configuration in setup/ablation_config.json x variant x repetition, run
through GLASS's own Stage-2 code as a library (derived from evaluation/loanapp_p2p/scripts/run_stage2_batch.py,
whose recording, seed shim, capture wrappers, KPI table and log preprocessing are reused unchanged).

Inputs per configuration
  baseline   data/<dataset>/: the package's chosen BPMN + Prosimos JSON (validated in baselines/), cost_per_hour
             20 for every resource (business context), loaded with accept_simod_raw_input -> build_baseline_scenario
  KPIs       setup/stage2_kpis.json (Stage-1 ablation, variant full; selection rule in run_stage1_ablation.py)
  context    the three business-context answers as the clarification chat (user turns). GLASS's context summary
             (what reaches the prompt) drops the staffing rule and the 20 EUR rate (seen in the smoke runs; GLASS
             behaviour, unchanged); the batch's validity check enforces both.
  log        the test log's profile (app.py extract_log_artifacts on the decompressed test log)
  settings   gpt-4o-mini; GLASS's loop temperatures; max_iterations 4; 10 seeds (42..51); the test log's case
             count and first start

Variants (--variant; each a switch on the same code path, recorded in every run record as `variant` and
`switches`):
  V_full        everything as above
  V_no_log      no log profile: log_profile=None for the draft payload and the loop (no log or context evidence
                in the Stage-2 prompt)
  V_no_context  no clarification chat (GLASS defaults: no operational-constraint summary)
  V_no_kb       knowledge base off, in generation and feedback: knowledge.retrieval.retrieve_for_second_llm is
                replaced by a stub that makes no retrieval (calls counted in the record as kb_stub_calls), the
                evidence's kb_json is emptied so the prompt has no knowledge-base section, and the feedback's
                alternative-intervention retrieval returns nothing, so the feedback has no literature section
  V_no_refine   max_iterations = 1
  V_old_refine  run_iterative_evaluation(legacy_refinement=True): stop at the first iteration whose overall
                status is improved, best iteration by score alone (GLASS commit "Refinement: optional earlier
                stop and selection rules for ablation")

Records go to <out>/raw/<config_id>__<variant>/rep_<k>.json; reruns resume (final statuses are skipped).
Simulated logs are written next to the records and kept out of git (SHA-256 in the records).

Usage (repo root):
  python evaluation/real_life_ablation/scripts/run_ablation_batch.py [--variant V[,V]] [--only CONFIG[,CONFIG]]
         [--reps 1,2,3] [--workers N] [--out DIR] [--smoke]
--smoke = --only BPIC_2012_W_G1G2 --reps 1 for all six variants, to --out, plus smoke_report.txt.

SCORING (analysis only; the recording is never changed by scoring). Per run, for the initial (iteration 1)
and the final (the loop's best iteration) scenario:
Primary
  scenario_produced  the iteration yielded a merged scenario
  valid           the patch validator passed for the accepted patch and the business context holds on the
                  merged scenario:
                  staffing   each resource profile (role) at most + 1 resource (a new role at most 1); no total
                             headcount cap (the staff-cost limit is the total constraint; see the README)
                  hours      every resource's working time on every weekday at most + 2 h over its baseline
                             calendar (a new resource: its role's baseline resources' longest day; a resource of
                             a new role: the longest baseline day)
                  staff cost weekly staff cost at most + 35 %: per resource 20 EUR x weekly hours of its own
                             calendar, else its role's schedule (GLASS's staff_cost_per_week, which counts headcount
                             and calendar extensions, with the business context's fixed rate in place of costHour;
                             Stage 2 on LoanApp/P2P used the hourly headcount cost instead)
                  rate       every role's costHour stays 20 EUR (rate_changed otherwise; the business context fixes
                             the rate, so a lower rate cannot pay for more staff)
                  immutable  every baseline activity is still present and no activity is added
                  demand     the merged scenario's start-event inter-arrival time and arrival calendar equal the
                             baseline's (demand_changed otherwise): the arrival rate is customer demand, not a
                             company lever (lead author's decision before the runs; an inter_arrival_time
                             modification the merger skipped does not count, but is reported as demand_proposed)
                  Branching probabilities may change (they change how often a path is taken, not the order).
  executable      the 10-seed evaluation simulated on all seeds
  merge_stability applied / proposed modifications of the accepted attempt: proposed = modifications in the
                  LLM's answer (before GLASS's patch-reject and constraint steps), applied = GLASS's merge count;
                  no modification proposed = None (not > 0). GLASS's own value (applied / modifications reaching
                  the merger, 0/0 = 1.0) is recorded as glass_merge_stability (lead author's decision)
  admissible      valid, executable and merge_stability > 0
  KPIs            per KPI from evaluate_multi_seed (paired with the cached 10-seed baseline): means, 95 % CI,
                  paired t-test p-value, significant (GLASS, p < 0.05), improved (GLASS's direction rule)
                  all_kpis_improved = every KPI improved
  DHR             share of the evaluated KPIs that improved (thesis definition)
  NIS             mean over the KPIs of |% change| signed + if improved, - otherwise (thesis definition)
Secondary
  change profile  number of modifications per parameter_type; share of modifications whose target is X (the
                  configuration's dataset's X activity) or a role / resource that performs X in the baseline
  distance        CTD, RED, CWD between each of the 10 simulated logs and the real test log, averaged; simulated
                  logs get the midnight-end mapping (an end of exactly 00:00:00 directly after the resource's
                  working day is mapped back to that end); the test log is not changed. The baseline's distance
                  is recorded per dataset for reference.
  loop_discarded_better_initial  final != initial, the initial is admissible, and (a) the final is not
                  admissible, or (b) DHR_initial > DHR_final, or (c) equal DHR and NIS_initial - NIS_final > 1
                  percentage point; the condition that fired is recorded
  iterations_used number of loop iterations
  no_improving_scenario  GLASS v3.2: no iteration reached a positive score, so the loop kept the baseline; the
                  final then has no scenario (scenario_produced false, reason no_improving_scenario, not admissible;
                  the best iteration is recorded as candidate_iteration). loop_discarded_better_initial counts such a
                  run only if the initial was admissible and better than the baseline on the 10 seeds (DHR > 0 or
                  NIS > +1 percentage point): condition "a: final not admissible (baseline kept)"
  direction_inconsistent_rejections  GLASS v3.2: modifications rejected because their stated direction
                  contradicts their values (validator diagnostics over all attempts)
END SCORING
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
import time
import traceback
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent                 # evaluation/real_life_ablation
REPO = BASE.parent.parent
sys.path.insert(0, str(REPO / "goal_to_parameters"))
sys.path.insert(0, str(REPO / "evaluation" / "loanapp_p2p" / "scripts"))
import run_stage1_batch as s1  # noqa: E402  (provider, recording, JSON helpers, app.py Stage-1 loader)
import run_stage2_batch as s2  # noqa: E402  (seed shim, capture wrappers, KPI table, log preprocessing)

CONFIG_PATH = BASE / "setup" / "ablation_config.json"
KPI_PATH = BASE / "setup" / "stage2_kpis.json"
FINAL_STATUSES = {"ok", "no_initial_proposal", "app_error"}
VARIANTS = {
    "V_full":       {"use_log_profile": True,  "use_context": True,  "use_kb": True,  "max_iterations": None, "legacy_refinement": False},
    "V_no_log":     {"use_log_profile": False, "use_context": True,  "use_kb": True,  "max_iterations": None, "legacy_refinement": False},
    "V_no_context": {"use_log_profile": True,  "use_context": False, "use_kb": True,  "max_iterations": None, "legacy_refinement": False},
    "V_no_kb":      {"use_log_profile": True,  "use_context": True,  "use_kb": False, "max_iterations": None, "legacy_refinement": False},
    "V_no_refine":  {"use_log_profile": True,  "use_context": True,  "use_kb": True,  "max_iterations": 1,    "legacy_refinement": False},
    "V_old_refine": {"use_log_profile": True,  "use_context": True,  "use_kb": True,  "max_iterations": None, "legacy_refinement": True},
}
MEASURES = ("CTD", "RED", "CWD")
COST_PER_HOUR = 20.0
MAX_EXTRA_HOURS_PER_DAY, MAX_COST_FACTOR, MAX_ADDED_PER_ROLE = 2, 1.35, 1
NIS_MARGIN_PP = 1.0
# Section titles GLASS renders into the Stage-2 prompt / feedback (used only to record what a prompt contains).
LOG_SECTION, KB_SECTION = "Event Log Evidence (filtered)", "Literature-Backed Parameter Recommendations"
FEEDBACK_KB_SECTION, KPI_SECTION = "## Literature-Backed Alternative Interventions", "Verified KPI Targets"
CONSTRAINTS_SECTION = "## Hard Operational Constraints"


class _UploadedLog(io.BytesIO):
    def __init__(self, data: bytes, name: str):
        super().__init__(data)
        self.name = name


def dataset_settings(config: dict, ds: str) -> dict:
    d, s = config["datasets"][ds], config["settings"]
    return {"model": s["model"], "max_iterations": s["max_iterations"], "seeds": s["seeds"], "cases": d["cases"],
            "start": str(pd.Timestamp(d["start"]))}


def switches_of(variant: str, settings: dict) -> dict:
    sw = dict(VARIANTS[variant])
    sw["max_iterations"] = sw["max_iterations"] or settings["max_iterations"]
    return sw


# ------------------------------------------------------------------ inputs ---

def load_inputs(cfg: dict, config: dict, kpis: dict) -> dict:
    ds = config["datasets"][cfg["dataset"]]
    sim_params = json.loads((BASE / ds["baseline_json"]).read_text(encoding="utf-8"))
    for prof in sim_params["resource_profiles"]:
        for res in prof["resource_list"]:
            res["cost_per_hour"] = config["business_context"]["cost_per_hour_all_resources"]
    picked = kpis[cfg["config_id"]]
    names = [k.get("measurable_as") for k in picked["stage1_result"]["kpis"]]
    if [n.lower() for n in names] != [e.lower() for e in cfg["expected_measurable_as"]]:
        raise SystemExit(f"{cfg['config_id']}: KPI objects {names} do not match {cfg['expected_measurable_as']}")
    bc = config["business_context"]
    return {"sim_params": sim_params, "sim_params_text": json.dumps(sim_params, indent=1),
            "bpmn": (BASE / ds["baseline_bpmn"]).read_text(encoding="utf-8"),
            "stage1_result": picked["stage1_result"], "kpi_sources": picked["sources"],
            "chat": [bc["staffing"], bc["budget"], bc["immutable"]], "x_activity": ds["x_activity"]}


def read_test_log(config: dict, ds: str) -> pd.DataFrame:
    df = pd.read_csv(BASE / config["datasets"][ds]["test_log"])
    for c in ("start_time", "end_time"):
        df[c] = pd.to_datetime(df[c], utc=True, format="mixed").dt.tz_localize(None)
    df["resource"] = df["resource"].astype(str)
    return df


# --------------------------------------------------------- measurements ---

def test_log_distances(logs: list[pd.DataFrame], test: pd.DataFrame) -> dict:
    from log_distance_measures.circadian_workforce_distribution import circadian_workforce_distribution_distance
    from log_distance_measures.config import EventLogIDs
    from log_distance_measures.cycle_time_distribution import cycle_time_distribution_distance
    from log_distance_measures.relative_event_distribution import relative_event_distribution_distance
    ids = EventLogIDs(case="case_id", activity="activity", start_time="start_time", end_time="end_time",
                      resource="resource")
    fns = {"CTD": lambda a: cycle_time_distribution_distance(a, ids, test, ids, bin_size=s2.DIST_BIN),
           "RED": lambda a: relative_event_distribution_distance(a, ids, test, ids),
           "CWD": lambda a: circadian_workforce_distribution_distance(a, ids, test, ids)}
    per_seed = {m: [float(f(lg)) for lg in logs] for m, f in fns.items()}
    return {m: {"mean": float(np.mean(v)), "per_seed": v} for m, v in per_seed.items()}


def weekly_hours(items: list[dict]) -> float:
    return sum(s2.weekday_hours(items).values())


def staff_cost_per_week(scn: dict) -> float:
    """GLASS's validation.staff_cost_per_week on the scenario's JSON form, at the business context's fixed rate."""
    tts = {t["id"]: t["timeTableItems"] for t in scn["resourceParameters"]["timeTables"]}
    return sum(COST_PER_HOUR * weekly_hours(tts.get(res.get("calendar") or role["schedule"], []))
               for role in scn["resourceParameters"]["roles"] for res in role["resources"])


def check_validity(scn: dict, base: dict) -> list[str]:
    """Business-context limits on a merged scenario (JSON form; independent of GLASS's own checks)."""
    v = []
    base_roles = {r["id"]: r for r in base["resourceParameters"]["roles"]}
    cal0, cal1 = s2.resource_timetables(base), s2.resource_timetables(scn)
    longest = {d: max(s2.weekday_hours(items)[d] for items in cal0.values()) for d in s2.WEEKDAYS}
    for role in scn["resourceParameters"]["roles"]:
        b = base_roles.get(role["id"])
        n0, n1 = (len(b["resources"]) if b else 0), len(role["resources"])
        if n1 > n0 + MAX_ADDED_PER_ROLE:
            v.append(f"staffing {role['id']}: {n0} -> {n1} (> +{MAX_ADDED_PER_ROLE})")
        if b:
            role_base = {d: max(s2.weekday_hours(cal0[r["id"]])[d] for r in b["resources"]) for d in s2.WEEKDAYS}
        for res in role["resources"]:
            h1 = s2.weekday_hours(cal1[res["id"]])
            h0 = s2.weekday_hours(cal0[res["id"]]) if res["id"] in cal0 else (role_base if b else longest)
            for d in s2.WEEKDAYS:
                if h1[d] > h0[d] + MAX_EXTRA_HOURS_PER_DAY + 1e-9:
                    v.append(f"hours {role['id']}/{res['id']}: {d} {h0[d]:g} h -> {h1[d]:g} h "
                             f"(> +{MAX_EXTRA_HOURS_PER_DAY} h/day)")
    c0, c1 = staff_cost_per_week(base), staff_cost_per_week(scn)
    if c1 > MAX_COST_FACTOR * c0 + 1e-6:
        v.append(f"staff cost per week {c0:.2f} -> {c1:.2f} (+{(c1 / c0 - 1) * 100:.1f} % > +35 %)")
    for role in scn["resourceParameters"]["roles"]:
        if abs(float(role["costHour"]) - COST_PER_HOUR) > 1e-9:
            v.append(f"rate_changed {role['id']}: costHour {role['costHour']:g} (business context: {COST_PER_HOUR:g})")
    acts = {a["name"] for m in scn["models"] for a in m["modelParameter"]["activities"]}
    for m in base["models"]:
        for a in m["modelParameter"]["activities"]:
            if a["name"] not in acts:
                v.append(f"immutable: activity '{a['name']}' missing")
    base_acts = {a["name"] for m in base["models"] for a in m["modelParameter"]["activities"]}
    for name in sorted(acts - base_acts):
        v.append(f"immutable: activity '{name}' added")
    if arrival_changed(scn, base):
        v.append("demand_changed: start-event inter-arrival time or arrival calendar differs from the baseline")
    return v


def arrival_changed(scn: dict, base: dict) -> bool:
    ev = lambda s: [(e.get("interArrivalTime"), e.get("arrivalCalendar")) for m in s["models"]
                    for e in m["modelParameter"].get("events", [])]
    return ev(scn) != ev(base)


def x_aliases(base: dict, x: str) -> set[str]:
    """X's name and id, the roles that perform X in the baseline, and those roles' resources (lower case)."""
    al = set()
    for m in base["models"]:
        for a in m["modelParameter"]["activities"]:
            if a["name"].lower() == x.lower():
                al |= {a["name"].lower(), a["id"].lower()} | {r.lower() for r in a["resources"]}
    roles = {r["id"].lower(): r for r in base["resourceParameters"]["roles"]}
    for rid in list(al):
        if rid in roles:
            al |= {res["id"].lower() for res in roles[rid]["resources"]}
    return al


def change_profile(mods: list[dict], aliases: set[str]) -> dict:
    on_x = [m for m in mods if str(m.get("target_element", "")).lower() in aliases]
    return {"n_modifications": len(mods), "per_parameter_type": dict(Counter(m.get("parameter_type") for m in mods)),
            "n_targeting_x": len(on_x), "share_targeting_x": round(len(on_x) / len(mods), 4) if mods else None}


def dhr_nis(kpis: list[dict]) -> tuple[float | None, float | None]:
    """Thesis definitions (evaluation/run_stage2_ablation.py _compute_dhr_mki)."""
    used = [k for k in kpis if k.get("glass_improved") is not None and k.get("pct_change") is not None]
    if not used:
        return None, None
    hits = [1.0 if k["glass_improved"] else 0.0 for k in used]
    nis = [abs(k["pct_change"]) * (1.0 if k["glass_improved"] else -1.0) for k in used]
    return sum(hits) / len(hits), sum(nis) / len(nis)


def discarded(ev: dict, kept_baseline: bool = False) -> tuple[bool, str]:
    ini, fin = ev.get("initial") or {}, ev.get("final") or {}
    if not ini or not ini.get("admissible"):
        return False, ""
    if kept_baseline:       # counted only when the initial was better than the baseline on the 10 seeds
        better = (ini.get("DHR") or 0) > 0 or (ini.get("NIS") is not None and ini["NIS"] > NIS_MARGIN_PP)
        return (True, "a: final not admissible (baseline kept)") if better else (False, "")
    if fin.get("same_as_initial") or fin.get("iteration") in (None, 1):
        return False, ""
    if not fin.get("admissible"):
        return True, "a: final not admissible"
    di, df_ = ini.get("DHR"), fin.get("DHR")
    if di is not None and df_ is not None and di > df_:
        return True, f"b: DHR {di:g} > {df_:g}"
    ni, nf = ini.get("NIS"), fin.get("NIS")
    if di == df_ and ni is not None and nf is not None and ni - nf > NIS_MARGIN_PP:
        return True, f"c: equal DHR, NIS {ni:.2f} - {nf:.2f} > {NIS_MARGIN_PP:g} pp"
    return False, ""


# ------------------------------------------------------------- switches ---

def disable_kb(patches, counter: dict) -> None:
    """V_no_kb: no knowledge-base retrieval and no knowledge-base material in generation or feedback."""
    import knowledge.retrieval as kr
    import second_llm.iterative_evaluator as ie
    import second_llm.payload_builder as pb
    import second_llm.scenario_generator as sg
    from knowledge.retrieval_result import RetrievalResult

    def no_retrieval(*args, **kwargs):
        counter["kb_stub_calls"] += 1
        return RetrievalResult()
    patches.set(kr, "retrieve_for_second_llm", no_retrieval)
    for module in (sg, pb):
        def without_kb(*args, _original=module.build_second_llm_evidence, **kwargs):
            evidence = _original(*args, **kwargs)
            evidence.kb_json = ""
            return evidence
        patches.set(module, "build_second_llm_evidence", without_kb)

    def no_alternatives(*args, **kwargs):
        counter["feedback_kb_suppressed"] += 1
        return ""
    patches.set(ie, "_retrieve_alternative_interventions", no_alternatives)


def merge_counts(notes: list[str], calls: list[dict], iteration: int) -> dict | None:
    """Merge stability of an iteration's accepted patch as applied / proposed by the LLM.

    GLASS keeps the last attempt that reached the merge (the "[merge stability] a/b" note of that attempt's
    block). proposed = modifications in the LLM's answer of that attempt (the iteration's k-th scenario-generation
    call), before GLASS's patch-reject and constraint steps; applied = a. GLASS's own value is a / b
    (b = modifications that reached the merger; 0/0 = 1.0). proposed = 0 gives stability None (counted as not > 0).
    """
    from second_llm.scenario_generator import _extract_and_parse_json
    blocks, current = [], None
    for n in notes or []:
        if n.startswith("Patch attempt "):
            current = {"attempt": int(n.split(" ")[2].split("/")[0]), "notes": []}
            blocks.append(current)
        elif current is not None:
            current["notes"].append(n)
    merged = [b for b in blocks if any(n.startswith("[merge stability]") for n in b["notes"])]
    if not merged:
        return None
    block = merged[-1]
    note = [n for n in block["notes"] if n.startswith("[merge stability]")][-1]
    applied, reaching = (int(x) for x in note.split("] ", 1)[1].split(" ")[0].split("/"))
    generation_calls = [c for c in calls if c.get("iteration") == iteration and KPI_SECTION in (c.get("user_prompt") or "")]
    proposed = None
    if block["attempt"] <= len(generation_calls):
        try:
            proposed = len(_extract_and_parse_json(generation_calls[block["attempt"] - 1]["raw"]).get("modifications") or [])
        except Exception:
            proposed = None
    if proposed is None:                                  # answer not readable: GLASS's count plus its rejections
        proposed = reaching + sum(n.startswith("[patch-reject]") for n in block["notes"])
    return {"accepted_attempt": block["attempt"], "proposed": proposed, "reaching_merger": reaching,
            "applied": applied, "rejected_before_merge": sum(n.startswith("[patch-reject]") for n in block["notes"]),
            "merge_stability": round(applied / proposed, 4) if proposed else None,
            "glass_merge_stability": round(applied / reaching, 4) if reaching else 1.0}


def prompt_sections(calls: list[dict]) -> list[dict]:
    """What each scenario-generation prompt contains (by GLASS's section titles)."""
    out = []
    for c in calls:
        up = c.get("user_prompt") or ""
        if KPI_SECTION not in up:
            continue
        out.append({"iteration": c.get("iteration"), "log_evidence": LOG_SECTION in up, "kb_section": KB_SECTION in up,
                    "operational_constraints": CONSTRAINTS_SECTION in up,
                    "feedback_kb_section": FEEDBACK_KB_SECTION in up, "user_prompt_chars": len(up)})
    return out


# --------------------------------------------------------------- one run ---

def run_rep(job: dict) -> dict:
    s2.quiet()
    config, cfg, variant, rep, out = job["config"], job["cfg"], job["variant"], job["rep"], Path(job["out"])
    ds = cfg["dataset"]
    settings = dataset_settings(config, ds)
    sw = switches_of(variant, settings)
    cell = f"{cfg['config_id']}__{variant}"
    path = out / "raw" / cell / f"rep_{rep}.json"
    t_start = datetime.now(timezone.utc).isoformat(timespec="seconds")
    t0 = time.time()

    import second_llm.iterative_evaluator as ie
    import second_llm.multi_seed_evaluation as mse
    from prompts.scenario_patch_prompt import _build_constraints_block
    from second_llm.models import ChatRole
    from second_llm.orchestrator import SecondLLMWorkspaceOrchestrator
    from second_llm.output_schema import SimuBridgeScenario
    from second_llm.prosimos_runner import scenario_to_prosimos_json
    from second_llm.state import reset_workspace
    from ui.evaluation_panel import _build_cost_map, _get_kpi_targets_from_workspace
    from ui.second_llm_panel import _get_evaluation_targets

    inputs = load_inputs(cfg, config, job["kpis"])
    shared = job["shared"][ds]
    log_profile = shared["log_profile"] if sw["use_log_profile"] else None
    tracker, events, patches = {"iteration": 0}, [], s2.Patches()
    counter = {"kb_stub_calls": 0, "feedback_kb_suppressed": 0}
    provider = s2.Stage2RecordingProvider(s1.make_provider(settings["model"]), tracker)
    record = {"config_id": cfg["config_id"], "dataset": ds, "goal": cfg["goal"], "variant": variant, "switches": sw,
              "rep": rep, "status": None, "error": None, "timestamp": t_start,
              "provider": type(provider.inner).__name__, "model": provider.get_model_name(),
              "glass_commit": job["glass_commit"], "config_sha256": job["config_sha"], "kpis_sha256": job["kpis_sha"],
              "script_sha256": job["script_sha"], "baseline_files_sha256": shared["baseline_files_sha256"],
              "test_log_sha256": shared["test_log_sha256"], "settings": settings, "seeds": s2.seeds_of(settings),
              "temperature_schedule": "GLASS run_iterative_evaluation: min(0.3 + 0.1 * (iteration - 1), 0.7)",
              "simulation_goal": cfg["simulation_goal"], "kpi_sources": inputs["kpi_sources"],
              "stage1_result_passed": inputs["stage1_result"], "x_activity": inputs["x_activity"],
              "chat_user_turns": inputs["chat"] if sw["use_context"] else [],
              "log_profile_passed": log_profile is not None,
              "baseline_logs_sha256": shared["baseline_logs_sha256"]}
    try:
        reset_workspace()
        orch = SecondLLMWorkspaceOrchestrator(provider=provider)
        first = orch.parse_first_llm_json(json.dumps(inputs["stage1_result"], ensure_ascii=False))
        if not first.is_valid:
            raise RuntimeError(f"Stage-1 result rejected by GLASS: {first.validation_error or first.parse_error}")
        orch.accept_simod_raw_input(inputs["sim_params_text"], bpmn_xml=inputs["bpmn"])
        if sw["use_context"]:
            for turn in inputs["chat"]:
                orch.chat_session.append(ChatRole.USER, turn)
        if not sw["use_kb"]:
            disable_kb(patches, counter)
        record["draft_payload"] = s2.dump(orch.build_second_llm_request_draft(log_profile=log_profile))

        baseline = s2.load_scenario(inputs["sim_params_text"], inputs["bpmn"])
        base_json = baseline.model_dump(mode="json")
        loop_targets = _get_evaluation_targets()
        eval_targets = _get_kpi_targets_from_workspace()
        cost_map = _build_cost_map(baseline)
        record.update({"loop_targets": s2.dump(loop_targets), "eval_targets": s2.dump(eval_targets),
                       "cost_map_roles": len(cost_map or {})})

        s2.capture_generation(patches, tracker, events)
        s2.seed_shim(patches, ie)

        def on_iteration(i, msg):
            tracker["iteration"] = i

        loop = ie.run_iterative_evaluation(orch, baseline, inputs["bpmn"], loop_targets,
                                           max_iterations=sw["max_iterations"], total_cases=settings["cases"],
                                           start_time=settings["start"], seed=s2.BASE_SEED, cost_per_hour=cost_map,
                                           on_iteration=on_iteration, log_profile=log_profile,
                                           legacy_refinement=sw["legacy_refinement"])
        patches.restore()
        record["kb_switch"] = dict(counter)
        if provider.fatal:
            return {"fatal": provider.fatal}

        iterations = []
        for it in loop.iterations:
            g = it.gen_result
            evs = [e for e in events if e["iteration"] == it.iteration]
            legacy = [e for e in evs if e["fn"] == "build_legacy_proposal"]
            accepted = next((k for k, e in enumerate(legacy) if g and g.proposal is not None
                             and e["proposal_id"] == id(g.proposal)), None)
            merges = [e for e in evs if e["fn"] == "apply_patch"]
            merge = merges[accepted] if accepted is not None else None
            validation = next((v for v in reversed([e for e in evs if e["fn"] == "validate_patch"])
                               if merge and v["patch"] == merge["patch"]), None)
            scenario = g.proposal.scenario if g and g.proposal else None
            iterations.append({
                "iteration": it.iteration, "error": it.error, "status": s2.dump(it.status), "score": it.score,
                "temperature": round(min(0.3 + 0.1 * (it.iteration - 1), 0.7), 2),
                "generation": None if g is None else {
                    "success": g.success, "error": g.error, "attempts": g.attempts, "decoding_mode": g.decoding_mode,
                    "raw_llm_output": g.raw_llm_output, "generation_notes": g.generation_notes,
                    "context_summary": s2.dump(g.context_summary._data) if g.context_summary else None,
                    "context_block_in_prompt": _build_constraints_block(g.context_summary),
                    "post_merge_validation": s2.dump(g.validation), "merge_stability": g.merge_stability,
                    "cost_report": s2.dump(g.cost_report)},
                "patch": merge["patch"] if merge else None,
                "patch_validator": validation["result"] if validation else None,
                "patch_validator_passed": (not validation["has_errors"]) if validation else None,
                "merged_scenario": merge["merged_scenario"] if merge else None,
                "merge_diagnostics": merge["diagnostics"] if merge else None,
                "simulated_scenario": scenario.model_dump(mode="json") if scenario else None,
                "simulated_scenario_prosimos": scenario_to_prosimos_json(scenario, inputs["bpmn"]) if scenario else None,
                "all_patch_attempts": [{k: e[k] for k in ("patch", "has_errors", "result")}
                                       for e in evs if e["fn"] == "validate_patch"],
                "single_run_evaluation": s2.dump(it.eval_result), "feedback_text": it.feedback_message,
            })
        record["loop"] = {"iterations": iterations, "iterations_used": len(iterations),
                          "best_iteration": (loop.best_iteration_idx + 1) if loop.best_iteration_idx is not None else None,
                          "outcome": loop.outcome, "no_improving_scenario": loop.no_improving_scenario,
                          "best_candidate_iteration": (loop.best_candidate_idx + 1)
                          if loop.best_candidate_idx is not None else None,
                          "final_status": s2.dump(loop.final_status), "error": loop.error,
                          "baseline_kpis_single_run": s2.dump(loop.baseline_kpis),
                          "total_time_seconds": round(loop.total_time_seconds, 1)}
        record["llm_calls"] = provider.calls
        record["prompt_sections"] = prompt_sections(provider.calls)
        for it_rec in iterations:
            it_rec["merge_counts"] = merge_counts((it_rec["generation"] or {}).get("generation_notes"),
                                                  provider.calls, it_rec["iteration"])
        # GLASS v3.2 rejects a modification whose stated direction contradicts its values: count the rejections
        # (validator diagnostics over all attempts of all iterations; the last attempt's re-validation after
        # dropping them is not counted twice because it no longer contains them)
        record["direction_inconsistent_rejections"] = sum(
            d.get("category") == "direction_inconsistent"
            for it_rec in iterations for a in it_rec["all_patch_attempts"]
            for d in ((a.get("result") or {}).get("diagnostics") or []))
        if provider.failed:
            record.update(status="llm_error", error=" | ".join(provider.failed))
            return finish(record, path, t0)
        if not iterations or iterations[0]["simulated_scenario"] is None:
            record.update(status="no_initial_proposal", error=loop.error or (iterations[0]["error"] if iterations else None))
            record["evaluation"] = {"initial": {"iteration": 1, "scenario_produced": False}}
            return finish(record, path, t0)

        cached = {s: pd.read_csv(out / "baseline_logs" / ds / f"seed_{s}.csv.gz") for s in record["seeds"]}
        test = read_test_log(config, ds)
        aliases = x_aliases(base_json, inputs["x_activity"])
        logs_dir = out / "logs" / cell / f"rep_{rep}"
        evaluation, log_sha = {}, {}
        for phase, it_no in (("initial", 1), ("final", record["loop"]["best_iteration"])):
            it = iterations[it_no - 1] if it_no else None
            if phase == "final" and record["loop"]["no_improving_scenario"]:
                evaluation["final"] = {"iteration": None, "scenario_produced": False, "reason": "no_improving_scenario",
                                       "candidate_iteration": record["loop"]["best_candidate_iteration"],
                                       "valid": False, "executable": False, "admissible": False}
                continue
            if it is None or it["simulated_scenario"] is None:
                evaluation[phase] = {"iteration": it_no, "scenario_produced": False, "valid": False,
                                     "executable": False, "admissible": False, "error": "no scenario"}
                continue
            if phase == "final" and it_no == 1:
                evaluation["final"] = {**evaluation["initial"], "same_as_initial": True}
                continue
            proposed = SimuBridgeScenario.model_validate(it["simulated_scenario"])
            logs: dict[int, pd.DataFrame] = {}
            s2.seed_shim(patches, mse, baseline=baseline, cached=cached, logs=logs)
            try:
                res = mse.evaluate_multi_seed(baseline, proposed, inputs["bpmn"], eval_targets,
                                              num_seeds=settings["seeds"], base_seed=s2.BASE_SEED,
                                              total_cases=settings["cases"], start_time=settings["start"],
                                              cost_per_hour=cost_map)
            finally:
                patches.restore()
            for s in record["seeds"]:
                if s in logs:
                    p = logs_dir / f"{phase}_seed_{s}.csv.gz"
                    p.parent.mkdir(parents=True, exist_ok=True)
                    logs[s].to_csv(p, index=False, compression="gzip")
                    log_sha[f"{phase}_seed_{s}.csv.gz"] = s1.sha256(p.read_bytes())
            executable = bool(res.ok) and all(s in logs for s in record["seeds"])
            dist = mapped = None
            if executable:
                raw_logs = [s2.prep_log(logs[s]) for s in record["seeds"]]
                for lg in raw_logs:
                    lg["resource"] = lg["resource"].astype(str)
                corr_logs, mapped = s2.corrected(raw_logs, it["simulated_scenario"])
                dist = test_log_distances(corr_logs, test)
            mods = (it["patch"] or {}).get("modifications", [])
            violations = check_validity(it["simulated_scenario"], base_json)
            kpis = s2.kpi_table(res, None, []) if res.ok else []
            dhr, nis = dhr_nis(kpis)
            counts = it.get("merge_counts") or {}
            stability = counts.get("merge_stability")
            valid = bool(it["patch_validator_passed"]) and not violations
            evaluation[phase] = {
                "iteration": it_no, "same_as_initial": False, "scenario_produced": True,
                "multi_seed_ok": res.ok, "multi_seed_error": res.error, "n_logs": len(logs),
                "patch_validator_passed": it["patch_validator_passed"],
                "post_merge_validation_errors": [i["message"] for i in
                                                 ((it["generation"] or {}).get("post_merge_validation") or {}).get("issues", [])
                                                 if i.get("severity") == "error"],
                "violations": violations, "demand_changed": any(v.startswith("demand_changed") for v in violations),
                "demand_proposed": any(m.get("parameter_type") == "inter_arrival_time" for m in mods),
                "rate_changed": any(v.startswith("rate_changed") for v in violations),
                "staff_cost_per_week": {"baseline": staff_cost_per_week(base_json),
                                        "proposed": staff_cost_per_week(it["simulated_scenario"])},
                "valid": valid, "executable": executable, "merge_stability": stability,
                "glass_merge_stability": counts.get("glass_merge_stability"), "merge_counts": counts or None,
                "admissible": valid and executable and (stability or 0) > 0,
                "kpis": kpis, "all_kpis_improved": bool(kpis) and all(k["glass_improved"] is True for k in kpis),
                "DHR": dhr, "NIS": nis, "summary": s2.dump(res.summary),
                "simulation_settings": res.simulation_settings,
                "change_profile": change_profile(mods, aliases),
                "distance_to_test_log": {k: v["mean"] for k, v in dist.items()} if dist else None,
                "distance_to_test_log_per_seed": {k: v["per_seed"] for k, v in dist.items()} if dist else None,
                "midnight_ends_mapped_per_seed": mapped}
        flag, reason = discarded(evaluation, kept_baseline=record["loop"]["no_improving_scenario"])
        record.update(evaluation=evaluation, loop_discarded_better_initial=flag, loop_discarded_condition=reason,
                      baseline_distance_to_test_log=shared["baseline_distance"],
                      x_aliases=sorted(aliases), logs_dir=logs_dir.relative_to(out).as_posix(),
                      logs_sha256=log_sha, status="ok")
    except Exception:
        patches.restore()
        if provider.fatal:
            return {"fatal": provider.fatal}
        record.update(status="app_error", error=traceback.format_exc(), llm_calls=provider.calls,
                      kb_switch=dict(counter))
    return finish(record, path, t0)


def finish(record: dict, path: Path, t0: float) -> dict:
    record["elapsed_s"] = round(time.time() - t0, 1)
    s1.write_json(path, record)
    return {"cell": f"{record['config_id']}__{record['variant']}", "rep": record["rep"], "status": record["status"],
            "elapsed_s": record["elapsed_s"], "error": (record.get("error") or "").splitlines()[-1:]}


# --------------------------------------------------------- per-dataset cache ---

def prepare_dataset(ds: str, config: dict, kpis: dict, out: Path, app) -> dict:
    """Cached per dataset: the 10-seed baseline runs and their distance to the test log; the log profile."""
    settings = dataset_settings(config, ds)
    cfg = next(c for c in config["configurations"] if c["dataset"] == ds)
    inputs = load_inputs(cfg, config, kpis)
    baseline = s2.load_scenario(inputs["sim_params_text"], inputs["bpmn"])
    paths = [out / "baseline_logs" / ds / f"seed_{s}.csv.gz" for s in s2.seeds_of(settings)]
    meta_path = out / "baseline_logs" / ds / "baseline.json"
    if meta_path.exists() and all(p.exists() for p in paths):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    else:
        shas = s2.simulate_seeds(baseline, inputs["bpmn"], settings, paths)
        check = out / "baseline_logs" / ds / "check.csv.gz"
        s2.simulate_seeds(baseline, inputs["bpmn"], {**settings, "seeds": 1}, [check])
        same = pd.read_csv(check).equals(pd.read_csv(paths[0]))
        check.unlink()
        if not same:
            raise SystemExit(f"{ds}: seeded baseline run is not reproducible")
        logs = [s2.prep_log(pd.read_csv(p)) for p in paths]
        for lg in logs:
            lg["resource"] = lg["resource"].astype(str)
        corr, mapped = s2.corrected(logs, baseline.model_dump(mode="json"))
        dist = test_log_distances(corr, read_test_log(config, ds))
        meta = {"baseline_logs_sha256": shas, "baseline_distance": {k: v["mean"] for k, v in dist.items()},
                "baseline_distance_per_seed": {k: v["per_seed"] for k, v in dist.items()},
                "baseline_midnight_ends_mapped_per_seed": mapped,
                "staff_cost_per_week": staff_cost_per_week(baseline.model_dump(mode="json"))}
        meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    test_path = BASE / config["datasets"][ds]["test_log"]
    log_profile = app["extract_log_artifacts"](_UploadedLog(gzip.open(test_path).read(),
                                                            test_path.name.replace(".gz", "")))[0]
    d = config["datasets"][ds]
    return {**meta, "log_profile": log_profile, "test_log_sha256": s1.sha256(test_path.read_bytes()),
            "baseline_files_sha256": {Path(d[k]).name: s1.sha256((BASE / d[k]).read_bytes())
                                      for k in ("baseline_bpmn", "baseline_json")}}


# -------------------------------------------------------------- smoke ---

def smoke_report(rec: dict) -> str:
    L = [f"== {rec['config_id']} {rec['variant']} rep {rec['rep']}: status {rec['status']} ({rec.get('elapsed_s')} s)",
         f"switches: {rec['switches']}", f"log profile passed: {rec['log_profile_passed']}; "
         f"KB switch counters: {rec.get('kb_switch')}"]
    if rec.get("error"):
        L.append(rec["error"][-2000:])
    L.append("-- KPI objects passed")
    for k, src in zip(rec["stage1_result_passed"]["kpis"], rec["kpi_sources"]):
        L.append(f"   {k['name']} | measurable_as {k['measurable_as']} | {k['category']} | {k['target_direction']} "
                 f"(from {src['source_cell']} rep {src['rep']}, {src['match']})")
    L.append(f"-- Clarification turns sent: {len(rec['chat_user_turns'])}")
    for t in rec["chat_user_turns"]:
        L.append(f"   user: {t}")
    its = (rec.get("loop") or {}).get("iterations", [])
    if its:
        L.append("-- Context block as rendered into the prompt (iteration 1)\n"
                 + ((its[0]["generation"] or {}).get("context_block_in_prompt") or "   (none)"))
    L.append("-- Prompt contents per generation call")
    for p in rec.get("prompt_sections") or []:
        L.append(f"   it {p['iteration']}: log evidence {p['log_evidence']} | KB section {p['kb_section']} | "
                 f"operational constraints {p['operational_constraints']} | feedback KB section "
                 f"{p['feedback_kb_section']} | {p['user_prompt_chars']} chars")
    L.append(f"-- Iterations used {len(its)}; final = iteration {(rec.get('loop') or {}).get('best_iteration')}")
    for it in its:
        g = it["generation"] or {}
        L.append(f"   it {it['iteration']} | T {it['temperature']} | status {it['status']} | score {it['score']:.1f} | "
                 f"validator {it['patch_validator_passed']} | merge {it.get('merge_counts')}")
        for m in (it["patch"] or {}).get("modifications", []):
            L.append(f"       {m['parameter_type']:28s} {m['direction']:10s} {m['target_element']}: "
                     f"{m['baseline_value']} -> {m['proposed_value']}")
        if it.get("feedback_text"):
            fb = it["feedback_text"]
            L.append(f"       feedback: {len(fb)} chars; literature section {FEEDBACK_KB_SECTION in fb}")
    for phase, ev in (rec.get("evaluation") or {}).items():
        L.append(f"-- {phase} (iteration {ev.get('iteration')}): produced {ev.get('scenario_produced')} | valid "
                 f"{ev.get('valid')} | executable {ev.get('executable')} | merge stability {ev.get('merge_stability')} | "
                 f"admissible {ev.get('admissible')} | DHR {ev.get('DHR')} | NIS {ev.get('NIS')}")
        L.append(f"   violations: {ev.get('violations')}")
        L.append(f"   staff cost per week: {ev.get('staff_cost_per_week')}")
        for k in ev.get("kpis", []):
            L.append(f"   {k['kpi']}: baseline {k['baseline_mean']:.2f} {k['baseline_ci']} | proposed "
                     f"{k['proposed_mean']:.2f} {k['proposed_ci']} | {k['pct_change']:+.2f} % p={k['p_value']} | "
                     f"significant {k['significant']} | improved {k['glass_improved']}")
        L.append(f"   change profile: {ev.get('change_profile')}")
        L.append(f"   distance to test log: {ev.get('distance_to_test_log')}")
    L.append(f"-- loop discarded better initial: {rec.get('loop_discarded_better_initial')} "
             f"{rec.get('loop_discarded_condition')}")
    L.append(f"-- baseline distance to test log: {rec.get('baseline_distance_to_test_log')}")
    return "\n".join(L)


# -------------------------------------------------------------- main ---

def main() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    kpis = json.loads(KPI_PATH.read_text(encoding="utf-8"))
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--variant", help="variant(s), comma-separated (default: all six)")
    ap.add_argument("--only", help="configuration id(s), comma-separated")
    ap.add_argument("--reps", default=",".join(map(str, config["settings"]["repetitions_first_pass"])),
                    help="repetition numbers, comma-separated (default: the first pass)")
    ap.add_argument("--out", type=Path, default=BASE / "results" / "stage2")
    ap.add_argument("--workers", type=int, default=1, help="parallel worker processes")
    ap.add_argument("--smoke", action="store_true", help="= --only BPIC_2012_W_G1G2 --reps 1, all variants")
    args = ap.parse_args()
    if args.smoke:
        args.only, args.reps = "BPIC_2012_W_G1G2", "1"
    variants = args.variant.split(",") if args.variant else list(VARIANTS)
    if unknown := [v for v in variants if v not in VARIANTS]:
        sys.exit(f"Unknown variant(s): {unknown}")
    reps = [int(r) for r in args.reps.split(",")]
    only = args.only.split(",") if args.only else None
    selected = [c for c in config["configurations"] if only is None or c["config_id"] in only]
    if not selected or (only and len(selected) != len(only)):
        sys.exit(f"Unknown configuration id in: {args.only}")
    if s2.git("status", "--porcelain", "--", "goal_to_parameters"):
        sys.exit("goal_to_parameters/ has local changes; the ablation must run on committed GLASS code")
    s2.quiet()
    commit = s2.git("rev-parse", "HEAD")
    for cfg in selected:
        load_inputs(cfg, config, kpis)                     # assert the KPI objects before any LLM call
    app, _ = s1.load_app_stage1()
    shared = {}
    for ds in sorted({c["dataset"] for c in selected}):
        s1.log(f"baseline runs and log profile for {ds} ...")
        shared[ds] = prepare_dataset(ds, config, kpis, args.out, app)
    common = {"config": config, "kpis": kpis, "out": str(args.out), "glass_commit": commit, "shared": shared,
              "config_sha": s1.sha256(CONFIG_PATH.read_bytes()), "kpis_sha": s1.sha256(KPI_PATH.read_bytes()),
              "script_sha": s1.sha256(Path(__file__).read_bytes())}
    jobs = []
    for cfg in selected:
        for variant in variants:
            for rep in reps:
                path = args.out / "raw" / f"{cfg['config_id']}__{variant}" / f"rep_{rep}.json"
                if (existing := s1.load_json(path)) and existing.get("status") in FINAL_STATUSES:
                    continue
                jobs.append({**common, "cfg": cfg, "variant": variant, "rep": rep})
    s1.log(f"{len(jobs)} run(s) to do; workers {args.workers}; GLASS {commit[:7]}")
    if sys.platform == "win32":   # no idle sleep while the batch runs (ES_CONTINUOUS | ES_SYSTEM_REQUIRED; ends with the process)
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    status = Counter()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_rep, job) for job in jobs]
        for done, fut in enumerate(futures, 1):
            res = fut.result()
            if "fatal" in res:
                pool.shutdown(wait=True, cancel_futures=True)
                sys.exit(f"Fatal API error: {res['fatal']}\nAborted; runs written so far are kept, rerun to resume.")
            status[res["status"]] += 1
            s1.log(f"[{done}/{len(jobs)}] {res['cell']} rep {res['rep']}: {res['status']} ({res['elapsed_s']} s) "
                   f"{' '.join(res['error'])}")
            if done % 25 == 0:
                s1.log(f"STATUS after {done} runs: {dict(status)}")
    s1.log(f"done: {dict(status)}")
    if args.smoke:
        text = "\n\n".join(smoke_report(s1.load_json(args.out / "raw" / f"{c['config_id']}__{v}" / "rep_1.json"))
                           for c in selected for v in variants)
        (args.out / "smoke_report.txt").write_text(text, encoding="utf-8")
        s1.log(f"smoke report: {args.out / 'smoke_report.txt'}")


if __name__ == "__main__":
    main()
