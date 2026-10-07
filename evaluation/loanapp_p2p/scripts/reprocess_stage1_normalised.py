"""
Re-applies GLASS's deterministic Stage-1 post-processing to the recorded LLM outputs of
results/stage1_results/ and writes results/stage1_results_normalised/. No LLM is called.

For every raw/<id>/rep_<k>.json with a result, the stored result_parsed
(= parse_kpi_generation_payload(raw)) goes through app.py's current _finalize_generated_result
(grounding sanitize -> normalise_measurable_as -> _fill_missing_activity_measurable_as ->
semantic validation), with log_profile / context_evidence rebuilt from the configuration's event log
by app.py's extract_log_artifacts, as handle_generation builds them. The app.py functions are compiled
from app.py's source by run_stage1_batch.load_app_stage1, as in the batch.

Each output record is the source record with result, measurable_as_trace, fill_warnings,
normalisation_warnings and semantic_validation replaced and a "normalisation" block added; raw,
attempts, result_parsed, prompts and run metadata are copied unchanged. The script aborts if anything
other than measurable_as / measurable_as_raw would differ from the source result, or if the semantic
validation issues (other than the measurable_as warnings) differ. results/stage1_results/ is only read.

Usage (repo root):  python evaluation/loanapp_p2p/scripts/reprocess_stage1_normalised.py
Run it in a checkout of the base commit with this folder's glass_local_fixes.patch applied
(see evaluation/loanapp_p2p/README.md); it refuses to run on any other commit.
"""

from __future__ import annotations

import copy
import csv
import json
import platform
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_stage1_batch as batch  # noqa: E402  (also puts goal_to_parameters/ on sys.path)

from models import KPIGenerationResult  # noqa: E402

SOURCE = batch.RESULTS_DIR / "stage1_results"
OUT = batch.RESULTS_DIR / "stage1_results_normalised"
MEASURABLE_AS_CODES = {"inferred_measurable_as", "measurable_as_normalised"}
REPLACED_FIELDS = ("result", "measurable_as_trace", "fill_warnings", "normalisation_warnings",
                   "semantic_validation", "normalisation")


def _without_measurable_as(kpis: list[dict]) -> list[dict]:
    return [{k: v for k, v in kpi.items() if k not in ("measurable_as", "measurable_as_raw")} for kpi in kpis]


def _other_issues(validation: dict) -> list[dict]:
    return [i for i in validation.get("issues", []) if i.get("code") not in MEASURABLE_AS_CODES]


def reprocess(rec: dict, app: dict, artifacts: tuple, meta: dict) -> dict:
    new = copy.deepcopy(rec)
    new["normalisation"] = meta
    if not rec.get("result_parsed"):
        return new
    log_profile, _, context_evidence = artifacts
    parsed = KPIGenerationResult.model_validate(rec["result_parsed"])
    where = f"{rec['config_id']} rep {rec['rep']}"
    if app["parse_kpi_generation_payload"](rec["raw"]).model_dump(mode="json") != parsed.model_dump(mode="json"):
        sys.exit(f"{where}: stored result_parsed != parse_kpi_generation_payload(raw)")
    result, validation = app["_finalize_generated_result"](
        parsed, simulation_goal=rec["simulation_goal"], log_profile=log_profile, context_evidence=context_evidence)
    final = result.model_dump(mode="python")
    old_kpis = rec["result"]["kpis"]
    if _without_measurable_as(final["kpis"]) != _without_measurable_as(old_kpis) or \
            {k: v for k, v in final.items() if k != "kpis"} != {k: v for k, v in rec["result"].items() if k != "kpis"}:
        sys.exit(f"{where}: re-processing changed more than measurable_as")
    if _other_issues(validation) != _other_issues(rec["semantic_validation"]) or \
            validation.get("has_errors") != rec["semantic_validation"].get("has_errors"):
        sys.exit(f"{where}: semantic validation differs from the source run beyond measurable_as warnings")
    new["result"] = final
    new["measurable_as_trace"] = [
        {"name": f["name"], "measurable_as_raw": p["measurable_as"], "measurable_as_stage1_results": o["measurable_as"],
         "measurable_as": f["measurable_as"]}
        for p, o, f in zip(rec["result_parsed"]["kpis"], old_kpis, final["kpis"])]
    new["fill_warnings"] = [i for i in validation["issues"] if i.get("code") == "inferred_measurable_as"]
    new["normalisation_warnings"] = [i for i in validation["issues"] if i.get("code") == "measurable_as_normalised"]
    new["semantic_validation"] = validation
    return new


_batch_score = batch.score


def score_with_normalisation(rec: dict, cfg: dict) -> dict:
    """batch.score plus n_measurable_as_normalised, placed after n_filled_by_app."""
    out = {}
    for key, value in _batch_score(rec, cfg).items():
        out[key] = value
        if key == "n_filled_by_app":
            out["n_measurable_as_normalised"] = len(rec.get("normalisation_warnings") or [])
    return out


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_readme(configs, app_sources, patch, source_patch_sha, meta, records, started) -> None:
    git = lambda *a: subprocess.run(["git", *a], cwd=batch.REPO_ROOT, capture_output=True, text=True).stdout.strip()
    first = next(iter(records.values()))
    rules = Counter(w["details"]["rule"] for r in records.values() for w in r.get("normalisation_warnings") or [])
    n_norm = sum(len(r.get("normalisation_warnings") or []) for r in records.values())
    n_fill = sum(len(r.get("fill_warnings") or []) for r in records.values())
    n_internal = sum(w["details"]["replaced_internal_label"] for r in records.values()
                     for w in r.get("normalisation_warnings") or [])
    before = {r["config_id"]: r for r in read_csv(SOURCE / "stage1_per_config.csv")}
    after = {r["config_id"]: r for r in read_csv(OUT / "stage1_per_config.csv")}
    logs = sorted({c["event_log"] for c in configs})
    lines = [
        "# GLASS Stage-1 evaluation — measurable_as normalised", "",
        f"Generated by `{batch.rel(__file__)}`.", "",
        f"**Same raw LLM outputs as `{batch.rel(SOURCE)}/`; the normalisation was applied afterwards.**",
        "No LLM was called. Every record's `raw`, `attempts`, `result_parsed`, prompts and run metadata are",
        "copied unchanged from `stage1_results/raw/` (each record stores the SHA-256 of its source file in",
        "`normalisation.source_file_sha256`). Only the deterministic post-processing was re-run on",
        "`result_parsed`: app.py `_finalize_generated_result` = grounding sanitize -> `normalise_measurable_as`",
        "-> `_fill_missing_activity_measurable_as` (activity taken from the `start_time('X')` term that is not",
        "subtracted) -> semantic validation, with the log profile rebuilt from each configuration's event log.",
        "The script aborts if anything except `measurable_as` / `measurable_as_raw` would change or if the",
        "semantic validation differs beyond the measurable_as warnings, so retries in the live app would",
        "have been the same.", "",
        "## Reproduce", "", "```",
        f"git checkout {git('rev-parse', 'HEAD')}",
        f"git apply {batch.rel(OUT / batch.PATCH_NAME)}",
        "# plus run_stage1_batch.py, reprocess_stage1_normalised.py, stage1_config.json, input_logs/ and",
        "# stage1_results/raw/ matching the SHA-256 below",
        f"python {batch.rel(__file__)}",
        f"python {batch.rel(Path(__file__).with_name('verify_stage1_results.py'))} --results {batch.rel(OUT)}", "```", "",
        "## Provenance", "",
        f"- Repository commit: `{git('rev-parse', 'HEAD')}`",
        f"- Local patch: `{batch.rel(OUT / batch.PATCH_NAME)}` (= `git diff -- goal_to_parameters`), "
        f"SHA-256 `{batch.sha256(patch)}`",
        f"- It extends the patch the LLM run used (`stage1_results/glass_local_fixes.patch`, SHA-256 `{source_patch_sha}`):",
        "  the `utils/parsing.py` and `utils/semantic_validation.py` hunks are identical; new are `app.py`",
        "  (`normalise_measurable_as`, shared activity extraction in `_fill_missing_activity_measurable_as`) and",
        "  `models/smart_kpi.py` (`measurable_as_raw` field).",
        f"- Re-processed: {meta['timestamp']} (started {started}); Python {platform.python_version()}",
        f"- Source LLM run: model `{first['model']}`, temperature {first['temperature']}, json_mode {first['json_mode']}, "
        f"{len(records)} records; earliest / latest rep timestamp "
        f"{min(r['timestamp'] for r in records.values())} / {max(r['timestamp'] for r in records.values())}",
        f"- Source run's app.py SHA-256 (in each record's `app_py_sha256`): `{first['app_py_sha256']}`; "
        f"source run's patch SHA-256 (`glass_patch_sha256`): `{first['glass_patch_sha256']}`", "",
        "## Effect", "",
        f"- `measurable_as_normalised` warnings: {n_norm} KPIs ({n_internal} replaced an internal metric label); by rule: "
        + ", ".join(f"{rule}: {n}" for rule, n in rules.most_common()),
        f"- `inferred_measurable_as` (fill step) after normalisation: {n_fill} KPIs", "",
        "| config | strict reps all found: stage1_results -> normalised | strict found counts: stage1_results -> normalised |",
        "|---|---|---|",
        *[f"| {cid} | {before[cid]['strict_reps_all_expected_found']} -> {after[cid]['strict_reps_all_expected_found']} | "
          f"{before[cid]['strict_found_counts']} -> {after[cid]['strict_found_counts']} |" for cid in after], "",
        "## Files (SHA-256)", "",
        f"- `{batch.rel(__file__)}`: `{meta['script_sha256']}`",
        f"- `{batch.rel(batch.__file__)}`: `{batch.sha256(Path(batch.__file__).read_bytes())}`",
        f"- `{batch.rel(batch.CONFIG_PATH)}`: `{batch.sha256(batch.CONFIG_PATH.read_bytes())}`",
        f"- `goal_to_parameters/app.py`: `{meta['app_py_sha256']}`",
        *[f"- `{batch.rel(batch.LOGS_DIR / name)}`: `{batch.sha256((batch.LOGS_DIR / name).read_bytes())}`"
          for name in logs], "",
        "## app.py Stage-1 code executed (compiled from app.py source; SHA-256 of each source text)", "",
        *[f"- `{name}`: `{batch.sha256(text)}`" for name, text in app_sources.items()], "",
        "## Scoring rules", "",
        "As in `stage1_results/README.md`, where \"after GLASS's fill step\" now means after",
        "`normalise_measurable_as` and the fill step. Added column `n_measurable_as_normalised` = KPIs whose",
        "measurable_as was changed by `normalise_measurable_as`.", "",
    ]
    (OUT / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    config = json.loads(batch.CONFIG_PATH.read_text(encoding="utf-8"))
    configs = config["configurations"]
    readme = (SOURCE / "README.md").read_text(encoding="utf-8")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=batch.REPO_ROOT, capture_output=True, text=True).stdout.strip()
    if f"Repository commit: `{head}`" not in readme:
        sys.exit(f"HEAD {head} is not the commit stage1_results was produced from")
    patch = batch.glass_patch()
    source_patch_sha = batch.sha256((SOURCE / batch.PATCH_NAME).read_bytes())

    sys.stdout = batch._MuteCostTracker(sys.stdout)
    app, app_sources = batch.load_app_stage1()
    meta = {
        "applied_to": "result_parsed of the source record (no LLM call)",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "script_sha256": batch.sha256(Path(__file__).read_bytes()),
        "app_py_sha256": batch.sha256(batch.APP_PATH.read_bytes()),
        "glass_patch_sha256": batch.sha256(patch),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / batch.PATCH_NAME).write_bytes(patch)

    records, artifacts = {}, {}
    for cfg in configs:
        if cfg["event_log"] not in artifacts:
            with open(batch.LOGS_DIR / cfg["event_log"], "rb") as fh:
                artifacts[cfg["event_log"]] = app["extract_log_artifacts"](fh)
        for path in sorted((SOURCE / "raw" / cfg["id"]).glob("rep_*.json"), key=lambda p: int(p.stem.split("_")[1])):
            source_bytes = path.read_bytes()
            rec = json.loads(source_bytes)
            new = reprocess(rec, app, artifacts[cfg["event_log"]],
                            {**meta, "source_file": path.relative_to(batch.REPO_ROOT).as_posix(),
                             "source_file_sha256": batch.sha256(source_bytes)})
            batch.write_json(OUT / "raw" / cfg["id"] / path.name, new)
            records[(cfg["id"], rec["rep"])] = new
            batch.log(f"{cfg['id']} rep {rec['rep']}: {len(new.get('normalisation_warnings') or [])} normalised, "
                      f"{len(new.get('fill_warnings') or [])} filled")

    batch.score = score_with_normalisation   # write_summaries looks score up in the batch module
    batch.write_summaries(OUT, configs)
    write_readme(configs, app_sources, patch, source_patch_sha, meta, records, started)
    batch.log(f"\nWrote {len(records)} records, stage1_summary.csv, stage1_per_config.csv and README.md in {OUT}")


if __name__ == "__main__":
    main()
