"""
README and workbook of the Stage-1 description ablation, from the CSV files and raw records that
run_stage1_description_ablation.py wrote to results/stage1_description_ablation/.

Writes results/stage1_description_ablation/README.md and results/GLASS_stage1_description_ablation.xlsx.
Usage (repo root): python evaluation/loanapp_p2p/scripts/make_stage1_description_ablation_report.py
"""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_stage1_description_ablation as abl  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
REPO = BASE.parent.parent
OUT = BASE / "results" / "stage1_description_ablation"
XLSX = BASE / "results" / "GLASS_stage1_description_ablation.xlsx"
FILES = ["description_ablation_runs.csv", "description_ablation_per_config.csv",
         "description_ablation_per_config_comparison.csv", "description_ablation_per_variant.csv",
         "description_ablation_tests.csv"]
LABELS = {
    "strict_reps_all_expected_found": "Runs with all expected KPIs found (strict)",
    "lenient_reps_all_expected_found": "Runs with all expected KPIs found (lenient)",
    "strict_expected_identified": "Expected KPIs identified (strict)",
    "lenient_expected_identified": "Expected KPIs identified (lenient)",
    "strict_mean_n_extra": "KPIs not asked for per run, strict (mean)",
    "lenient_mean_n_extra": "KPIs not asked for per run, lenient (mean)",
    "mean_n_returned": "KPIs returned per run (mean)",
    "kpi_set_jaccard": "KPI-set Jaccard over the repetitions",
    "category_stability": "Category stability over the repetitions",
}


def rows(name: str) -> list[dict]:
    return list(csv.DictReader(open(OUT / name, encoding="utf-8")))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def rel(p: Path) -> str:
    return p.resolve().relative_to(REPO).as_posix()


def prompt_pairs_identical(recs: list[dict]) -> str:
    """Pairs (configuration, rep) whose prompts are equal apart from the description block, of all pairs."""
    by = {(r["variant"], r["config_id"], r["rep"]): r for r in recs}
    n = ok = 0
    for (v, cid, rep), a in by.items():
        if v != "with_description" or ("no_description", cid, rep) not in by:
            continue
        b = by[("no_description", cid, rep)]
        n += 1
        ok += (a["system_prompt"] == b["system_prompt"] and a["few_shot_messages"] == b["few_shot_messages"]
               and a["user_prompt"].replace(a["description_block_in_prompt"], "")
               == b["user_prompt"].replace(b["description_block_in_prompt"], ""))
    return f"{ok} of {n}"


def md_table(header: list[str], body: list[list]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + \
           ["| " + " | ".join("" if c is None else str(c) for c in r) + " |" for r in body]


def main() -> None:
    config = json.loads((BASE / "setup" / "stage1_config.json").read_text(encoding="utf-8"))
    recs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((OUT / "raw").glob("*/*/rep_*.json"))]
    first = recs[0]
    stamps = sorted(r["timestamp"] for r in recs)
    var = {r["variant"]: r for r in rows("description_ablation_per_variant.csv")}
    comp, tests = rows("description_ablation_per_config_comparison.csv"), rows("description_ablation_tests.csv")
    no_desc = next(r for r in recs if r["variant"] == "no_description")
    gate_msgs = sorted({r["scope_gate_message"] for r in recs if r["variant"] == "no_description"})

    vt_keys = [("runs", "Runs"), ("runs_ok", "Runs with a result"), ("runs_parse_error", "Parse errors"),
               ("strict_expected_identified", "Expected KPIs identified (strict)"),
               ("strict_runs_all_expected_found", "Runs with all expected found (strict)"),
               ("lenient_expected_identified", "Expected KPIs identified (lenient)"),
               ("lenient_runs_all_expected_found", "Runs with all expected found (lenient)"),
               ("strict_mean_n_extra", "KPIs not asked for per run, strict (mean)"),
               ("lenient_mean_n_extra", "KPIs not asked for per run, lenient (mean)"),
               ("mean_n_returned", "KPIs returned per run (mean)"),
               ("kpi_set_jaccard_mean_over_configs", "KPI-set Jaccard (mean over configurations)"),
               ("category_stability_mean_over_configs", "Category stability (mean over configurations)"),
               ("mean_attempts", "LLM attempts per run (mean)"),
               ("runs_scope_gate_passed", "Runs whose inputs pass the UI keyword gate")]
    vt = [[label, var["with_description"][k], var["no_description"][k]] for k, label in vt_keys]
    fisher = [t for t in tests if t["test"].startswith("Fisher")]
    signs = [t for t in tests if t["test"].startswith("sign")]
    L = ["# Stage 1 with and without the process description", "",
         f"Generated by `{rel(Path(__file__))}` from the records of "
         f"`{rel(Path(abl.__file__))}`.", "",
         "**Question.** Does GLASS's Stage 1 identify the expected KPIs as well without the process description? "
         "Both variants run every configuration of `setup/stage1_config.json` five times through the Step 1 "
         "batch's Stage-1 path, with the same model, temperature, number of KPIs and event-log evidence. The only "
         "difference is the process description passed to GLASS's prompt builder.", "",
         "- **with_description:** the configuration's process description, as in the Step 1 batch.",
         "- **no_description:** an empty string, passed to the existing prompt builder unchanged (GLASS v3.3 as "
         "it is, no code change). The prompt then still contains the description heading with an empty quoted "
         "block; this is what every no_description prompt contains in that place:", "",
         "```text", no_desc["description_block_in_prompt"].rstrip("\n"), "```", "",
         "  The rest of the prompt (system prompt, few-shot examples, goal, log and context evidence, "
         f"requirements) is the same in both variants: for all {sum(r['variant'] == 'with_description' for r in recs)} "
         "pairs of runs (same configuration and repetition) the system prompt, the few-shot messages and the user "
         "prompt without the description block are identical (checked when this README was written: "
         f"{prompt_pairs_identical(recs)} identical).", "",
         "## Results per variant", ""]
    L += md_table(["Measure", "with_description", "no_description"], vt)
    L += ["", "## Tests (two-sided)", "", "Fisher exact test on the pooled runs or expected-KPI instances:", ""]
    L += md_table(["Measure", "with_description", "no_description", "p"],
                  [[LABELS[t["measure"]], t["with_description"], t["no_description"], t["p"]] for t in fisher])
    L += ["", "Sign test over the configurations (difference with_description − no_description; ties dropped):", ""]
    L += md_table(["Measure", "Configurations that differ", "with_description higher", "p"],
                  [[LABELS[t["measure"]], t["n"], t["with_greater"], t["p"]] for t in signs])
    L += ["", "## Per configuration", "",
          "Runs with all expected KPIs found (out of 5) and the per-configuration Fisher test; Jaccard and "
          "category stability over the five repetitions.", ""]
    L += md_table(["Configuration", "All found strict, with / without", "p", "All found lenient, with / without", "p",
                   "Extras strict, with / without", "Jaccard, with / without", "Category stability, with / without"],
                  [[r["config_id"],
                    f"{r['strict_reps_all_expected_found_with']} / {r['strict_reps_all_expected_found_without']}",
                    r["strict_reps_all_expected_found_fisher_p"],
                    f"{r['lenient_reps_all_expected_found_with']} / {r['lenient_reps_all_expected_found_without']}",
                    r["lenient_reps_all_expected_found_fisher_p"],
                    f"{r['strict_mean_n_extra_with']} / {r['strict_mean_n_extra_without']}",
                    f"{r['kpi_set_jaccard_with']} / {r['kpi_set_jaccard_without']}",
                    f"{r['category_stability_with']} / {r['category_stability_without']}"] for r in comp])
    L += ["", "## UI keyword gate", "",
          "The UI's Generate button runs `validate_generation_scope` before Stage 1. As in the Step 1 batch, the "
          "verdict is recorded per run (`scope_gate_passed`, `scope_gate_message`) but not enforced. With an empty "
          "description the gate rejects every run, so in the UI no_description would get an error and no KPIs. "
          "Messages: " + "; ".join(f"\"{m}\"" for m in gate_msgs) + ". With the description, "
          f"{var['with_description']['runs_scope_gate_passed']} of {var['with_description']['runs']} runs pass; the "
          "others fail on the goal's wording (no performance keyword), as in the Step 1 batch.", "",
          "## Run", "",
          f"- GLASS: commit `{first['glass_commit']}` (v3.3), no local changes under `goal_to_parameters/`",
          f"- Provider / model: `{first['provider']}` / `{first['model']}`; temperature {first['temperature']}; "
          f"json_mode {first['json_mode']}; num_kpis {first['num_kpis']}; log evidence {first['use_log_evidence']}",
          f"- Runs: {len(recs)} ({len(config['configurations'])} configurations × 2 variants × "
          f"{config['settings']['repetitions']} repetitions); first / last run started {stamps[0]} / {stamps[-1]}",
          "- Command: `python evaluation/loanapp_p2p/scripts/run_stage1_description_ablation.py --workers 4`",
          "- Check: `python evaluation/loanapp_p2p/scripts/verify_stage1_description_ablation.py` (rebuilds every "
          "CSV from the raw records with its own implementation of the scoring)", "",
          "## Files", "",
          "- `raw/<variant>/<configuration>/rep_<k>.json`: the Step 1 batch's record of the run, plus `variant`, "
          "`process_description_passed`, `description_block_in_prompt`, `glass_commit` and "
          "`step1_batch_script_sha256`",
          "- `description_ablation_runs.csv`: one row per run (Step 1 scoring columns plus expected KPIs found, "
          "KPI keys and categories)",
          "- `description_ablation_per_config.csv`: one row per configuration and variant",
          "- `description_ablation_per_config_comparison.csv`: the two variants side by side per configuration, "
          "with the per-configuration Fisher tests",
          "- `description_ablation_per_variant.csv`: one row per variant",
          "- `description_ablation_tests.csv`: pooled Fisher tests and sign tests",
          f"- `{rel(XLSX)}`: the same tables as a workbook", "",
          "SHA-256 of the inputs:", "",
          f"- `{rel(BASE / 'setup' / 'stage1_config.json')}`: `{sha(BASE / 'setup' / 'stage1_config.json')}`",
          f"- `{rel(Path(abl.__file__))}`: `{first['script_sha256']}` (as recorded in the runs)",
          f"- `{rel(Path(abl.s1.__file__))}`: `{first['step1_batch_script_sha256']}` (as recorded in the runs)",
          f"- `goal_to_parameters/app.py`: `{first['app_py_sha256']}` (as recorded in the runs)",
          *[f"- `{rel(BASE / 'input_logs' / n)}`: `{sha(BASE / 'input_logs' / n)}`"
            for n in sorted({c['event_log'] for c in config['configurations']})], "",
          "## Scoring (copied from the script header)", "", "```",
          abl.__doc__.split("SCORING (analysis only; the recording is never changed by scoring)")[1]
          .split("END SCORING")[0].strip("\n"), "```", ""]
    (OUT / "README.md").write_text("\n".join(L), encoding="utf-8", newline="\n")

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    head = Font(bold=True)
    fill = PatternFill("solid", fgColor="DDE7F0")

    def sheet(title: str, header: list[str], body: list[list], widths: list[int] | None = None, first=False):
        ws = wb.active if first else wb.create_sheet()
        ws.title = title
        ws.append(header)
        for c in ws[1]:
            c.font, c.fill, c.alignment = head, fill, Alignment(wrap_text=True, vertical="top")
        for r in body:
            ws.append([_num(x) for x in r])
        for i, w in enumerate(widths or [], 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = w
        ws.freeze_panes = "A2"

    sheet("Per variant", ["Measure", "with_description", "no_description"], vt, [52, 20, 20], first=True)
    sheet("Tests", ["Test", "Measure", "with_description", "no_description", "Configurations that differ",
                    "with_description higher", "p"],
          [[t["test"], LABELS[t["measure"]], t["with_description"], t["no_description"], t["n"], t["with_greater"], t["p"]]
           for t in tests], [30, 48, 18, 18, 14, 14, 10])
    c_rows = rows("description_ablation_per_config_comparison.csv")
    sheet("Per configuration", list(c_rows[0].keys()), [list(r.values()) for r in c_rows], [20] + [16] * 30)
    for name, title in (("description_ablation_per_config.csv", "Per configuration and variant"),
                        ("description_ablation_runs.csv", "All runs")):
        rr = rows(name)
        sheet(title, list(rr[0].keys()), [list(r.values()) for r in rr], [18] * 40)
    scoring = abl.__doc__.split("SCORING (analysis only; the recording is never changed by scoring)")[1] \
        .split("END SCORING")[0].strip("\n").splitlines()
    sheet("Settings and scoring", ["Item", "Value"],
          [["GLASS", f"commit {first['glass_commit']} (v3.3), no local changes"],
           ["Model", f"{first['model']}, temperature {first['temperature']}, json_mode {first['json_mode']}"],
           ["Inputs per run", "process description (with_description) or empty string (no_description) + goal + "
                              "event-log evidence, as in the Step 1 batch"],
           ["Runs", f"{len(recs)}: {len(config['configurations'])} configurations x 2 variants x "
                    f"{config['settings']['repetitions']} repetitions"],
           ["no_description prompt block", no_desc["description_block_in_prompt"]],
           ["Scoring", ""]] + [["", line] for line in scoring], [28, 120])
    wb.save(XLSX)
    print(f"wrote {rel(OUT / 'README.md')} and {rel(XLSX)}")


def _num(x):
    if isinstance(x, str):
        try:
            return int(x)
        except ValueError:
            try:
                return float(x)
            except ValueError:
                return x
    return x


if __name__ == "__main__":
    main()
