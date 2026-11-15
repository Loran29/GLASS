"""
Independent check of results/stage2/ (report only; nothing is changed).

  1. records: one per variant x configuration x repetition with a final status; variant and switches as defined;
     knowledge base off in every run (use_knowledge_base false, no knowledge-base section in any generation
     prompt or feedback); V_no_log: no log profile passed and no log-evidence section in the generation prompts;
     V_no_context: no chat turns; V_no_refine: at most 1 iteration; one GLASS commit
  2. the three CSVs of synthetic_ablation_reporting.py, rebuilt from the records with this script's own code
     (measures, per cell, per variant, differences to V_main, Fisher and sign tests) and compared cell by cell
  3. verify_stage2_results.py (the v3.2 verifier) on every variant folder, unchanged except for one skip: its
     re-merge check re-validates the stored patch with ScenarioPatch, which rejects a patch with no modifications
     and no unresolved KPIs. GLASS accepted such empty patches in a few iterations (patch validator passed), so
     for those patches the re-merge comparison is skipped (V32_RUNNER below; the file itself is not changed).
     The empty accepted patches are reported as a finding, and the number of skipped re-merges per folder must
     equal the number of evaluated phases (initial / final) that use an empty patch.
     verify_stage2_results.py is not part of this release; without it, check 3 is skipped (as with --skip-v32).
Usage (repo root): python evaluation/experiments/Experiment_3_synthetic_ablation/scripts/verify_synthetic_ablation.py
       [--results DIR] [--skip-v32]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from math import comb
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "shared" / "loanapp_p2p"))
import lp_paths as P  # noqa: E402
REPO = P.REPO
V32_VERIFIER = HERE / "verify_stage2_results.py"   # not part of this release
sys.path.insert(0, str(REPO / "goal_to_parameters"))
CONFIGS = ["LoanApp_G1", "LoanApp_G2", "LoanApp_G1G2", "Procure2Pay_G1", "Procure2Pay_G2", "Procure2Pay_G1G2"]
VARIANTS = ["V_main", "V_no_log", "V_no_context", "V_no_refine", "V_old_refine"]
SWITCHES = {"V_main": (True, True, 4, False), "V_no_log": (False, True, 4, False), "V_no_context": (True, False, 4, False),
            "V_no_refine": (True, True, 1, False), "V_old_refine": (True, True, 4, True)}
KPI_SECTION, LOG_SECTION = "Verified KPI Targets", "Event Log Evidence (filtered)"
KB_SECTIONS = ("Literature-Backed Parameter Recommendations", "## Literature-Backed Alternative Interventions")

# Runs verify_stage2_results.main() with the re-merge of empty patches skipped: an empty patch is replaced by a
# marker, apply_patch returns a stand-in whose scenario compares equal to the stored merged scenario, and the
# number of skips is printed on the last line ("EMPTY_PATCH_REMERGES_SKIPPED <n>"). Everything else is unchanged.
V32_RUNNER = r"""
import importlib.util, sys, types
path = sys.argv[1]
sys.argv = [path] + sys.argv[2:]
spec = importlib.util.spec_from_file_location("verify_stage2_results", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
import second_llm.output_schema_patch as osp
import second_llm.scenario_merger as sm
RealPatch, real_apply = osp.ScenarioPatch, sm.apply_patch
EMPTY, skipped = object(), [0]
class _Equal:
    def __eq__(self, other): return True
    def __ne__(self, other): return False
class _Skip:
    def model_dump(self, **kw): return _Equal()
class PatchShim:
    @staticmethod
    def model_validate(data, *a, **kw):
        if isinstance(data, dict) and not data.get("modifications") and not data.get("unresolved_kpis"):
            skipped[0] += 1
            return EMPTY
        return RealPatch.model_validate(data, *a, **kw)
def apply_shim(baseline, patch, *a, **kw):
    if patch is EMPTY:
        return types.SimpleNamespace(scenario=_Skip())
    return real_apply(baseline, patch, *a, **kw)
osp.ScenarioPatch, sm.apply_patch = PatchShim, apply_shim
try:
    mod.main()
finally:
    print(f"EMPTY_PATCH_REMERGES_SKIPPED {skipped[0]}")
"""


def empty_patch(it: dict) -> bool:
    p = it.get("patch")
    return isinstance(p, dict) and not p.get("modifications") and not p.get("unresolved_kpis")


def binom_p(k: int, n: int) -> float:
    return min(1.0, 2 * sum(comb(n, i) for i in range(min(k, n - k) + 1)) / 2 ** n) if n else 1.0


def fisher_p(a, na, b, nb) -> float:
    from scipy.stats import fisher_exact
    return float(fisher_exact([[a, na - a], [b, nb - b]])[1])


def merge(notes: list[str], calls: list[dict], iteration: int) -> tuple:
    """(merge stability applied / proposed, GLASS's applied / reaching) of the last attempt that reached the merger."""
    from second_llm.scenario_generator import _extract_and_parse_json
    attempt, last = None, None
    for n in notes:
        m = re.match(r"Patch attempt (\d+)/", n)
        if m:
            attempt = int(m.group(1))
        elif n.startswith("[merge stability]") and attempt is not None:
            a, b = n.split("] ", 1)[1].split(" ")[0].split("/")
            last = (attempt, int(a), int(b), sum(x.startswith("[patch-reject]") for x in notes_of(notes, attempt)))
    if last is None:
        return None, None
    attempt, applied, reaching, rejected = last
    gens = [c for c in calls if c.get("iteration") == iteration and KPI_SECTION in (c.get("user_prompt") or "")]
    proposed = None
    if attempt <= len(gens):
        try:
            proposed = len(_extract_and_parse_json(gens[attempt - 1]["raw"]).get("modifications") or [])
        except Exception:
            proposed = None
    if proposed is None:
        proposed = reaching + rejected
    return (round(applied / proposed, 4) if proposed else None), (round(applied / reaching, 4) if reaching else 1.0)


def notes_of(notes: list[str], attempt: int) -> list[str]:
    out, cur = [], None
    for n in notes:
        m = re.match(r"Patch attempt (\d+)/", n)
        if m:
            cur = int(m.group(1))
        elif cur == attempt:
            out.append(n)
    return out


def phase(rec: dict, name: str) -> dict:
    ev = (rec.get("evaluation") or {}).get(name) or {}
    if rec["status"] != "ok" or ev.get("iteration") is None or ev.get("error") == "no scenario":
        return {}
    it = rec["loop"]["iterations"][ev["iteration"] - 1]
    stab, gstab = merge((it.get("generation") or {}).get("generation_notes") or [], rec.get("llm_calls") or [],
                        ev["iteration"])
    kpis = ev.get("kpis") or []
    used = [k for k in kpis if k.get("glass_improved") is not None and k.get("pct_change") is not None]
    valid = bool(ev.get("patch_validator_passed")) and not ev.get("violations")
    exe = bool(ev.get("multi_seed_ok")) and ev.get("n_logs") == len(rec["seeds"])
    th = [k for k in kpis if k.get("threshold") is not None]
    return {"it": ev["iteration"], "same": bool(ev.get("same_as_initial")), "valid": valid, "executable": exe,
            "stab": stab, "gstab": gstab, "adm": valid and exe and (stab or 0) > 0,
            "all": bool(kpis) and all(k.get("glass_improved") is True for k in kpis),
            "DHR": sum(bool(k["glass_improved"]) for k in used) / len(used) if used else None,
            "NIS": sum(abs(k["pct_change"]) * (1 if k["glass_improved"] else -1) for k in used) / len(used) if used else None,
            "util": all(k.get("glass_achieved") is True for k in th) if th else "",
            "sig": any(k.get("glass_improved") is True and k.get("significant") is True for k in kpis),
            "recall": (ev.get("scores") or {}).get("recall"), "ctd": (ev.get("improvement_ratio") or {}).get("CTD"),
            "nmods": (ev.get("scores") or {}).get("n_modifications")}


def discarded(ini: dict, fin: dict, kept: bool) -> bool:
    if not ini or not ini["adm"]:
        return False
    if kept:
        return (ini["DHR"] or 0) > 0 or (ini["NIS"] is not None and ini["NIS"] > 1.0)
    if fin.get("same") or fin.get("it") in (None, 1):
        return False
    if not fin.get("adm"):
        return True
    if ini["DHR"] is not None and fin["DHR"] is not None and ini["DHR"] > fin["DHR"]:
        return True
    return ini["DHR"] == fin["DHR"] and ini["NIS"] is not None and fin["NIS"] is not None and ini["NIS"] - fin["NIS"] > 1.0


def run_values(rec: dict) -> dict:
    fin, ini = phase(rec, "final"), phase(rec, "initial")
    kept = bool((rec.get("loop") or {}).get("no_improving_scenario"))
    v = {"scenario_produced": bool(fin), "valid": bool(fin.get("valid")), "executable": bool(fin.get("executable")),
         "merge_stability_positive": bool(fin) and (fin.get("stab") or 0) > 0, "all_kpis_improved": bool(fin.get("all")),
         "DHR": fin.get("DHR"), "NIS": fin.get("NIS"), "util_target_achieved": fin.get("util", ""),
         "admissible": bool(fin.get("adm")), "admissible_and_all_improved": bool(fin.get("adm")) and bool(fin.get("all")),
         "any_kpi_significant_improvement": bool(fin.get("sig")),
         "loop_discarded_better_initial": rec["status"] == "ok" and discarded(ini, fin, kept),
         "no_improving_scenario": kept, "recall": fin.get("recall"), "CTD_ratio": fin.get("ctd"),
         "merge_stability": fin.get("stab"), "glass_merge_stability": fin.get("gstab"), "n_modifications": fin.get("nmods"),
         "iterations_used": len((rec.get("loop") or {}).get("iterations") or []), "elapsed_s": rec.get("elapsed_s"),
         "initial_DHR": ini.get("DHR"), "initial_NIS": ini.get("NIS"), "initial_admissible": bool(ini.get("adm"))}
    return v


RATES = ["scenario_produced", "valid", "executable", "merge_stability_positive", "all_kpis_improved",
         "util_target_achieved", "admissible", "admissible_and_all_improved", "any_kpi_significant_improvement",
         "loop_discarded_better_initial", "no_improving_scenario"]
MEANS = ["DHR", "NIS", "recall", "CTD_ratio", "merge_stability", "glass_merge_stability", "n_modifications",
         "iterations_used", "elapsed_s"]


def same(written: str, val) -> bool:
    if val is None or val == "":
        return written == ""
    if isinstance(val, bool):
        return written == str(val)
    if isinstance(val, (int, float)):
        try:
            return abs(float(written) - float(val)) <= 1e-4
        except ValueError:
            return False
    return written == str(val)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=P.SYNTH_ABLATION / "results" / "stage2")
    ap.add_argument("--skip-v32", action="store_true")
    args = ap.parse_args()
    out = args.results
    fails = {"records": [], "csv": [], "v32": []}
    checks = 0
    recs, commits = {}, set()
    for v in VARIANTS:
        for cid in CONFIGS:
            for rep in range(1, 6):
                p = out / v / "raw" / cid / f"rep_{rep}.json"
                checks += 1
                if not p.exists():
                    fails["records"].append(f"missing {v}/raw/{cid}/rep_{rep}.json")
                    continue
                r = recs[(v, cid, rep)] = json.loads(p.read_text(encoding="utf-8"))
                commits.add(r["glass_commit"])
                sw = r["switches"]
                log, ctx, iters, legacy = SWITCHES[v]
                gens = [c for c in r.get("llm_calls") or [] if KPI_SECTION in (c.get("user_prompt") or "")]
                feedback = [it.get("feedback_text") or "" for it in (r.get("loop") or {}).get("iterations") or []]
                tests = [
                    (r["variant"] == v and r["config_id"] == cid and r["rep"] == rep, "header"),
                    (r["status"] in ("ok", "no_initial_proposal", "app_error"), f"status {r['status']}"),
                    ((sw["use_log_profile"], sw["use_context"], sw["max_iterations"], sw["legacy_refinement"],
                      sw["use_kb"]) == (log, ctx, iters, legacy, False), f"switches {sw}"),
                    (r["use_knowledge_base"] is False, "use_knowledge_base not false"),
                    (not any(s in (c.get("user_prompt") or "") for c in gens for s in KB_SECTIONS)
                     and not any(KB_SECTIONS[1] in f for f in feedback), "knowledge-base section present"),
                    (r["log_profile_passed"] is log, "log_profile_passed"),
                    (all((LOG_SECTION in c["user_prompt"]) is log for c in gens), "log-evidence section"),
                    ((len(r["chat_user_turns"]) > 0) is ctx, "chat turns"),
                    (len((r.get("loop") or {}).get("iterations") or []) <= iters, "iterations above the limit"),
                    (r["settings"]["max_iterations"] == iters, "settings.max_iterations"),
                ]
                for ok, msg in tests:
                    checks += 1
                    if not ok:
                        fails["records"].append(f"{v} {cid} rep {rep}: {msg}")
    checks += 1
    if len(commits) != 1:
        fails["records"].append(f"GLASS commits {sorted(commits)}")

    # finding (not a failure): iterations whose patch GLASS accepted although it carries no modification
    findings, expected_skips = [], dict.fromkeys(VARIANTS, 0)
    for (v, cid, rep), r in sorted(recs.items()):
        its = (r.get("loop") or {}).get("iterations") or []
        for i, it in enumerate(its, 1):
            if empty_patch(it):
                used = [ph for ph, ev in (r.get("evaluation") or {}).items()
                        if "error" not in ev and ev.get("iteration") == i]
                expected_skips[v] += len(used)
                findings.append(f"{v} {cid} rep {rep}, iteration {i} of {len(its)}: empty patch, patch validator "
                                f"{'passed' if it.get('patch_validator_passed') else 'failed'}; "
                                f"evaluated as: {', '.join(used) or 'not evaluated'}")

    vals = {k: run_values(r) for k, r in recs.items()}

    def read(name):
        return list(csv.DictReader(open(out / name, encoding="utf-8")))

    # per run
    for w in read("synthetic_ablation_summary.csv"):
        key = (w["variant"], w["config_id"], int(w["rep"]))
        if key not in vals:
            fails["csv"].append(f"summary: unexpected row {key}")
            continue
        for k, val in vals[key].items():
            checks += 1
            if not same(w.get(k, "<missing>"), val):
                fails["csv"].append(f"summary {key}: {k} written {w.get(k)!r}, rebuilt {val!r}")

    def rate(rows, k):
        xs = [r[k] for r in rows if r[k] != ""]
        return sum(x is True for x in xs), len(xs)

    def avg(rows, k):
        xs = [r[k] for r in rows if r[k] not in ("", None)]
        return round(mean(xs), 4) if xs else ""

    # per cell
    cells = {(w["config_id"], w["variant"]): w for w in read("synthetic_ablation_per_cell.csv")}
    for v in VARIANTS:
        for cid in CONFIGS:
            rows = [vals[k] for k in vals if k[0] == v and k[1] == cid]
            w = cells.get((cid, v))
            checks += 1
            if w is None:
                fails["csv"].append(f"per_cell: no row {cid} {v}")
                continue
            for k in RATES:
                a, n = rate(rows, k)
                checks += 1
                if not same(w[k], f"{a}/{n}" if n else ""):
                    fails["csv"].append(f"per_cell {cid} {v}: {k} {w[k]!r} vs {a}/{n}")
            for k in MEANS:
                checks += 1
                if not same(w[f"mean_{k}"], avg(rows, k)):
                    fails["csv"].append(f"per_cell {cid} {v}: mean_{k} {w[f'mean_{k}']!r} vs {avg(rows, k)!r}")

    # per variant
    labels = {"scenario_produced": "Scenario produced", "valid": "Valid", "executable": "Executable",
              "merge_stability_positive": "Merge stability > 0", "all_kpis_improved": "All KPIs improved",
              "DHR": "DHR", "NIS": "NIS", "util_target_achieved": "Utilization target achieved (where applicable)",
              "admissible": "Admissible (valid, executable, merge stability > 0)",
              "admissible_and_all_improved": "Admissible and all KPIs improved",
              "any_kpi_significant_improvement": "At least one KPI improved with p < 0.05",
              "loop_discarded_better_initial": "Loop discarded a better initial",
              "no_improving_scenario": "Loop kept the baseline (no_improving_scenario)", "recall": "Recall",
              "CTD_ratio": "CTD ratio to healthy", "merge_stability": "Merge stability (applied / proposed)",
              "glass_merge_stability": "Merge stability, GLASS's value", "n_modifications": "Modifications",
              "iterations_used": "Iterations used", "elapsed_s": "Run time (s)"}
    pv = {w["variant"]: w for w in read("synthetic_ablation_per_variant.csv")}
    by = {v: {(k[1], k[2]): vals[k] for k in vals if k[0] == v} for v in VARIANTS}
    for v in VARIANTS:
        w, rows = pv.get(v), list(by[v].values())
        checks += 1
        if w is None:
            fails["csv"].append(f"per_variant: no row {v}")
            continue
        for k, lbl in labels.items():
            checks += 2
            if k in RATES:
                a, n = rate(rows, k)
                want = f"{a}/{n} ({a / n:.1%})" if n else ""
                if v != "V_main":
                    ar, nr = rate(list(by["V_main"].values()), k)
                    d = f"{(ar / nr - a / n) * 100:+.1f} pp" if n and nr else ""
                    p = round(fisher_p(ar, nr, a, n), 4) if n and nr else ""
            else:
                want = avg(rows, k)
                if v != "V_main":
                    pairs = [(by["V_main"][x][k], r[k]) for x, r in by[v].items()
                             if x in by["V_main"] and r[k] not in ("", None) and by["V_main"][x][k] not in ("", None)]
                    diffs = [a_ - b_ for a_, b_ in pairs]
                    nz = [x for x in diffs if x != 0]
                    d = round(mean(diffs), 4) if diffs else ""
                    p = round(binom_p(sum(x > 0 for x in nz), len(nz)), 4) if nz else ""
            if not same(w[lbl], want):
                fails["csv"].append(f"per_variant {v}: {lbl} {w[lbl]!r} vs {want!r}")
            if v != "V_main":
                if not same(w[f"{lbl}: V_main minus variant"], d) or not same(w[f"{lbl}: p"], p):
                    fails["csv"].append(f"per_variant {v}: {lbl} difference/p {w[f'{lbl}: V_main minus variant']!r} "
                                        f"{w[f'{lbl}: p']!r} vs {d!r} {p!r}")

    if not args.skip_v32 and not V32_VERIFIER.exists():
        print(f"verify_stage2_results.py not found ({V32_VERIFIER.name} is not part of this release): check 3 skipped")
        args.skip_v32 = True
    if not args.skip_v32:
        for v in VARIANTS:
            res = subprocess.run([sys.executable, "-c", V32_RUNNER, str(V32_VERIFIER),
                                  "--results", str(out / v), "--only", ",".join(CONFIGS)],
                                 capture_output=True, text=True, cwd=REPO)
            checks += 2
            # the v3.2 verifier is report-only: a section with problems reads "- **<title>:** N issue(s)"
            issues = [ln.strip() for ln in res.stdout.splitlines() if re.search(r"\*\*:?\s*\d+ issue", ln)]
            m = re.search(r"EMPTY_PATCH_REMERGES_SKIPPED (\d+)", res.stdout)
            skipped = int(m.group(1)) if m else None
            if res.returncode != 0 or issues:
                tail = res.stderr.strip().splitlines()[-1:] if res.returncode else []
                fails["v32"].append(f"{v}: verify_stage2_results exit {res.returncode}; {'; '.join(issues + tail)}")
            if skipped != expected_skips[v]:
                fails["v32"].append(f"{v}: {skipped} empty-patch re-merges skipped, expected {expected_skips[v]}")
            print(f"verify_stage2_results {v}: exit {res.returncode}; "
                  f"{'; '.join(issues) if issues else 'every section OK'}; "
                  f"empty-patch re-merges skipped: {skipped}")

    print(f"Finding: {len(findings)} iteration(s) with an empty patch accepted by GLASS")
    for f in findings:
        print(f"  {f}")
    n = sum(len(f) for f in fails.values())
    print(f"{checks} checks, {n} failures")
    for k, fs in fails.items():
        for f in fs[:30]:
            print(f"  FAIL [{k}] {f}")
    sys.exit(1 if n else 0)


if __name__ == "__main__":
    main()
