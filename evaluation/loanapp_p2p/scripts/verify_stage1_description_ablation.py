"""
Independent check of results/stage1_description_ablation/: rebuilds every CSV from the raw records with its own
implementation of the scoring rules (written from the rules in run_stage1_description_ablation.py's and
run_stage1_batch.py's headers; neither script's scoring code is imported) and compares them cell by cell. Also
checks the raw records: one per (variant, configuration, rep), the description each variant passed and what the
prompt contains, and one value of model, temperature, GLASS commit, app.py and script SHA-256 for all runs.

Usage (repo root): python evaluation/loanapp_p2p/scripts/verify_stage1_description_ablation.py [--dir DIR]
Exit code 0 if every check passes.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import sys
from collections import Counter
from math import comb
from pathlib import Path
from statistics import mean

BASE = Path(__file__).resolve().parent.parent
CONFIG = json.loads((BASE / "setup" / "stage1_config.json").read_text(encoding="utf-8"))
VARIANTS = ("with_description", "no_description")


def norm(t) -> str:
    return str(t or "").lower().replace("\\'", "'").replace("’", "'").replace("_", " ")


def lenient(expected: str, k: dict) -> bool:
    name, text = norm(k.get("name")), norm(k.get("name")) + "\n" + norm(k.get("suggested_formula"))
    if expected == "Average Cycle Time":
        return k.get("process_scope") == "end_to_end" and k.get("category") == "time" and "cycle time" in text
    if expected.endswith(" Waiting Time"):
        return (k.get("process_scope") == "activity_level" and norm(expected[:-13]) in text and "wait" in text)
    if expected == "Resource Utilization":
        return k.get("category") == "utilization" or "utilization" in name
    return norm(k.get("measurable_as")) == norm(expected)


def run_scores(rec: dict, expected: list[str]) -> dict:
    answered = rec["status"] in ("ok", "out_of_scope")
    kpis = (rec.get("result") or {}).get("kpis") or []
    got = {k["measurable_as"].lower() for k in kpis if k.get("measurable_as")}
    exp = {e.lower() for e in expected}
    strict = [e for e in expected if e.lower() in got]
    len_found = [e for e in expected if any(lenient(e, k) for k in kpis)]
    return {
        "strict_n": len(strict) if answered else 0, "lenient_n": len(len_found) if answered else 0,
        "strict_all": answered and len(strict) == len(expected), "lenient_all": answered and len(len_found) == len(expected),
        "strict_extra": sum(1 for k in kpis if k.get("measurable_as") and k["measurable_as"].lower() not in exp),
        "lenient_extra": sum(1 for k in kpis if not any(lenient(e, k) for e in expected)),
        "n": len(kpis), "attempts": len(rec.get("attempts") or []),
    }


def key(k: dict) -> str:
    return (k.get("measurable_as") or "").lower() or f"({k.get('category')}, {k.get('process_scope')})"


def mean_pairs(items: list, f) -> float | None:
    pairs = list(itertools.combinations(items, 2))
    return round(mean(f(a, b) for a, b in pairs), 4) if pairs else None


def jac(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


def mjac(a: Counter, b: Counter) -> float:
    keys = set(a) | set(b)
    den = sum(max(a[x], b[x]) for x in keys)
    return sum(min(a[x], b[x]) for x in keys) / den if den else 1.0


def sign_p(diffs: list[float]) -> tuple[int, int, float]:
    d = [x for x in diffs if x != 0]
    n, k = len(d), sum(x > 0 for x in d)
    if not n:
        return 0, 0, 1.0
    return n, k, min(1.0, 2 * sum(comb(n, i) for i in range(min(k, n - k) + 1)) / 2 ** n)


def fisher_p(a, na, b, nb) -> float:
    from scipy.stats import fisher_exact
    return float(fisher_exact([[a, na - a], [b, nb - b]])[1])


def close(x: str, y) -> bool:
    if y is None:
        return x == ""
    if isinstance(y, bool):
        return x == str(y)
    if isinstance(y, (int, float)):
        try:
            return abs(float(x) - float(y)) <= 1e-4
        except ValueError:
            return False
    return x == str(y)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=BASE / "results" / "stage1_description_ablation")
    d = ap.parse_args().dir
    fails, checks = [], 0
    configs, reps = CONFIG["configurations"], CONFIG["settings"]["repetitions"]
    recs = {}
    for v in VARIANTS:
        for c in configs:
            for r in range(1, reps + 1):
                p = d / "raw" / v / c["id"] / f"rep_{r}.json"
                checks += 1
                if not p.exists():
                    fails.append(f"missing {p.relative_to(d)}")
                    continue
                rec = recs[(v, c["id"], r)] = json.loads(p.read_text(encoding="utf-8"))
                want = CONFIG["process_descriptions"][c["process"]] if v == "with_description" else ""
                checks += 4
                if (rec["variant"], rec["config_id"], rec["rep"]) != (v, c["id"], r):
                    fails.append(f"{p.relative_to(d)}: header {rec['variant']} {rec['config_id']} {rec['rep']}")
                if rec["process_description_passed"] != want:
                    fails.append(f"{p.relative_to(d)}: description passed differs from the variant's")
                block = f'Process Description:\n"""{want}"""\n\n'
                if rec["description_block_in_prompt"] != block or block not in rec["user_prompt"]:
                    fails.append(f"{p.relative_to(d)}: prompt's description block is not the variant's")
                if rec["status"] not in ("ok", "parse_error", "out_of_scope"):
                    fails.append(f"{p.relative_to(d)}: status {rec['status']}")
    extra = [p for p in (d / "raw").glob("*/*/rep_*.json")
             if (p.parent.parent.name, p.parent.name, int(p.stem.split("_")[1])) not in recs]
    fails += [f"unexpected file {p.relative_to(d)}" for p in extra]
    for field in ("model", "temperature", "json_mode", "num_kpis", "use_log_evidence", "glass_commit",
                  "app_py_sha256", "script_sha256", "step1_batch_script_sha256", "glass_patch_sha256"):
        checks += 1
        vals = {json.dumps(r.get(field)) for r in recs.values()}
        if len(vals) != 1:
            fails.append(f"{field}: {sorted(vals)}")
    checks += 1
    if {r["temperature"] for r in recs.values()} != {CONFIG["settings"]["temperature"]}:
        fails.append("temperature differs from stage1_config.json")

    # rebuild per-config and per-variant values
    per = {}
    for (v, cid, r), rec in recs.items():
        per.setdefault((v, cid), []).append(rec)
    exp_of = {c["id"]: c["expected_measurable_as"] for c in configs}
    pc = {}
    for (v, cid), rs in per.items():
        sc = [run_scores(x, exp_of[cid]) for x in rs]
        ok = [s for s, x in zip(sc, rs) if x["status"] == "ok"]
        res = [x["result"]["kpis"] for x in rs if x.get("result")]
        pc[(v, cid)] = {
            "reps": len(rs), "reps_ok": len(ok),
            "strict_expected_identified": sum(s["strict_n"] for s in sc),
            "lenient_expected_identified": sum(s["lenient_n"] for s in sc),
            "strict_expected_instances": len(rs) * len(exp_of[cid]),
            "lenient_expected_instances": len(rs) * len(exp_of[cid]),
            "strict_reps_all_expected_found": sum(s["strict_all"] for s in sc),
            "lenient_reps_all_expected_found": sum(s["lenient_all"] for s in sc),
            "strict_mean_n_extra": round(mean(s["strict_extra"] for s in ok), 2) if ok else None,
            "lenient_mean_n_extra": round(mean(s["lenient_extra"] for s in ok), 2) if ok else None,
            "mean_n_returned": round(mean(s["n"] for s in ok), 2) if ok else None,
            "mean_attempts": round(mean(s["attempts"] for s in sc), 2),
            "kpi_set_jaccard": mean_pairs([{key(k) for k in ks} for ks in res], jac),
            "category_stability": mean_pairs([Counter(str(k.get("category")) for k in ks) for ks in res], mjac),
            "_scores": sc, "_ok": ok,
        }

    def compare(fname: str, rows: list[dict], match) -> None:
        nonlocal checks
        path = d / fname
        if not path.exists():
            fails.append(f"missing {fname}")
            return
        written = list(csv.DictReader(open(path, encoding="utf-8")))
        checks += 1
        if len(written) != len(rows):
            fails.append(f"{fname}: {len(written)} rows, rebuilt {len(rows)}")
            return
        for want in rows:
            got = next((w for w in written if match(w, want)), None)
            if got is None:
                fails.append(f"{fname}: no row for {[want.get(k) for k in ('variant', 'config_id', 'measure') if k in want]}")
                continue
            for k, val in want.items():
                checks += 1
                if k not in got or not close(got[k], val):
                    fails.append(f"{fname} {[want.get(x) for x in ('variant', 'config_id', 'measure') if x in want]}: "
                                 f"{k} written {got.get(k)!r}, rebuilt {val!r}")

    # per run
    run_rows = []
    for (v, cid, r), rec in sorted(recs.items()):
        s = run_scores(rec, exp_of[cid])
        run_rows.append({"variant": v, "config_id": cid, "rep": r, "status": rec["status"],
                         "strict_n_expected_found": s["strict_n"], "lenient_n_expected_found": s["lenient_n"],
                         "strict_all_expected_found": s["strict_all"], "lenient_all_expected_found": s["lenient_all"],
                         "strict_n_extra": s["strict_extra"], "lenient_n_extra": s["lenient_extra"],
                         "n_returned": s["n"], "attempts": s["attempts"]})
    compare("description_ablation_runs.csv", run_rows,
            lambda w, x: (w["variant"], w["config_id"], int(w["rep"])) == (x["variant"], x["config_id"], x["rep"]))
    cfg_rows = [{"variant": v, "config_id": cid, **{k: val for k, val in p.items() if not k.startswith("_")}}
                for (v, cid), p in pc.items()]
    compare("description_ablation_per_config.csv", cfg_rows,
            lambda w, x: (w["variant"], w["config_id"]) == (x["variant"], x["config_id"]))

    counts = [(f"{m}_{w}", m, w) for m in ("strict", "lenient") for w in ("reps_all_expected_found", "expected_identified")]
    cmp_rows = []
    for c in configs:
        a, b = pc.get(("with_description", c["id"])), pc.get(("no_description", c["id"]))
        if not a or not b:
            continue
        row = {"config_id": c["id"]}
        for k, m, w in counts:
            na, nb = (a["reps"], b["reps"]) if w.startswith("reps") else (a[f"{m}_expected_instances"], b[f"{m}_expected_instances"])
            row[f"{k}_with"], row[f"{k}_without"] = f"{a[k]}/{na}", f"{b[k]}/{nb}"
            row[f"{k}_fisher_p"] = round(fisher_p(a[k], na, b[k], nb), 4)
        for k in ("strict_mean_n_extra", "lenient_mean_n_extra", "mean_n_returned", "kpi_set_jaccard", "category_stability"):
            row[f"{k}_with"], row[f"{k}_without"] = a[k], b[k]
        cmp_rows.append(row)
    compare("description_ablation_per_config_comparison.csv", cmp_rows, lambda w, x: w["config_id"] == x["config_id"])

    var_rows, test_rows = [], []
    for v in VARIANTS:
        ps = [p for (vv, _), p in pc.items() if vv == v]
        sc = [s for p in ps for s in p["_scores"]]
        ok = [s for p in ps for s in p["_ok"]]
        vrecs = [x for (vv, _, _), x in recs.items() if vv == v]
        row = {"variant": v, "configurations": len(ps), "runs": len(sc), "runs_ok": len(ok),
               "runs_parse_error": sum(x["status"] == "parse_error" for x in vrecs),
               "runs_scope_gate_passed": sum(bool(x["scope_gate_passed"]) for x in vrecs)}
        for m in ("strict", "lenient"):
            row[f"{m}_expected_identified"] = f"{sum(p[f'{m}_expected_identified'] for p in ps)}/{sum(p[f'{m}_expected_instances'] for p in ps)}"
            row[f"{m}_runs_all_expected_found"] = f"{sum(p[f'{m}_reps_all_expected_found'] for p in ps)}/{len(sc)}"
            row[f"{m}_mean_n_extra"] = round(mean(s[f"{m}_extra"] for s in ok), 2)
        row["mean_n_returned"] = round(mean(s["n"] for s in ok), 2)
        row["mean_attempts"] = round(mean(s["attempts"] for s in sc), 2)
        for k in ("kpi_set_jaccard", "category_stability"):
            vals = [p[k] for p in ps if p[k] is not None]
            row[f"{k}_mean_over_configs"] = round(mean(vals), 4) if vals else None
        var_rows.append(row)
    compare("description_ablation_per_variant.csv", var_rows, lambda w, x: w["variant"] == x["variant"])

    ids = [c["id"] for c in configs if all((v, c["id"]) in pc for v in VARIANTS)]
    A, B = [pc[("with_description", c)] for c in ids], [pc[("no_description", c)] for c in ids]
    for k, m, w in counts:
        na = sum(p["reps"] if w.startswith("reps") else p[f"{m}_expected_instances"] for p in A)
        nb = sum(p["reps"] if w.startswith("reps") else p[f"{m}_expected_instances"] for p in B)
        a, b = sum(p[k] for p in A), sum(p[k] for p in B)
        test_rows.append({"test": "Fisher exact, pooled", "measure": k, "with_description": f"{a}/{na}",
                          "no_description": f"{b}/{nb}", "p": round(fisher_p(a, na, b, nb), 4)})
    for k in ([f"{m}_reps_all_expected_found" for m in ("strict", "lenient")]
              + [f"{m}_expected_identified" for m in ("strict", "lenient")]
              + [f"{m}_mean_n_extra" for m in ("strict", "lenient")]
              + ["mean_n_returned", "kpi_set_jaccard", "category_stability"]):
        n, kk, p = sign_p([x[k] - y[k] for x, y in zip(A, B) if x[k] is not None and y[k] is not None])
        test_rows.append({"test": "sign test over configurations", "measure": k, "n": n, "with_greater": kk,
                          "p": round(p, 4)})
    compare("description_ablation_tests.csv", test_rows, lambda w, x: (w["test"], w["measure"]) == (x["test"], x["measure"]))

    print(f"{checks} checks, {len(fails)} failures")
    for f in fails[:50]:
        print("  FAIL", f)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
