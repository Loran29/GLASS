"""
Refinement pilot: one round of GLASS's KPI refinement on stored Stage-1 first proposals.

For every case in setup/pilot_config.json the stored first proposal is refined once through the
app's own refinement path (goal_to_parameters/app.py, handle_refinement): build_refinement_prompt(
sanitize_user_input(...), previous_kpis_json = current_result.model_dump_json(indent=2), accepted /
rejected names in KPI order, total_kpis = len(kpis), log / context evidence) -> parse_with_retries(
temperature and json_mode as handle_refinement passes them, no few-shot messages) -> the same
_finalize_generated_result post-processing as Stage 1. The app.py functions are compiled from app.py's
own source (run_stage1_batch.load_app_stage1); nothing under goal_to_parameters/ is modified, no stored
answer is altered and no new first proposal is generated.

First proposal: results/stage1_results/raw/<id>/rep_<k>.json is the source file (its SHA-256 is
recorded); its result as post-processed by today's GLASS is taken from the matching
results/stage1_results_normalised/ record, which must cite that SHA-256. That is the KPI set today's
app would show the user and send back as previous_kpis_json.

Decisions: KPIs named in `accept` are accepted, those in `reject` rejected. The UI only enables
"Refine KPIs" once no KPI is pending, so every other (undecided) KPI is treated as accepted.

Usage (repo root):  python evaluation/loanapp_p2p/scripts/run_refinement_pilot.py
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import logging
import platform
import sys
import time
import traceback
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_stage1_batch as batch  # noqa: E402  (also puts goal_to_parameters/ on sys.path)

from models import KPIGenerationResult  # noqa: E402

PILOT_CONFIG = batch.SETUP_DIR / "pilot_config.json"
SOURCE = batch.RESULTS_DIR / "stage1_results"
NORMALISED = batch.RESULTS_DIR / "stage1_results_normalised"
OUT = batch.RESULTS_DIR / "refinement_pilot"
ROUND = 1


def expected_key(expected_name: str, cfg: dict) -> str:
    """Pilot expected KPI name -> the measurable_as string the batch scores it by."""
    key = "Resource Utilization" if expected_name.endswith(" Utilization") else expected_name
    if key not in cfg["expected_measurable_as"]:
        sys.exit(f"{cfg['id']}: expected KPI {expected_name!r} maps to {key!r}, not in stage1_config.json")
    return key


def app_refinement_call() -> dict:
    """Keyword constants handle_refinement passes to parse_with_retries (temperature, json_mode)."""
    tree = ast.parse(batch.APP_PATH.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "handle_refinement")
    call = next(n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "parse_with_retries")
    kw = {k.arg: k.value for k in call.keywords}
    if "few_shot_messages" in kw:
        sys.exit("handle_refinement now passes few_shot_messages; update the pilot")
    return {"temperature": kw["temperature"].value, "json_mode": kw["json_mode"].value}


def present(expected: list[str], keys: dict[str, str], kpis: list[dict]) -> dict[str, list[str]]:
    """Expected KPI names found by the batch's strict and lenient rule sets."""
    got = {k["measurable_as"].lower() for k in kpis if k.get("measurable_as")}
    return {"strict": [e for e in expected if keys[e].lower() in got],
            "lenient": [e for e in expected if any(batch.lenient_match(keys[e], k) for k in kpis)]}


def extras(keys: dict[str, str], kpis: list[dict]) -> dict[str, list[str]]:
    wanted = {v.lower() for v in keys.values()}
    return {"strict": [k["name"] for k in kpis if k.get("measurable_as") and k["measurable_as"].lower() not in wanted],
            "lenient": [k["name"] for k in kpis if not any(batch.lenient_match(v, k) for v in keys.values())]}


def summary_row(rec: dict) -> dict:
    case, keys = rec["case"], rec["expected_keys"]
    before = rec["first_proposal"]["kpis"]
    after = (rec.get("result") or {}).get("kpis") or []
    by_name = {k["name"]: k for k in after}
    changed = [n for n in rec["decisions"]["accepted"]
               if n not in by_name or by_name[n].get("suggested_formula") != next(
                   k for k in before if k["name"] == n).get("suggested_formula")]
    pb, pa, ex = present(case["expected"], keys, before), present(case["expected"], keys, after), extras(keys, after)
    row = {"case": f"{case['config_id']}_rep{case['rep']}", "config_id": case["config_id"], "rep": case["rep"],
           "status": rec["status"], "attempts": len(rec["attempts"]),
           "accepted": "|".join(rec["decisions"]["accepted"]),
           "accepted_from_undecided": "|".join(rec["decisions"]["undecided_treated_as_accepted"]),
           "rejected": "|".join(rec["decisions"]["rejected"]), "expected": "|".join(case["expected"])}
    for mode in ("strict", "lenient"):
        row[f"{mode}_present_before"] = "|".join(pb[mode])
        row[f"{mode}_present_after"] = "|".join(pa[mode])
        row[f"{mode}_still_missing"] = "|".join(e for e in case["expected"] if e not in pa[mode])
        row[f"{mode}_n_extras_after"] = len(ex[mode])
        row[f"{mode}_extras_after"] = "|".join(ex[mode])
    all_found = lambda p: len(p["lenient"]) == len(case["expected"])   # outcome uses lenient scoring
    row["outcome"] = ("reached_without_refinement" if all_found(pb) else
                      "reached_after_1_round" if after and all_found(pa) else "not_reached_after_1_round")
    row.update({
        "accepted_unchanged": ("yes" if not changed else "no") if after else "",
        "accepted_changed": "|".join(changed) if after else "",
        "rejected_names_still_present": "|".join(n for n in rec["decisions"]["rejected"] if n in by_name),
        "n_kpis_before": len(before), "n_kpis_returned": len(after),
        "returned_kpis": ";".join(f"{k['name']}|{k.get('measurable_as') or ''}|{k['target_direction']}|"
                                  f"{k['process_scope']}" for k in after),
    })
    return row


def run_case(case: dict, ctx: dict) -> dict:
    app, cfg = ctx["app"], ctx["configs"][case["config_id"]]
    src_path = SOURCE / "raw" / cfg["id"] / f"rep_{case['rep']}.json"
    norm_path = NORMALISED / "raw" / cfg["id"] / f"rep_{case['rep']}.json"
    src_bytes = src_path.read_bytes()
    src, norm = json.loads(src_bytes), json.loads(norm_path.read_bytes())
    if norm["normalisation"]["source_file_sha256"] != batch.sha256(src_bytes):
        sys.exit(f"{norm_path} does not cite the SHA-256 of {src_path}")
    if src["status"] != "ok" or src["simulation_goal"] != cfg["simulation_goal"] or src["event_log"] != cfg["event_log"]:
        sys.exit(f"{src_path}: status / goal / event log do not match stage1_config.json")
    first = KPIGenerationResult.model_validate(norm["result"])
    names = [k.name for k in first.kpis]
    unknown = [n for n in case["accept"] + case["reject"] if n not in names]
    if unknown or set(case["accept"]) & set(case["reject"]) or len(set(names)) != len(names):
        sys.exit(f"{cfg['id']} rep {case['rep']}: names not in the first proposal (exact match): {unknown} "
                 f"or overlapping accept/reject or duplicate KPI names; KPIs are {names}")
    decision = {n: "rejected" if n in case["reject"] else "accepted" for n in names}
    accepted = [n for n in names if decision[n] == "accepted"]   # KPI order, as handle_refinement builds them
    rejected = [n for n in names if decision[n] == "rejected"]
    undecided = [n for n in names if n not in case["accept"] + case["reject"]]

    log_path = batch.LOGS_DIR / cfg["event_log"]
    log_profile, log_evidence, context_evidence = ctx["artifacts"][cfg["event_log"]]
    description, goal = ctx["descriptions"][cfg["process"]], cfg["simulation_goal"]
    feedback = case["feedback_text"].strip()            # the UI passes feedback_text.strip()
    previous_kpis_json = first.model_dump_json(indent=2)
    system_prompt, user_prompt = app["build_refinement_prompt"](
        process_description=app["sanitize_user_input"](description),
        simulation_goal=app["sanitize_user_input"](goal),
        previous_kpis_json=previous_kpis_json,
        human_feedback=app["sanitize_user_input"](feedback),
        accepted_kpi_names=accepted, rejected_kpi_names=rejected, total_kpis=len(first.kpis),
        log_evidence=log_evidence, context_evidence=context_evidence)
    feedback_gate = app["validate_refinement_scope"](description, goal, feedback)   # recorded, not enforced

    rec_provider = batch.RecordingProvider(ctx["provider"])
    started, t0 = datetime.now(timezone.utc).isoformat(timespec="seconds"), time.time()
    result = raw_output = validation = error = None
    batch._rep_state.parse_warnings, batch._rep_state.attempt = [], 0
    try:
        result, raw_output, validation = app["parse_with_retries"](
            provider=rec_provider, system_prompt=system_prompt, user_prompt=user_prompt, simulation_goal=goal,
            temperature=ctx["call"]["temperature"], log_profile=log_profile, context_evidence=context_evidence,
            json_mode=ctx["call"]["json_mode"])
        status = "ok"
    except app["KPIParsingError"] as exc:
        status, error, raw_output = "parse_error", str(exc), exc.raw_output
    except batch.FATAL_API_ERRORS as exc:
        sys.exit(f"fatal API error, aborting: {exc}")
    except batch.LLMCallFailed as exc:
        status, error = "llm_error", str(exc)
    except Exception:
        status, error = "app_error", traceback.format_exc()
    elapsed = round(time.time() - t0, 2)
    parse_warnings, batch._rep_state.parse_warnings = batch._rep_state.parse_warnings, None

    attempts = []
    for n, call in enumerate(rec_provider.calls, 1):
        outcome, attempt_error = batch.classify_attempt(app, call["raw"], goal, log_profile, context_evidence)
        attempts.append({"attempt": n, "elapsed_s": call["elapsed_s"], "outcome": outcome, "error": attempt_error,
                         "user_prompt": None if call["user_prompt"] == user_prompt else call["user_prompt"],
                         "raw": call["raw"]})
    issues = (validation or {}).get("issues", [])
    rec = {
        "case": case, "round": ROUND, "status": status, "error": error,
        "source_file": src_path.relative_to(batch.REPO_ROOT).as_posix(), "source_sha256": batch.sha256(src_bytes),
        "first_proposal_file": norm_path.relative_to(batch.REPO_ROOT).as_posix(),
        "first_proposal_file_sha256": batch.sha256(norm_path.read_bytes()),
        "first_proposal": norm["result"],
        "expected_keys": {e: expected_key(e, cfg) for e in case["expected"]},
        "decisions": {"accepted": accepted, "rejected": rejected, "undecided_treated_as_accepted": undecided,
                      "configured_accept": case["accept"], "configured_reject": case["reject"]},
        "feedback_text": case["feedback_text"], "human_feedback_sent": feedback,
        "feedback_scope_gate_passed": feedback_gate is None, "feedback_scope_gate_message": feedback_gate,
        "provider": type(ctx["provider"]).__name__, "model": ctx["provider"].get_model_name(),
        "temperature": ctx["call"]["temperature"], "json_mode": ctx["call"]["json_mode"],
        "process": cfg["process"], "simulation_goal": goal,
        "event_log": cfg["event_log"], "event_log_sha256": batch.sha256(log_path.read_bytes()),
        "script_sha256": ctx["script_sha"], "app_py_sha256": ctx["app_sha"], "glass_patch_sha256": ctx["patch_sha"],
        "pilot_config_sha256": ctx["pilot_sha"], "timestamp": started, "elapsed_s": elapsed,
        "previous_kpis_json": previous_kpis_json, "system_prompt": system_prompt, "user_prompt": user_prompt,
        "raw": raw_output, "attempts": attempts, "parse_warnings": parse_warnings,
        "result_parsed": app["parse_kpi_generation_payload"](raw_output).model_dump(mode="python") if result else None,
        "result": result.model_dump(mode="python") if result else None,
        "normalisation_warnings": [i for i in issues if i.get("code") == "measurable_as_normalised"],
        "fill_warnings": [i for i in issues if i.get("code") == "inferred_measurable_as"],
        "semantic_validation": validation,
    }
    return rec


SOFT_CREDIT_KPI = "Purchasing Agent Workload by Time of Day"


def write_readme(ctx: dict, rows: list[dict], started: str, ended: str) -> None:
    git = lambda *a: batch.subprocess.run(["git", *a], cwd=batch.REPO_ROOT, capture_output=True, text=True).stdout.strip()
    recs = {r["case"]: json.loads((OUT / "raw" / f"{r['case']}_round1.json").read_text(encoding="utf-8")) for r in rows}
    first = next(iter(recs.values()))
    gated = [c for c, rec in recs.items() if not rec["feedback_scope_gate_passed"]]
    dropped = [(c, n) for c, rec in recs.items() if rec.get("result")
               for n in rec["decisions"]["accepted"] if n not in {k["name"] for k in rec["result"]["kpis"]}]
    soft = [(c, k) for c, rec in recs.items() for k in (rec.get("result") or {}).get("kpis", [])
            if k["name"] == SOFT_CREDIT_KPI]
    llm_patch = OUT / "glass_local_fixes_llm_run.patch"
    stamps = sorted(rec["timestamp"] for rec in recs.values())
    post = {rec.get("postprocessing", {}).get("reprocessed_at") for rec in recs.values()} - {None}
    lines = [
        "# GLASS refinement pilot — round 1", "",
        f"Generated by `{batch.rel(__file__)}`. One refinement round per case through app.py's",
        "`handle_refinement` path (build_refinement_prompt -> parse_with_retries -> _finalize_generated_result).", "",
        "## Reproduce", "", "```",
        f"git checkout {git('rev-parse', 'HEAD')}",
        f"git apply {batch.rel(OUT / batch.PATCH_NAME)}",
        "# plus run_stage1_batch.py, run_refinement_pilot.py, pilot_config.json, stage1_config.json, input_logs/,",
        "# stage1_results/raw/ and stage1_results_normalised/raw/ matching the SHA-256 recorded per case",
        f"python {batch.rel(__file__)}              # 9 LLM calls, one round",
        f"python {batch.rel(Path(__file__).with_name('verify_refinement_pilot.py'))}", "```", "",
        "To replay this exact run: apply `glass_local_fixes_llm_run.patch` instead, run the pilot (the LLM",
        "calls), then switch goal_to_parameters/ to `glass_local_fixes.patch` and run",
        f"`python {batch.rel(__file__)} --reprocess` (no LLM call).", "",
        "## Run", "",
        f"- Base commit: `{git('rev-parse', 'HEAD')}`",
        f"- GLASS patch during the LLM calls: `{batch.rel(llm_patch)}`, SHA-256 "
        f"`{first['glass_patch_sha256']}` (= `stage1_results_normalised/{batch.PATCH_NAME}`: "
        f"{first['glass_patch_sha256'] == ctx['normalised_patch_sha']})",
        f"- GLASS patch of the stored post-processing: `{batch.rel(OUT / batch.PATCH_NAME)}`, SHA-256 "
        f"`{ctx['patch_sha']}`. It extends the LLM-run patch only in `normalise_measurable_as`: "
        "measurable_as_raw is written only when the value changes and never overwritten (first value wins).",
        "  The refinement prompts do not depend on that function, so the LLM inputs are the same under both patches.",
        f"- Records re-processed with it (deterministic post-processing only, no LLM call): {', '.join(sorted(post)) or 'no'}",
        f"- `goal_to_parameters/app.py` SHA-256 now: `{ctx['app_sha']}`; during the LLM calls: `{first['app_py_sha256']}`",
        f"- Provider / model: `{first['provider']}` / `{first['model']}`",
        f"- Temperature: {first['temperature']}, json_mode: {first['json_mode']} (keyword constants of",
        "  handle_refinement's parse_with_retries call, read from app.py); no few-shot messages",
        f"- `{batch.rel(PILOT_CONFIG)}` SHA-256: `{ctx['pilot_sha']}`",
        f"- `{batch.rel(batch.CONFIG_PATH)}` SHA-256: `{batch.sha256(batch.CONFIG_PATH.read_bytes())}`",
        f"- `{batch.rel(__file__)}` SHA-256 now: `{ctx['script_sha']}`; during the LLM calls: "
        f"`{first['script_sha256']}`",
        f"- `{batch.rel(batch.__file__)}` SHA-256: `{batch.sha256(Path(batch.__file__).read_bytes())}`",
        f"- Cases: {len(rows)}; rounds: {ROUND}; LLM calls {stamps[0]} .. {stamps[-1]}; "
        f"this invocation {started} .. {ended}",
        f"- Python {platform.python_version()}; openai {version('openai')}; pydantic {version('pydantic')}", "",
        "## Findings to keep in mind", "",
        f"- Feedback gate: the UI's keyword check on feedback (`validate_refinement_scope`) would block "
        f"{len(gated)}/{len(recs)} cases ({', '.join(gated) or 'none'}); the UI would show an error and not refine.",
        f"- Accepted KPIs dropped by the model ({len(dropped)}), although the prompt asks to keep accepted KPIs "
        "unchanged and to return exactly total_kpis KPIs; GLASS does not enforce this in code:",
        *[f"  - {c}: \"{n}\"" for c, n in dropped],
        *[f"- Soft credit: in {c}, \"{k['name']}\" (category {k['category']}, measurable_as "
          f"{k.get('measurable_as')}) counts towards Purchasing Agent Utilization under both rule sets, but its "
          f"formula `{k['suggested_formula']}` counts activity occurrences per hour; it is not a utilization measure."
          for c, k in soft],
        "- `outcome` (lenient scoring): reached_without_refinement = all expected KPIs already in the first "
        "proposal; reached_after_1_round = all present after round 1; not_reached_after_1_round otherwise.", "",
        "## Method notes", "",
        "- First proposal = `result` of the `stage1_results_normalised/` record whose",
        "  `normalisation.source_file_sha256` equals the SHA-256 of the `stage1_results/` source file (same LLM",
        "  answer, post-processed by the GLASS version used here). Before and after are scored under the same code.",
        "- Undecided KPIs are treated as accepted: the UI enables \"Refine KPIs\" only when no KPI is pending.",
        "  Each record lists them under `decisions.undecided_treated_as_accepted`.",
        "- Feedback is passed straight to the prompt. The UI's keyword check on feedback",
        "  (`validate_refinement_scope`) is recorded per case (`feedback_scope_gate_passed`), not enforced;",
        f"  it would block: {', '.join(gated) or 'none'}.",
        "- Expected KPIs are scored with the batch's rule sets; `<role> Utilization` is scored as",
        "  `Resource Utilization` (the lenient rule does not check the role).",
        "- `accepted_unchanged`: every accepted KPI is still present with identical name and suggested_formula.", "",
    ]
    (OUT / "README.md").write_text("\n".join(lines), encoding="utf-8")


def _canon(obj) -> object:
    return json.loads(json.dumps(obj, default=str))


def reprocess_record(rec: dict, ctx: dict, now: str) -> dict:
    """Re-run GLASS's deterministic post-processing (parse -> _finalize_generated_result) on the stored
    raw answer with the current code. No LLM call; raw, attempts and prompts stay as recorded. Aborts if
    anything other than measurable_as_raw would change."""
    if rec["status"] != "ok":
        return rec
    app, cfg = ctx["app"], ctx["configs"][rec["case"]["config_id"]]
    log_profile, _, context_evidence = ctx["artifacts"][cfg["event_log"]]
    parsed = app["parse_kpi_generation_payload"](rec["raw"])
    result, validation = app["_finalize_generated_result"](
        parsed, simulation_goal=rec["simulation_goal"], log_profile=log_profile, context_evidence=context_evidence)
    new, old = _canon(result.model_dump(mode="python")), rec["result"]
    without_raw = lambda r: _canon({**r, "kpis": [{k: v for k, v in kpi.items() if k != "measurable_as_raw"}
                                                  for kpi in r["kpis"]]})
    where = f"{rec['case']['config_id']} rep {rec['case']['rep']}"
    if without_raw(new) != without_raw(old):
        sys.exit(f"{where}: re-processing would change more than measurable_as_raw; aborting")
    if _canon(validation) != rec["semantic_validation"]:
        sys.exit(f"{where}: re-processing would change the semantic validation; aborting")
    if _canon(parsed.model_dump(mode="python")) != rec["result_parsed"]:
        sys.exit(f"{where}: stored result_parsed != parse(raw); aborting")
    before = (rec.get("postprocessing") or {}).get("result_before") or old   # keep the very first result
    rec["postprocessing"] = {
        "reprocessed_at": now, "glass_patch_sha256": ctx["patch_sha"], "app_py_sha256": ctx["app_sha"],
        "reason": "normalise_measurable_as no longer overwrites measurable_as_raw (written only on change, "
                  "first value wins); deterministic post-processing re-run on the stored raw answer, no LLM call",
        "measurable_as_raw_changed": [n["name"] for n, o in zip(new["kpis"], before["kpis"])
                                      if n.get("measurable_as_raw") != o.get("measurable_as_raw")],
        "result_before": before,
    }
    rec["result"] = new
    return rec


def main() -> None:
    global OUT
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=OUT, help="pilot folder (resumes: stored cases are skipped)")
    ap.add_argument("--reprocess", action="store_true",
                    help="re-run only the deterministic post-processing on the stored raw answers (no LLM call)")
    args = ap.parse_args()
    OUT = args.out
    pilot = json.loads(PILOT_CONFIG.read_text(encoding="utf-8"))
    stage1 = json.loads(batch.CONFIG_PATH.read_text(encoding="utf-8"))
    patch, patch_file = batch.glass_patch(), OUT / batch.PATCH_NAME
    if not patch and patch_file.exists():
        sys.exit(f"{patch_file} exists but goal_to_parameters/ has no local changes")
    if patch and (not patch_file.exists() or patch_file.read_bytes() != patch):
        sys.exit(f"goal_to_parameters/ local changes differ from {patch_file}; save them with\n"
                 f"  git diff --output={patch_file.relative_to(batch.REPO_ROOT).as_posix()} -- goal_to_parameters")

    sys.stdout = batch._MuteCostTracker(sys.stdout)
    logging.getLogger("utils.parsing").addHandler(batch._ParseWarnings())
    batch.APP_ROOTS = batch.APP_ROOTS + ("validate_refinement_scope",)   # also load the UI's feedback check
    app, _ = batch.load_app_stage1()
    configs = {c["id"]: c for c in stage1["configurations"]}
    artifacts = {}
    for case in pilot["cases"]:
        log_name = configs[case["config_id"]]["event_log"]
        if log_name not in artifacts:
            with open(batch.LOGS_DIR / log_name, "rb") as fh:
                artifacts[log_name] = app["extract_log_artifacts"](fh)
    ctx = {"app": app, "configs": configs, "descriptions": stage1["process_descriptions"], "artifacts": artifacts,
           "call": app_refinement_call(),
           "script_sha": batch.sha256(Path(__file__).read_bytes()), "app_sha": batch.sha256(batch.APP_PATH.read_bytes()),
           "patch_sha": batch.sha256(patch) if patch else None, "pilot_sha": batch.sha256(PILOT_CONFIG.read_bytes()),
           "normalised_patch_sha": batch.sha256((NORMALISED / batch.PATCH_NAME).read_bytes())}
    if not args.reprocess:   # no provider at all in --reprocess mode: it cannot call the LLM
        ctx["provider"] = batch.make_provider(stage1["settings"]["model"])
        batch.log(f"Provider: {ctx['provider'].get_model_name()}  call: {ctx['call']}  GLASS patch: {ctx['patch_sha']}")

    rows = []
    for n, case in enumerate(pilot["cases"], 1):
        path = OUT / "raw" / f"{case['config_id']}_rep{case['rep']}_round{ROUND}.json"
        prefix = f"[{n}/{len(pilot['cases'])}] {case['config_id']} rep {case['rep']} round {ROUND} ..."
        rec = batch.load_json(path)
        if args.reprocess:
            if rec is None:
                sys.exit(f"{prefix} no stored record to re-process")
            batch.write_json(path, reprocess_record(rec, ctx, started))
            rec = batch.load_json(path)
            batch.log(f"{prefix} re-processed (measurable_as_raw changed for "
                      f"{len((rec.get('postprocessing') or {}).get('measurable_as_raw_changed', []))} KPIs)")
        elif rec and rec["status"] in batch.FINAL_STATUSES:
            batch.log(f"{prefix} skipped (exists: {rec['status']})")
        else:
            if n > 1:
                time.sleep(batch.PAUSE_S)
            batch.write_json(path, run_case(case, ctx))
            rec = batch.load_json(path)   # score what is stored (enums as their JSON values)
            kpis = (rec["result"] or {}).get("kpis") or []
            batch.log(f"{prefix} {rec['status']} ({len(kpis)} KPIs, {len(rec['attempts'])} attempt(s), {rec['elapsed_s']:.1f} s)")
        rows.append(summary_row(rec))
    batch.write_csv(OUT / "pilot_summary.csv", rows)
    write_readme(ctx, rows, started, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    batch.log(f"\nWrote pilot_summary.csv and README.md in {OUT}")


if __name__ == "__main__":
    main()
