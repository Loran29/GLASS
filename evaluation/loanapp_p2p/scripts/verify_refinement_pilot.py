"""
Independent verification of results/refinement_pilot/ (report only: never edits or reruns anything).

Records: round 1 for every case of setup/pilot_config.json, plus one record per entry of
setup/pilot_rounds.json (round n refines round n-1's delivered result).

Checks
  1. files     : one raw file per pilot case, status ok; one model / temperature / json_mode / patch /
                 script SHA-256; temperature and json_mode equal handle_refinement's constants in app.py.
  2. sources   : SHA-256 of every stage1_results/ source file == recorded; the stage1_results_normalised/
                 record cites that SHA-256 and carries the same raw LLM answer; first_proposal == its result;
                 previous_kpis_json == KPIGenerationResult(first_proposal).model_dump_json(indent=2).
  3. decisions : accepted + rejected = all first-proposal KPIs; rejected == configured reject; configured
                 accept within accepted; the prompt lists exactly these names and the feedback text.
  4. accepted  : every accepted KPI byte-identical (canonical JSON) (a) in the model's raw answer vs the
                 previous_kpis_json it was shown, (b) delivered result vs first proposal. Differences are
                 listed per field.
  5. summary   : pilot_summary.csv rebuilt from the raw files with fresh code and diffed cell by cell.
  6. patch     : goal_to_parameters/ == base commit (from README.md) + refinement_pilot/
                 glass_local_fixes.patch (the post-processing patch, which check 7 re-executes); glass_local_fixes_llm_run.patch (used during the LLM calls) ==
                 stage1_results_normalised/glass_local_fixes.patch.
  7. post-proc : re-running today's parse + _finalize_generated_result on every stored raw answer
                 reproduces the stored result; re-processing changed nothing but measurable_as_raw.

Raw records store the paths of their source files in the pre-move layout (evaluation/stage1_results/...);
they are resolved to results/... here, so the records stay byte-identical.

Writes results/refinement_pilot/verification_report.md.
Usage (repo root):  python evaluation/loanapp_p2p/scripts/verify_refinement_pilot.py
"""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent          # evaluation/loanapp_p2p
REPO = BASE.parent.parent
RESULTS = BASE / "results"
PILOT = RESULTS / "refinement_pilot"
SETUP = BASE / "setup"
LOGS = BASE / "input_logs"
sys.path.insert(0, str(REPO / "goal_to_parameters"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from models import KPIGenerationResult  # noqa: E402
from verify_stage1_results import Report, lenient_rule, raw_kpis  # noqa: E402  (fresh rule set, not the batch's)


def stored_path(path: str) -> Path:
    """A path stored in a raw record. Records written before the move use evaluation/<folder>/...;
    those folders now live in results/<folder>/."""
    p = Path(path)
    if (REPO / p).exists():
        return REPO / p
    if p.parts[:1] == ("evaluation",) and len(p.parts) > 2:
        return RESULTS / Path(*p.parts[1:])
    return REPO / p


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def field_diff(a: dict, b: dict) -> list[str]:
    return sorted(k for k in set(a) | set(b) if canon(a.get(k)) != canon(b.get(k)))


def handle_refinement_constants() -> dict:
    tree = ast.parse((REPO / "goal_to_parameters" / "app.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "handle_refinement")
    found = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.keyword) and node.arg in ("temperature", "json_mode") and isinstance(node.value, ast.Constant):
            found[node.arg] = node.value.value
    return found


def key_for(expected: str) -> str:
    return "Resource Utilization" if re.search(r" Utilization$", expected) else expected


def fresh_row(rec: dict, max_rounds: int, manual: dict) -> dict[str, str]:
    case, rnd = rec["case"], rec["round"]
    before, after = rec["first_proposal"]["kpis"], (rec.get("result") or {}).get("kpis") or []
    exp = case["expected"]

    def strict(kpis):
        vals = [k["measurable_as"].lower() for k in kpis if k.get("measurable_as")]
        return [e for e in exp if key_for(e).lower() in vals]

    def lenient(kpis):
        return [e for e in exp if any(lenient_rule(key_for(e), k) for k in kpis)]

    keys = {key_for(e).lower() for e in exp}
    s_extra = [k["name"] for k in after if k.get("measurable_as") and k["measurable_as"].lower() not in keys]
    l_extra = [k["name"] for k in after if all(not lenient_rule(key_for(e), k) for e in exp)]
    before_formula = {k["name"]: k.get("suggested_formula") for k in before}
    after_formula = {k["name"]: k.get("suggested_formula") for k in after}
    changed = [n for n in rec["decisions"]["accepted"]
               if n not in after_formula or after_formula[n] != before_formula[n]]
    row = {"case": f"{case['config_id']}_rep{case['rep']}", "config_id": case["config_id"], "rep": case["rep"],
           "round": rnd, "protocol": case.get("protocol") or "", "status": rec["status"], "attempts": len(rec["attempts"]),
           "accepted": "|".join(rec["decisions"]["accepted"]),
           # round 1: KPIs left undecided in pilot_config.json; rounds >= 2 accept every non-rejected KPI by rule
           "accepted_from_undecided": "|".join(n for n in rec["decisions"]["accepted"] if n not in case["accept"])
           if rnd == 1 else "",
           "rejected": "|".join(rec["decisions"]["rejected"]), "feedback_text": rec["feedback_text"],
           "expected": "|".join(exp)}
    for mode, fn, extra in (("strict", strict, s_extra), ("lenient", lenient, l_extra)):
        row[f"{mode}_present_before"] = "|".join(fn(before))
        row[f"{mode}_present_after"] = "|".join(fn(after))
        row[f"{mode}_still_missing"] = "|".join(e for e in exp if e not in fn(after))
        row[f"{mode}_n_extras_after"] = len(extra)
        row[f"{mode}_extras_after"] = "|".join(extra)
    reached_after = bool(after) and set(lenient(after)) == set(exp)
    if rnd == 1 and set(lenient(before)) == set(exp):
        row["outcome"] = "reached_without_refinement"
    elif rnd == 1:
        row["outcome"] = "reached_after_1_round" if reached_after else "not_reached_after_1_round"
    elif reached_after:
        row["outcome"] = f"reached_after_{rnd}_rounds"
    elif rnd >= max_rounds:
        row["outcome"] = f"not_converged_after_{rnd}_rounds"
    else:
        row["outcome"] = f"not_reached_after_{rnd}_rounds"
    row["manual_outcome"] = manual.get(f"{row['case']}_round{rnd}", {}).get("outcome", "")
    row["accepted_unchanged"] = ("no" if changed else "yes") if after else ""
    row["accepted_changed"] = "|".join(changed) if after else ""
    row["rejected_names_still_present"] = "|".join(n for n in rec["decisions"]["rejected"] if n in after_formula)
    row["n_kpis_before"], row["n_kpis_returned"] = len(before), len(after)
    row["returned_kpis"] = ";".join("|".join([k["name"], k.get("measurable_as") or "", k["target_direction"],
                                              k["process_scope"]]) for k in after)
    return {k: str(v) for k, v in row.items()}


def main() -> None:
    pilot = json.loads((SETUP / "pilot_config.json").read_text(encoding="utf-8"))
    report = Report()
    rounds_path = SETUP / "pilot_rounds.json"
    rounds = json.loads(rounds_path.read_text(encoding="utf-8")) if rounds_path.exists() else {"max_rounds": 1, "rounds": []}
    base_cases = {(c["config_id"], c["rep"]): c for c in pilot["cases"]}
    expected_cases = {}   # label -> the case each record must store
    for case in pilot["cases"]:
        expected_cases[f"{case['config_id']}_rep{case['rep']}_round1"] = case
    for e in rounds["rounds"]:
        base = base_cases[(e["config_id"], e["rep"])]
        expected_cases[f"{e['config_id']}_rep{e['rep']}_round{e['round']}"] = {
            "config_id": e["config_id"], "rep": e["rep"], "round": e["round"], "protocol": e.get("protocol"),
            "expected": base["expected"], "accept": [], "reject": e["reject"], "feedback_text": e["feedback_text"]}
    records, fails = {}, []
    for name, case in expected_cases.items():
        path = PILOT / "raw" / f"{name}.json"
        if not path.exists():
            fails.append(f"missing raw/{path.name}")
            continue
        rec = records[name] = json.loads(path.read_text(encoding="utf-8"))
        if rec["status"] != "ok":
            fails.append(f"{name}: status {rec['status']}")
        if rec["case"] != case or rec["round"] != case.get("round", 1):
            fails.append(f"{name}: stored case / round differs from pilot_config.json / pilot_rounds.json")
        if case.get("round", 1) > rounds["max_rounds"]:
            fails.append(f"{name}: round beyond max_rounds {rounds['max_rounds']}")
    extra = sorted(p.name for p in (PILOT / "raw").glob("*.json") if p.stem not in records)
    fails += [f"unexpected file raw/{n}" for n in extra]
    constants = handle_refinement_constants()
    for field in ("model", "temperature", "json_mode"):
        values = {canon(r.get(field)) for r in records.values()}
        if len(values) != 1:
            fails.append(f"{field}: {sorted(values)}")
    for rnd in sorted({r["round"] for r in records.values()}):   # code and script are fixed within a round
        for field in ("glass_patch_sha256", "script_sha256", "app_py_sha256"):
            values = {canon(r.get(field)) for r in records.values() if r["round"] == rnd}
            if len(values) != 1:
                fails.append(f"round {rnd} {field}: {sorted(values)}")
    for field in ("temperature", "json_mode"):
        if {r.get(field) for r in records.values()} != {constants.get(field)}:
            fails.append(f"{field} != handle_refinement's {constants.get(field)!r}")
    report.add(f"1. raw files ({len(records)}/{len(expected_cases)} records, max {rounds['max_rounds']} rounds; "
               f"handle_refinement {constants})", len(expected_cases) + 8, fails)

    fails, checked = [], 0
    for name, rec in records.items():
        if rec["round"] > 1:
            checked += 3
            prev_bytes = stored_path(rec["previous_round_file"]).read_bytes()
            if digest(prev_bytes) != rec["previous_round_sha256"]:
                fails.append(f"{name}: {rec['previous_round_file']} no longer has the recorded SHA-256")
            if Path(rec["previous_round_file"]).name != f"{name.rsplit('_round', 1)[0]}_round{rec['round'] - 1}.json" or \
                    canon(rec["first_proposal"]) != canon(json.loads(prev_bytes)["result"]):
                fails.append(f"{name}: first_proposal is not round {rec['round'] - 1}'s delivered result")
            if rec["previous_kpis_json"] != KPIGenerationResult.model_validate(rec["first_proposal"]).model_dump_json(indent=2):
                fails.append(f"{name}: previous_kpis_json is not the first proposal's model_dump_json(indent=2)")
            continue
        checked += 5
        src_bytes = stored_path(rec["source_file"]).read_bytes()
        norm_bytes = stored_path(rec["first_proposal_file"]).read_bytes()
        norm = json.loads(norm_bytes)
        if digest(src_bytes) != rec["source_sha256"]:
            fails.append(f"{name}: {rec['source_file']} no longer has the recorded SHA-256")
        if digest(norm_bytes) != rec["first_proposal_file_sha256"] or \
                norm["normalisation"]["source_file_sha256"] != digest(src_bytes):
            fails.append(f"{name}: normalised record SHA-256 / source citation mismatch")
        if json.loads(src_bytes)["raw"] != norm["raw"]:
            fails.append(f"{name}: normalised record carries a different LLM answer than the source")
        if canon(rec["first_proposal"]) != canon(norm["result"]):
            fails.append(f"{name}: first_proposal != normalised record's result")
        if rec["previous_kpis_json"] != KPIGenerationResult.model_validate(rec["first_proposal"]).model_dump_json(indent=2):
            fails.append(f"{name}: previous_kpis_json is not the first proposal's model_dump_json(indent=2)")
    report.add("2. first proposals: round 1 = normalised result of the stored answer (source files byte-identical); "
               "round n = round n-1's delivered result", checked, fails)

    fails, checked = [], 0
    for name, rec in records.items():
        checked += 4
        d, names = rec["decisions"], [k["name"] for k in rec["first_proposal"]["kpis"]]
        if sorted(d["accepted"] + d["rejected"]) != sorted(names):
            fails.append(f"{name}: accepted + rejected != first-proposal KPIs")
        if d["rejected"] != [n for n in names if n in rec["case"]["reject"]] or not set(rec["case"]["accept"]) <= set(d["accepted"]):
            fails.append(f"{name}: decisions do not follow pilot_config.json / pilot_rounds.json")
        if rec["round"] > 1 and (d["accepted"] != [n for n in names if n not in rec["case"]["reject"]]
                                 or set(rec["case"]["reject"]) - set(names)):
            checked += 1
            fails.append(f"{name}: round {rec['round']} must reject exactly the listed KPIs and accept all others")
        u = rec["user_prompt"]
        if f"Accepted KPIs (keep exactly unchanged): {', '.join(d['accepted']) or 'None'}\n" not in u or \
                f"Rejected KPIs (replace one-for-one): {', '.join(d['rejected']) or 'None'}\n" not in u:
            fails.append(f"{name}: prompt does not list the recorded decisions")
        if rec["case"]["feedback_text"].strip() not in u or rec["previous_kpis_json"] not in u:
            fails.append(f"{name}: prompt lacks the feedback text or previous_kpis_json")
    report.add("3. decisions and prompt contents", checked, fails)

    model_fails, delivered_fails, n_accepted = [], [], 0
    for name, rec in records.items():
        if not rec.get("result"):
            continue
        shown = {k["name"]: k for k in json.loads(rec["previous_kpis_json"])["kpis"]}
        answered = {k.get("name"): k for k in raw_kpis(rec["raw"])}
        before = {k["name"]: k for k in rec["first_proposal"]["kpis"]}
        after = {k["name"]: k for k in rec["result"]["kpis"]}
        for kpi in rec["decisions"]["accepted"]:
            n_accepted += 1
            if kpi not in answered:
                model_fails.append(f"{name}: accepted KPI '{kpi}' missing from the model's answer")
            elif canon(answered[kpi]) != canon(shown[kpi]):
                model_fails.append(f"{name}: '{kpi}' fields changed by the model: {field_diff(shown[kpi], answered[kpi])}")
            if kpi not in after:
                delivered_fails.append(f"{name}: accepted KPI '{kpi}' missing from the delivered result")
            elif canon(after[kpi]) != canon(before[kpi]):
                fields = field_diff(before[kpi], after[kpi])
                note = " (only GLASS's measurable_as_raw provenance field)" if fields == ["measurable_as_raw"] else ""
                delivered_fails.append(f"{name}: '{kpi}' delivered != first proposal; fields: {fields}{note}")
    report.add(f"4a. accepted KPIs byte-identical: model's answer vs KPI shown in previous_kpis_json "
               f"({n_accepted - len(model_fails)}/{n_accepted} identical)", n_accepted, model_fails)
    report.add(f"4b. accepted KPIs byte-identical: delivered result vs first proposal "
               f"({n_accepted - len(delivered_fails)}/{n_accepted} identical)", n_accepted, delivered_fails)

    fails = []
    with open(PILOT / "pilot_summary.csv", newline="", encoding="utf-8") as f:
        csv_rows = {f"{r['case']}_round{r['round']}": r for r in csv.DictReader(f)}
    fresh = {name: fresh_row(rec, rounds["max_rounds"], rounds.get("manual_outcomes", {})) for name, rec in records.items()}
    for name in sorted(set(csv_rows) | set(fresh)):
        if name not in csv_rows or name not in fresh:
            fails.append(f"{name}: row only in {'rebuild' if name in fresh else 'pilot_summary.csv'}")
            continue
        for col in sorted(set(csv_rows[name]) | set(fresh[name])):
            if csv_rows[name].get(col) != fresh[name].get(col):
                fails.append(f"{name} column {col}: csv={csv_rows[name].get(col)!r} rebuilt={fresh[name].get(col)!r}")
    report.add(f"5. pilot_summary.csv rebuilt independently ({len(fresh)} rows)", len(fresh), fails)

    fails = []
    patch = (PILOT / "glass_local_fixes.patch").read_bytes()
    llm_patch = (PILOT / "glass_local_fixes_llm_run.patch").read_bytes()
    base = re.search(r"Base commit: `([0-9a-f]{40})`", (PILOT / "README.md").read_text(encoding="utf-8")).group(1)
    current = subprocess.run(["git", "diff", base, "--", "goal_to_parameters"], cwd=REPO, capture_output=True,
                             check=True).stdout
    if patch != current:
        fails.append(f"goal_to_parameters/ differs from {base[:7]} + refinement_pilot/glass_local_fixes.patch")
    if llm_patch != (RESULTS / "stage1_results_normalised" / "glass_local_fixes.patch").read_bytes():
        fails.append("LLM-run patch differs from stage1_results_normalised/glass_local_fixes.patch")
    if {r["glass_patch_sha256"] for r in records.values() if r["round"] == 1} != {digest(llm_patch)}:
        fails.append("round-1 records record a different LLM-run patch SHA-256")
    if {r["glass_patch_sha256"] for r in records.values() if r["round"] > 1} - {digest(patch)}:
        fails.append("records of rounds >= 2 were not produced with base + glass_local_fixes.patch")
    post = {(r.get("postprocessing") or {}).get("glass_patch_sha256") for r in records.values()
            if r["status"] == "ok" and r["round"] == 1}
    if post != {digest(patch)}:
        fails.append(f"stored post-processing patch SHA-256 {sorted(map(str, post))} != {digest(patch)}")
    report.add("6. GLASS = commit + patch (round-1 LLM calls: stage1_results_normalised patch; later rounds and "
               "all post-processing: current patch)", 5, fails)

    # 7. stored result = today's post-processing of the stored raw answer ------------------------
    import run_stage1_batch as batch   # only to load the app.py functions the pilot executes, not to score
    batch.APP_ROOTS = batch.APP_ROOTS + ("validate_refinement_scope",)
    app, _ = batch.load_app_stage1()
    config = json.loads((SETUP / "stage1_config.json").read_text(encoding="utf-8"))
    logs = {c["id"]: c["event_log"] for c in config["configurations"]}
    fails, checked, artifacts = [], 0, {}
    strip_raw = lambda r: canon({**r, "kpis": [{k: v for k, v in kpi.items() if k != "measurable_as_raw"} for kpi in r["kpis"]]})
    for name, rec in records.items():
        if not rec.get("result"):
            continue
        checked += 2
        log_name = logs[rec["case"]["config_id"]]
        if log_name not in artifacts:
            with open(LOGS / log_name, "rb") as fh:
                artifacts[log_name] = app["extract_log_artifacts"](fh)
        log_profile, _, context_evidence = artifacts[log_name]
        result, _ = app["_finalize_generated_result"](app["parse_kpi_generation_payload"](rec["raw"]),
                                                      simulation_goal=rec["simulation_goal"], log_profile=log_profile,
                                                      context_evidence=context_evidence)
        if canon(json.loads(json.dumps(result.model_dump(mode="python"), default=str))) != canon(rec["result"]):
            fails.append(f"{name}: stored result != current post-processing of the stored raw answer")
        before = (rec.get("postprocessing") or {}).get("result_before")
        if before is not None and strip_raw(before) != strip_raw(rec["result"]):
            fails.append(f"{name}: re-processing changed more than measurable_as_raw")
    report.add("7. stored result = current post-processing of the stored raw answer (no LLM call)", checked, fails)

    verdict = "PASS" if report.passed else "FAIL"
    md = [f"# Refinement pilot verification: {verdict}", "",
          f"Produced by `{Path(__file__).resolve().relative_to(REPO).as_posix()}`; findings are reported, never patched.", "",
          "| check | checks | failures | result |", "|---|---|---|---|"]
    md += [f"| {t} | {n} | {len(f)} | {'PASS' if not f else 'FAIL'} |" for t, n, f in report.sections]
    for title, _, f in report.sections:
        if f:
            md += ["", f"## {title}", ""] + [f"- {x}" for x in f]
    (PILOT / "verification_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\nOVERALL: {verdict}  (report: {PILOT / 'verification_report.md'})")
    sys.exit(0 if report.passed else 1)


if __name__ == "__main__":
    main()
