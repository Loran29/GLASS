"""
Independent verification of the Stage-1 batch results (results/stage1_results/).

Re-derives everything it can from the raw JSON files with its own code, and only reports:
it never edits or reruns results. Writes <results folder>/verification_report.md.

The GLASS code a folder was produced with is rebuilt from the base commit and the folder's
glass_local_fixes.patch (both recorded in the folder), so a folder can be verified after later
GLASS changes were committed; the working tree is not required to equal that code.

Checks
  1. files     : 14 configs x reps 1..N raw files exist; status ok|parse_error; one model string,
                 one temperature (= config), one script SHA-256 across all files.
  2b. patch    : glass_local_fixes.patch applies cleanly to the recorded commit; patch SHA-256 in
                 every raw file and in README.md (whether HEAD contains that code is reported).
  2. app code  : app.py rebuilt from the recorded commit + patch has the app.py SHA-256 recorded in
                 the raw files (LF or CRLF checkout); the batch's loader, applied to that app.py,
                 yields every Stage-1 function with inspect.getsource() == its text (sliced by AST
                 line range), the per-function SHA-256 in README.md and identical bytecode.
  3. fill      : for every KPI, measurable_as == fill(measurable_as_raw), re-running
                 _fill_missing_activity_measurable_as extracted here from app.py; result_parsed
                 == parse_kpi_generation_payload(raw).
  4. attempts  : retry prompts are exactly parse_with_retries' prompts built from the previous
                 recorded answer; first-attempt parse errors re-derived by re-parsing.
  5. logs      : every rep names the config's event log, stores its current SHA-256, and its user
                 prompt contains the log evidence rebuilt from that file.
  6. summary   : stage1_summary.csv rebuilt from raw files with a fresh implementation of both
                 scoring rule sets and diffed cell by cell.

Normalised folder (records carry a "normalisation" block, written by reprocess_stage1_normalised.py):
  1b. source   : every record equals its --source record (raw, attempts, result_parsed, prompts, run
                 metadata) except the re-derived fields, and stores the source file's SHA-256; the
                 delivered KPIs differ from the source run in measurable_as / measurable_as_raw only and
                 the semantic validation issues differ only by measurable_as warnings.
  2 / 2b       : app.py is compared with app.py of the recorded commit + the folder's patch; the
                 records' normalisation.glass_patch_sha256 must equal the patch file, and every file diff
                 of the source run's patch must appear unchanged in it.
  3. fill      : measurable_as == fill(normalise(measurable_as_raw)), re-running
                 _normalise_result_measurable_as and _fill_missing_activity_measurable_as extracted from
                 that app.py with the log profile rebuilt from the event log; measurable_as_raw on each
                 delivered KPI == result_parsed's measurable_as; warning counts match.
  6. summary   : adds n_measurable_as_normalised = KPIs changed from measurable_as_raw minus fills.

Usage (repo root):  python evaluation/loanapp_p2p/scripts/verify_stage1_results.py [--reps 5] [--results DIR] [--source DIR]
"""

from __future__ import annotations

import __future__
import argparse
import ast
import csv
import hashlib
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent.parent          # evaluation/loanapp_p2p
REPO = BASE.parent.parent
RESULTS = BASE / "results" / "stage1_results"
LOGS = BASE / "input_logs"
CONFIG = BASE / "setup" / "stage1_config.json"
APP = REPO / "goal_to_parameters" / "app.py"
sys.path.insert(0, str(REPO / "goal_to_parameters"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from models import KPIGenerationResult  # noqa: E402
from utils import (KPIParsingError, build_context_evidence_prompt, build_log_evidence_prompt,  # noqa: E402
                   parse_kpi_generation_payload, profile_event_log)
from utils.parsing import extract_json_object, strip_code_fences  # noqa: E402

EVIDENCE_BASIS_LABELS = {"derived_from_log", "approximated"}   # metric status labels the patch maps
MEASURABLE_AS_CODES = {"inferred_measurable_as", "measurable_as_normalised"}
# Fields a normalised record re-derives; everything else must equal the source record.
REDERIVED = {"result", "measurable_as_trace", "fill_warnings", "normalisation_warnings", "semantic_validation",
             "normalisation"}


def raw_kpis(raw: str) -> list[dict]:
    """KPI dicts exactly as the model wrote them (before any GLASS normalisation)."""
    text = strip_code_fences(raw)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = json.loads(extract_json_object(text))
    return payload.get("kpis") or []


class Report:
    def __init__(self):
        self.sections: list[tuple[str, int, list[str]]] = []

    def add(self, title: str, checked: int, failures: list[str]) -> None:
        self.sections.append((title, checked, failures))
        print(f"[{'PASS' if not failures else 'FAIL'}] {title}: {checked} checks, {len(failures)} failures")
        for f in failures[:15]:
            print(f"       - {f}")

    @property
    def passed(self) -> bool:
        return all(not f for _, _, f in self.sections)


def digest(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def committed_app_source(commit: str) -> str:
    out = subprocess.run(["git", "show", f"{commit}:goal_to_parameters/app.py"], cwd=REPO,
                         capture_output=True, check=True)
    return out.stdout.decode("utf-8").replace("\r\n", "\n")


def patched_app_source(commit: str, patch_file: Path) -> str:
    """app.py of `commit` with `patch_file` applied, built in a throw-away git index."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        run = lambda *a: subprocess.run(["git", *a], cwd=REPO, env=env, capture_output=True, check=True).stdout
        run("read-tree", commit)
        run("apply", "--cached", str(patch_file))
        return run("show", ":goal_to_parameters/app.py").decode("utf-8").replace("\r\n", "\n")


def file_diffs(patch: bytes) -> list[bytes]:
    """A git patch split into its per-file sections."""
    return [b"diff --git" + part for part in patch.split(b"diff --git")[1:]]


def top_level_text(src: str) -> dict[str, str]:
    """name -> exact source text of each top-level def/assignment, sliced by AST line range."""
    lines, texts = src.splitlines(), {}
    for node in ast.parse(src).body:
        names = [node.name] if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else \
            [t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)]
        for name in names:
            texts[name] = "\n".join(lines[node.lineno - 1: node.end_lineno])
    return texts


def needed_definitions(src: str, roots: list[str]) -> str:
    """Source text of `roots` and every top-level definition they reference, in file order."""
    nodes: dict[str, ast.AST] = {}
    for node in ast.parse(src).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            nodes[node.name] = node
        elif isinstance(node, ast.Assign):
            nodes.update({t.id: node for t in node.targets if isinstance(t, ast.Name)})
    needed, stack = set(), list(roots)
    while stack:
        name = stack.pop()
        if name in nodes and name not in needed:
            needed.add(name)
            stack += [n.id for n in ast.walk(nodes[name]) if isinstance(n, ast.Name)]
    lines = src.splitlines()
    ordered = sorted({id(nodes[n]): nodes[n] for n in needed}.values(), key=lambda n: n.lineno)
    return "\n\n".join("\n".join(lines[n.lineno - 1: n.end_lineno]) for n in ordered)


# --------------------------------------------------------- fresh scoring ---

def _n(s: Any) -> str:
    s = "" if s is None else str(s).lower()
    for a, b in (("\\'", "'"), ("â€™", "'"), ("_", " ")):
        s = s.replace(a, b)
    return s


def lenient_rule(expected: str, k: dict) -> bool:
    blob = _n(k.get("name")) + "\n" + _n(k.get("suggested_formula"))
    if expected == "Average Cycle Time":
        return (k.get("process_scope"), k.get("category")) == ("end_to_end", "time") and "cycle time" in blob
    if expected == "Resource Utilization":
        return k.get("category") == "utilization" or "utilization" in _n(k.get("name"))
    if m := re.fullmatch(r"(.+) Waiting Time", expected):
        return k.get("process_scope") == "activity_level" and _n(m.group(1)) in blob and "wait" in blob
    raise ValueError(f"no lenient rule for {expected!r}")


def fresh_row(rec: dict, cfg: dict, normalised: bool = False) -> dict[str, str]:
    kpis = rec["result"]["kpis"] if rec.get("result") else []
    exp = cfg["expected_measurable_as"]
    got = [k["measurable_as"] for k in kpis if k.get("measurable_as")]
    s_found = [e for e in exp if any(e.lower() == g.lower() for g in got)]
    s_extra = [g for g in got if all(g.lower() != e.lower() for e in exp)]
    l_found = [e for e in exp if any(lenient_rule(e, k) for k in kpis)]
    l_extra = [k["name"] for k in kpis if not any(lenient_rule(e, k) for e in exp)]
    delivered = rec["status"] in ("ok", "out_of_scope")
    att = rec.get("attempts") or []
    first_parse_err = first_crash = None
    if att:
        try:
            parse_kpi_generation_payload(att[0]["raw"])
        except KPIParsingError as exc:
            first_parse_err = str(exc)
        except Exception as exc:   # e.g. valid JSON that is not an object
            first_crash = f"{type(exc).__name__}: {exc}"
    first_sem = bool(att) and first_parse_err is None and first_crash is None and len(att) > 1
    row = {
        "config_id": cfg["id"], "process": cfg["process"], "goal_config": cfg["goal_config"], "rep": rec["rep"],
        "status": rec["status"], "scope_gate_passed": rec.get("scope_gate_passed"), "attempts": len(att),
        "first_attempt_parse_error": first_parse_err is not None, "first_attempt_semantic_error": first_sem,
        "first_attempt_error": first_parse_err or first_crash or (att[0]["error"] if first_sem else ""),
        "expected_measurable_as": "|".join(exp),
        "returned_kpis": ";".join("|".join([k["name"], k.get("measurable_as") or "", k["target_direction"],
                                            k["process_scope"]]) for k in kpis),
        "n_returned": len(kpis), "n_null_measurable_as": len([k for k in kpis if not k.get("measurable_as")]),
        "n_filled_by_app": len(rec.get("fill_warnings") or []),
        **({"n_measurable_as_normalised": len([k for k in kpis if k.get("measurable_as") != k.get("measurable_as_raw")])
            - len(rec.get("fill_warnings") or [])} if normalised else {}),
        "n_evidence_basis_normalised": len([k for k in raw_kpis(rec["raw"]) if isinstance(k.get("evidence_basis"), str)
                                            and re.sub(r"[\s-]", "_", k["evidence_basis"].strip().lower())
                                            in EVIDENCE_BASIS_LABELS]) if delivered else 0,
        "n_segmentation_dropped": len([i for i in (rec.get("semantic_validation") or {}).get("issues", [])
                                       if i.get("code") == "unsupported_context_condition_dropped"]),
        "strict_found": "|".join(s_found), "strict_missing": "|".join(e for e in exp if e not in s_found),
        "strict_extra": "|".join(s_extra), "strict_all_expected_found": delivered and s_found == exp,
        "strict_n_extra": len(s_extra),
        "utilization_direction": "|".join(k["target_direction"] for k in kpis
                                          if (k.get("measurable_as") or "").lower() == "resource utilization"),
        "lenient_found": "|".join(l_found), "lenient_missing": "|".join(e for e in exp if e not in l_found),
        "lenient_extra": "|".join(l_extra), "lenient_all_expected_found": delivered and l_found == exp,
        "lenient_n_extra": len(l_extra),
        "lenient_utilization_direction": "|".join(k["target_direction"] for k in kpis
                                                  if lenient_rule("Resource Utilization", k)),
        "goal_structured": rec["result"]["simulation_goal_structured"] if rec.get("result") else "",
    }
    return {k: "" if v is None else str(v) for k, v in row.items()}


# ------------------------------------------------------------------ main ---

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=None)
    ap.add_argument("--results", type=Path, default=RESULTS)
    ap.add_argument("--source", type=Path, default=RESULTS,
                    help="for a normalised folder: the folder whose LLM outputs it re-processed")
    args = ap.parse_args()
    results_dir = args.results
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    configs, reps = config["configurations"], args.reps or config["settings"]["repetitions"]
    report = Report()

    # 1. files ------------------------------------------------------------
    records, fails = {}, []
    for cfg in configs:
        for rep in range(1, reps + 1):
            path = results_dir / "raw" / cfg["id"] / f"rep_{rep}.json"
            if not path.exists():
                fails.append(f"missing raw/{cfg['id']}/{path.name}")
                continue
            rec = records[(cfg["id"], rep)] = json.loads(path.read_text(encoding="utf-8"))
            if rec["status"] not in ("ok", "parse_error"):
                fails.append(f"{cfg['id']} rep {rep}: status {rec['status']}")
            if rec["rep"] != rep or rec["config_id"] != cfg["id"]:
                fails.append(f"{path.name}: header says {rec['config_id']} rep {rec['rep']}")
    extra_files = [p for p in (results_dir / "raw").glob("*/rep_*.json")
                   if (p.parent.name, int(p.stem.split("_")[1])) not in records]
    fails += [f"unexpected file raw/{p.parent.name}/{p.name}" for p in extra_files]
    for field, want in (("model", None), ("temperature", config["settings"]["temperature"]),
                        ("script_sha256", None), ("app_py_sha256", None), ("json_mode", True),
                        ("glass_patch_sha256", None)):
        values = {str(r.get(field)) for r in records.values()}
        if len(values) != 1 or (want is not None and values != {str(want)}):
            fails.append(f"{field} values {sorted(values)} (expected one value{'' if want is None else f' = {want}'})")
    statuses = {s: sum(r['status'] == s for r in records.values()) for s in {r['status'] for r in records.values()}}
    report.add(f"1. raw files ({len(records)}/{len(configs) * reps}; statuses {statuses})",
               len(configs) * reps + 5, fails)
    normalised = all("normalisation" in r for r in records.values())

    # 1b. normalised folder: same LLM outputs as the source folder ----------------
    if normalised:
        fails, checked = [], 0
        for (cid, rep), rec in records.items():
            src_path = args.source / "raw" / cid / f"rep_{rep}.json"
            checked += 1
            if not src_path.exists():
                fails.append(f"{cid} rep {rep}: no source record {src_path}")
                continue
            src_bytes = src_path.read_bytes()
            src = json.loads(src_bytes)
            if rec["normalisation"].get("source_file_sha256") != digest(src_bytes):
                fails.append(f"{cid} rep {rep}: normalisation.source_file_sha256 != SHA-256 of {src_path.name}")
            differing = sorted(k for k in set(rec) | set(src) if k not in REDERIVED and rec.get(k) != src.get(k))
            if differing:
                fails.append(f"{cid} rep {rep}: fields differ from the source record: {differing}")
            if bool(rec.get("result")) != bool(src.get("result")):
                fails.append(f"{cid} rep {rep}: result present in only one of normalised / source")
            elif src.get("result"):
                checked += 2
                strip = lambda kpis: [{k: v for k, v in kpi.items() if k not in ("measurable_as", "measurable_as_raw")}
                                      for kpi in kpis]
                if strip(rec["result"]["kpis"]) != strip(src["result"]["kpis"]) or \
                        {k: v for k, v in rec["result"].items() if k != "kpis"} != \
                        {k: v for k, v in src["result"].items() if k != "kpis"}:
                    fails.append(f"{cid} rep {rep}: delivered result differs from the source beyond measurable_as")
                other = lambda v: ([i for i in v["issues"] if i.get("code") not in MEASURABLE_AS_CODES], v.get("has_errors"))
                if other(rec["semantic_validation"]) != other(src["semantic_validation"]):
                    fails.append(f"{cid} rep {rep}: semantic validation differs from the source beyond measurable_as warnings")
        report.add(f"1b. same LLM outputs as {args.source.name} (only measurable_as re-derived)", checked, fails)

    # 2. app code -------------------------------------------------------------
    fails, checked = [], 0
    readme = (results_dir / "README.md").read_text(encoding="utf-8")
    commit = re.search(r"Repository commit: `([0-9a-f]{40})`", readme).group(1)
    patch_file = results_dir / "glass_local_fixes.patch"
    reference = patched_app_source(commit, patch_file) if patch_file.exists() else committed_app_source(commit)
    ref_label = f"app.py@{commit[:7]}" + (" + glass_local_fixes.patch" if patch_file.exists() else "")
    first = next(iter(records.values()))
    rec_app_sha = first["normalisation"]["app_py_sha256"] if normalised else first["app_py_sha256"]
    checked += 1
    if rec_app_sha not in (digest(reference), digest(reference.replace("\n", "\r\n"))):
        fails.append(f"app.py SHA-256 recorded in the raw files != {ref_label} (LF or CRLF)")
    import run_stage1_batch as batch  # only to check what the batch executes, not to score
    with tempfile.TemporaryDirectory() as tmp:
        rebuilt = Path(tmp) / "app.py"
        rebuilt.write_text(reference, encoding="utf-8", newline="\n")
        live_app, batch.APP_PATH = batch.APP_PATH, rebuilt   # the batch's loader, applied to the rebuilt app.py
        try:
            namespace, sources = batch.load_app_stage1()
        finally:
            batch.APP_PATH = live_app
        texts = top_level_text(reference)
        compiled = compile(reference, str(rebuilt), "exec", flags=__future__.annotations.compiler_flag, dont_inherit=True)
        committed_code = {c.co_name: c for c in compiled.co_consts if hasattr(c, "co_code")}
        for name, text in sources.items():
            checked += 2
            if text != texts.get(name):
                fails.append(f"{name}: executed source differs from {ref_label}")
            if f"`{name}`: `{digest(texts.get(name, ''))}`" not in readme:
                fails.append(f"{name}: SHA-256 in README.md does not match {ref_label}")
            obj = namespace[name]
            if inspect.isfunction(obj):
                checked += 2
                if inspect.getsource(obj).rstrip("\n") != texts[name]:
                    fails.append(f"{name}: inspect.getsource differs from app.py text")
                if obj.__code__ != committed_code[name]:   # compares bytecode, consts, names, line table
                    fails.append(f"{name}: bytecode differs from function compiled from {ref_label}")
    report.add(f"2. app.py Stage-1 code identical ({len(sources)} definitions, {ref_label})", checked, fails)

    # 2b. GLASS local patch -----------------------------------------------------
    fails, checked = [], 3
    recorded = {(r["normalisation"] if normalised else r).get("glass_patch_sha256") for r in records.values()}
    in_head = ""
    if not patch_file.exists():
        if recorded != {None}:   # a run without local GLASS changes records no patch
            fails.append(f"glass_local_fixes.patch missing, but raw files record patch SHA-256 {sorted(map(str, recorded))}")
        head_diff = subprocess.run(["git", "diff", commit, "HEAD", "--", "goal_to_parameters"], cwd=REPO,
                                   capture_output=True, check=True).stdout
        in_head = " (no local patch); HEAD contains this code" if not head_diff else " (no local patch); HEAD has later GLASS changes"
    else:
        patch = patch_file.read_bytes()
        patch_sha = digest(patch)
        if recorded != {patch_sha}:
            fails.append(f"raw files record patch SHA-256 {sorted(map(str, recorded))}, patch file is {patch_sha}")
        if patch_sha not in readme or commit not in readme:
            fails.append("README.md does not state the base commit and the patch SHA-256")
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
            subprocess.run(["git", "read-tree", commit], cwd=REPO, env=env, check=True, capture_output=True)
            check = subprocess.run(["git", "apply", "--cached", "--check", str(patch_file)], cwd=REPO, env=env,
                                   capture_output=True)
        if check.returncode != 0:
            fails.append(f"patch does not apply cleanly to {commit[:7]}: {check.stderr.decode().strip()}")
        head_diff = subprocess.run(["git", "diff", commit, "HEAD", "--", "goal_to_parameters"], cwd=REPO,
                                   capture_output=True, check=True).stdout
        in_head = "; HEAD contains this code" if head_diff == patch else "; HEAD has later GLASS changes"
        if normalised:
            checked += 2
            source_patch = (args.source / "glass_local_fixes.patch").read_bytes()
            if {r.get("glass_patch_sha256") for r in records.values()} != {digest(source_patch)}:
                fails.append("records' glass_patch_sha256 (LLM run) != SHA-256 of the source folder's patch")
            missing = [d.split(b"\n", 1)[0].decode() for d in file_diffs(source_patch) if d not in file_diffs(patch)]
            if missing:
                fails.append(f"source run's patch sections not contained unchanged in this patch: {missing}")
    report.add(f"2b. GLASS = commit {commit[:7]} + glass_local_fixes.patch{in_head}", checked, fails)

    # 3. fill -------------------------------------------------------------------
    fill_ns: dict[str, Any] = {"re": re, "Any": Any, "KPIGenerationResult": KPIGenerationResult}
    # A run whose own GLASS code already normalises measurable_as (inside _finalize_generated_result) is
    # replayed with that step too; a normalised folder adds it afterwards (checked against its warnings).
    inline_norm = not normalised and "_normalise_result_measurable_as" in top_level_text(reference)
    roots = ["_fill_missing_activity_measurable_as"] + (["_normalise_result_measurable_as"] if normalised or inline_norm else [])
    exec(compile(needed_definitions(reference, roots), "app_fill",
                 "exec", flags=__future__.annotations.compiler_flag, dont_inherit=True), fill_ns)
    fill = fill_ns["_fill_missing_activity_measurable_as"]
    fails, checked, n_filled, n_normalised, profiles = [], 0, 0, 0, {}
    for (cid, rep), rec in records.items():
        if not rec.get("result"):
            continue
        checked += 1
        parsed = KPIGenerationResult.model_validate(rec["result_parsed"])
        if parse_kpi_generation_payload(rec["raw"]).model_dump(mode="json") != parsed.model_dump(mode="json"):
            fails.append(f"{cid} rep {rep}: result_parsed != parse(raw)")
        stored = [k["measurable_as"] for k in rec["result"]["kpis"]]
        if normalised or inline_norm:
            event_log = rec["event_log"]
            if event_log not in profiles:
                with open(LOGS / event_log, "rb") as fh:
                    profiles[event_log] = profile_event_log(fh)
            parsed, norm_warnings = fill_ns["_normalise_result_measurable_as"](parsed, log_profile=profiles[event_log])
        if inline_norm:
            n_normalised += len(norm_warnings)
        if normalised:
            n_normalised += len(norm_warnings)
            if len(norm_warnings) != len(rec["normalisation_warnings"]):
                fails.append(f"{cid} rep {rep}: {len(norm_warnings)} normalisations re-derived, "
                             f"{len(rec['normalisation_warnings'])} recorded")
            if [k.get("measurable_as_raw") for k in rec["result"]["kpis"]] != [k["measurable_as"] for k in rec["result_parsed"]["kpis"]]:
                fails.append(f"{cid} rep {rep}: measurable_as_raw on delivered KPIs != result_parsed measurable_as")
            final_by_name = {k["name"]: k for k in rec["result"]["kpis"]}
            for w in rec["normalisation_warnings"]:
                checked += 1
                if final_by_name[w["kpi_names"][0]]["measurable_as"] != w["details"]["measurable_as"]:
                    fails.append(f"{cid} rep {rep}: normalisation warning for {w['kpi_names']} does not match the KPI")
        refilled, warnings = fill(parsed)
        want = [k.measurable_as for k in refilled.kpis]
        trace = rec["measurable_as_trace"]
        n_filled += len(warnings)
        if want != stored:
            fails.append(f"{cid} rep {rep}: re-derived {want} != stored {stored}")
        if [t["measurable_as_raw"] for t in trace] != [k["measurable_as"] for k in rec["result_parsed"]["kpis"]] \
                or [t["measurable_as"] for t in trace] != stored:
            fails.append(f"{cid} rep {rep}: measurable_as_trace inconsistent")
        if len(warnings) != len(rec["fill_warnings"]):
            fails.append(f"{cid} rep {rep}: {len(warnings)} fills re-derived, {len(rec['fill_warnings'])} recorded")
        final_by_name = {k["name"]: k for k in rec["result"]["kpis"]}
        for issue in rec["semantic_validation"]["issues"]:
            if issue.get("code") == "unsupported_context_condition_dropped":
                checked += 1
                if any(final_by_name.get(n, {}).get("context_segmentation") for n in issue["kpi_names"]):
                    fails.append(f"{cid} rep {rep}: segmentation reported dropped but still present for {issue['kpi_names']}")
    report.add(f"3. {'normalisation + ' if normalised or inline_norm else ''}fill step re-run ({checked} checks, "
               + (f"{n_normalised} measurable_as normalised, " if normalised or inline_norm else "")
               + f"{n_filled} measurable_as filled by the app)", checked, fails)

    # 4. attempts ---------------------------------------------------------------
    fails, checked = [], 0
    for (cid, rep), rec in records.items():
        att = rec["attempts"]
        checked += 1
        if not 1 <= len(att) <= 3 or att[0]["user_prompt"] is not None:
            fails.append(f"{cid} rep {rep}: {len(att)} attempts / first prompt not the base prompt")
            continue
        for prev, cur in zip(att, att[1:]):
            checked += 1
            prompt, base = cur["user_prompt"] or "", rec["user_prompt"]
            schema = base + "\n\nYour previous output did not match the required JSON schema."
            semantic = base + "\n\nYour previous output matched the JSON schema but had semantic KPI-quality issues."
            if not (prompt.startswith(schema) or prompt.startswith(semantic)) or \
                    not prompt.endswith("Previous invalid output:\n" + prev["raw"]):
                fails.append(f"{cid} rep {rep} attempt {cur['attempt']}: retry prompt is not parse_with_retries' prompt")
        if rec["status"] in ("ok", "parse_error") and rec["raw"] != att[-1]["raw"]:
            fails.append(f"{cid} rep {rep}: final raw != last attempt")
    report.add("4. attempt records consistent with parse_with_retries", checked, fails)

    # 5. logs -------------------------------------------------------------------
    fails, checked, evidence = [], 0, {}
    for (cid, rep), rec in records.items():
        cfg = next(c for c in configs if c["id"] == cid)
        checked += 3
        log = LOGS / cfg["event_log"]
        if rec["event_log"] != cfg["event_log"]:
            fails.append(f"{cid} rep {rep}: used {rec['event_log']}, config says {cfg['event_log']}")
        if rec["event_log_sha256"] != digest(log.read_bytes()):
            fails.append(f"{cid} rep {rep}: event_log_sha256 != SHA-256 of {log.name}")
        if cid not in evidence:
            with open(log, "rb") as fh:
                profile = profile_event_log(fh)
            evidence[cid] = (build_log_evidence_prompt(profile), build_context_evidence_prompt(profile))
        if not all(part in rec["user_prompt"] for part in evidence[cid] if part):
            fails.append(f"{cid} rep {rep}: user prompt does not contain the evidence built from {log.name}")
        if cfg["simulation_goal"] not in rec["user_prompt"]:
            fails.append(f"{cid} rep {rep}: user prompt does not contain the configured goal")
    systems = {r["system_prompt"] for r in records.values()}
    checked += 1
    if len(systems) != 1:
        fails.append(f"{len(systems)} different system prompts across reps")
    report.add("5. event logs and prompts match stage1_config.json", checked, fails)

    # 6. summary ----------------------------------------------------------------
    fails = []
    with open(results_dir / "stage1_summary.csv", newline="", encoding="utf-8") as f:
        batch_rows = {(r["config_id"], r["rep"]): r for r in csv.DictReader(f)}
    fresh = {(cid, str(rep)): fresh_row(rec, next(c for c in configs if c["id"] == cid), normalised)
             for (cid, rep), rec in records.items()}
    for key in sorted(set(batch_rows) | set(fresh)):
        if key not in batch_rows or key not in fresh:
            fails.append(f"{key}: row only in {'fresh rebuild' if key in fresh else 'stage1_summary.csv'}")
            continue
        for col in sorted(set(batch_rows[key]) | set(fresh[key])):
            a, b = batch_rows[key].get(col), fresh[key].get(col)
            if a != b:
                fails.append(f"{key[0]} rep {key[1]} column {col}: csv={a!r} rebuilt={b!r}")
    report.add(f"6. stage1_summary.csv rebuilt independently ({len(fresh)} rows)", len(fresh), fails)

    # report ----------------------------------------------------------------------
    verdict = "PASS" if report.passed else "FAIL"
    md = [f"# Stage-1 results verification: {verdict}", "",
          f"Produced by `{Path(__file__).resolve().relative_to(REPO).as_posix()}`; findings are reported, never patched.", "",
          "| check | checks | failures | result |", "|---|---|---|---|"]
    md += [f"| {t} | {n} | {len(f)} | {'PASS' if not f else 'FAIL'} |" for t, n, f in report.sections]
    for title, _, f in report.sections:
        if f:
            md += ["", f"## {title}", ""] + [f"- {x}" for x in f]
    (results_dir / "verification_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\nOVERALL: {verdict}  (report: {results_dir / 'verification_report.md'})")
    sys.exit(0 if report.passed else 1)


if __name__ == "__main__":
    main()
