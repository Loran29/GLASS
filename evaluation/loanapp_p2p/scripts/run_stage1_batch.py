"""
Stage-1 batch evaluation (natural-language goal -> SMART KPIs), as delivered by the GLASS app.

Every configuration in setup/stage1_config.json is run N times through the app's own Stage-1
path (goal_to_parameters/app.py, handle_generation): validate_generation_scope ->
extract_log_artifacts -> build_smart_kpi_prompt(sanitize_user_input(...)) -> parse_with_retries
(json_mode=True; parse + semantic repair retries; _finalize_generated_result = grounding sanitize +
_fill_missing_activity_measurable_as + semantic validation). Those functions are compiled from
app.py's own source (see load_app_stage1), so they cannot drift from the app; nothing under
goal_to_parameters/ is modified. LLM answers are stored as returned; scoring is analysis-only.

Usage (repo root):  python evaluation/loanapp_p2p/scripts/run_stage1_batch.py [--smoke | --only ID] [--reps N]
Re-running resumes: reps with a final status are skipped, llm_error reps are retried.

SCORING RULES (analysis only; recording is never changed by scoring)
  strict_*  : an expected measurable_as string is found if it equals, case-insensitively and
              exactly, the measurable_as of a returned KPI after GLASS's fill step.
              strict_extra = returned non-null measurable_as values not in the expected set.
  lenient_* : an expected KPI is found if at least one returned KPI satisfies its rule. Text is
              compared after normalisation: lower-case, "\\'" and "’" -> "'", "_" -> " ".
    "Average Cycle Time"      process_scope == "end_to_end" and category == "time" and
                              "cycle time" in (name or suggested_formula)
    "<Activity> Waiting Time" process_scope == "activity_level" and "<activity>" in (name or
                              suggested_formula) and "wait" in (name or suggested_formula);
                              an activity-duration KPI (no "wait") does not count
    "Resource Utilization"    category == "utilization" or "utilization" in name
              lenient_extra = names of returned KPIs that satisfy none of the expected rules.
  n_null_measurable_as = returned KPIs whose measurable_as is null after the fill step.
END SCORING RULES
"""

from __future__ import annotations

import __future__
import argparse
import ast
import csv
import hashlib
import io
import json
import logging
import os
import platform
import subprocess
import sys
import threading
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from statistics import mean

BASE = Path(__file__).resolve().parent.parent          # evaluation/loanapp_p2p
REPO_ROOT = BASE.parent.parent
SETUP_DIR, LOGS_DIR, RESULTS_DIR = BASE / "setup", BASE / "input_logs", BASE / "results"
APP_PATH = REPO_ROOT / "goal_to_parameters" / "app.py"
CONFIG_PATH = SETUP_DIR / "stage1_config.json"
sys.path.insert(0, str(REPO_ROOT / "goal_to_parameters"))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / "goal_to_parameters" / ".env")

import openai

APP_ROOTS = ("parse_with_retries", "sanitize_user_input", "validate_generation_scope", "extract_log_artifacts")
APP_JSON_MODE = True   # handle_generation passes json_mode=True to parse_with_retries
API_RETRIES, API_BACKOFF_S, PAUSE_S = 3, 20, 1.0
FATAL_API_ERRORS = (openai.AuthenticationError, openai.PermissionDeniedError, openai.NotFoundError)
FINAL_STATUSES = {"ok", "parse_error", "out_of_scope", "app_error"}
PATCH_NAME = "glass_local_fixes.patch"   # uncommitted GLASS changes the run executes (git diff)


def sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def glass_patch() -> bytes:
    """Local, uncommitted changes to goal_to_parameters/ relative to HEAD."""
    return subprocess.run(["git", "diff", "--", "goal_to_parameters"], cwd=REPO_ROOT,
                          capture_output=True, check=True).stdout


_rep_state = threading.local()   # per worker thread: provider, current rep's parse warnings, attempt no.
_print_lock = threading.Lock()


def log(message: str) -> None:
    with _print_lock:
        print(message, flush=True)


class _ParseWarnings(logging.Handler):
    """Collects GLASS parsing warnings (e.g. evidence_basis normalisation) for the rep that is
    running in the current worker thread, tagged with the LLM call they belong to."""

    def emit(self, record):
        sink = getattr(_rep_state, "parse_warnings", None)
        if sink is not None:
            sink.append({"attempt": getattr(_rep_state, "attempt", None), "message": record.getMessage()})


class _MuteCostTracker(io.TextIOBase):
    """Thread-safe stdout filter: drops GLASS's cost-tracker lines, passes everything else."""

    def __init__(self, inner):
        self.inner, self.local = inner, threading.local()

    def write(self, s):
        if "[LLM Cost]" in s:
            self.local.drop_newline = True
            return len(s)
        if s == "\n" and getattr(self.local, "drop_newline", False):
            self.local.drop_newline = False
            return len(s)
        self.local.drop_newline = False
        return self.inner.write(s)

    def flush(self):
        self.inner.flush()


def load_app_stage1() -> tuple[dict, dict[str, str]]:
    """Compile the top-level defs/constants of app.py reachable from APP_ROOTS from app.py's own
    AST (original file name and line numbers), plus app.py's imports minus streamlit/ui. The
    Streamlit module itself is never executed. Returns (namespace, {name: exact source text})."""
    src = APP_PATH.read_text(encoding="utf-8")
    defs, imports = {}, []
    for node in ast.parse(src).body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
            if module != "__future__" and module != "streamlit" and not module.startswith("ui."):
                imports.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            defs[node.name] = node
        elif isinstance(node, ast.Assign):
            defs.update({n.id: node for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name)})
    needed, stack = set(), list(APP_ROOTS)
    while stack:
        name = stack.pop()
        if name not in needed:
            needed.add(name)
            stack += [n.id for n in ast.walk(defs[name]) if isinstance(n, ast.Name) and n.id in defs]
    nodes = sorted({id(defs[n]): defs[n] for n in needed}.values(), key=lambda n: n.lineno)
    namespace = {"__name__": "glass_app_stage1"}
    code = compile(ast.Module(body=imports + nodes, type_ignores=[]), str(APP_PATH), "exec",
                   flags=__future__.annotations.compiler_flag, dont_inherit=True)
    exec(code, namespace)
    return namespace, {name: ast.get_source_segment(src, defs[name]) for name in sorted(needed)}


APP_PAGE_FN = "_render_goal_to_parameters_page"   # sets `temperature = 0.2` and calls handle_generation


def app_temperature() -> float:
    """The temperature app.py's page function hard-codes for handle_generation."""
    page_fn = next(n for n in ast.parse(APP_PATH.read_text(encoding="utf-8")).body
                   if isinstance(n, ast.FunctionDef) and n.name == APP_PAGE_FN)
    return next(n.value.value for n in ast.walk(page_fn) if isinstance(n, ast.Assign)
                and getattr(n.targets[0], "id", None) == "temperature")


class LLMCallFailed(Exception):
    """API / provider failure that persisted through all back-off retries."""


class RecordingProvider:
    """Wraps the real provider: logs every call verbatim, pauses between calls and adds the
    20 s back-off for HTTP / rate-limit / provider failures. Answers are passed through untouched."""

    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def get_model_name(self) -> str:
        return self.inner.get_model_name()

    def generate(self, system_prompt, user_prompt, temperature=0.3, **kwargs) -> str:
        if self.calls:
            time.sleep(PAUSE_S)
        t0 = time.time()
        for attempt in range(API_RETRIES + 1):
            try:
                raw = self.inner.generate(system_prompt=system_prompt, user_prompt=user_prompt,
                                          temperature=temperature, **kwargs)
                break
            except FATAL_API_ERRORS:
                raise
            except Exception as exc:  # incl. OpenRouter HTTP-200 error bodies -> choices=None
                if attempt == API_RETRIES or (isinstance(exc, openai.APIStatusError)
                                              and exc.status_code < 500 and exc.status_code not in (408, 409, 429)):
                    raise LLMCallFailed(f"{type(exc).__name__}: {exc}") from exc
                log(f"    API error ({type(exc).__name__}); waiting {API_BACKOFF_S}s, retry {attempt + 1}/{API_RETRIES}")
                time.sleep(API_BACKOFF_S)
        self.calls.append({"user_prompt": user_prompt, "raw": raw, "elapsed_s": round(time.time() - t0, 2),
                           "temperature": temperature, "kwargs": kwargs})
        _rep_state.attempt = len(self.calls)   # parse warnings that follow belong to this call
        return raw


def classify_attempt(app, raw, goal, log_profile, context_evidence) -> tuple[str, str | None]:
    """Re-run the app's deterministic checks on one recorded answer to label why it was retried."""
    try:
        parsed = app["parse_kpi_generation_payload"](raw)
        _, validation = app["_finalize_generated_result"](parsed, simulation_goal=goal, log_profile=log_profile,
                                                          context_evidence=context_evidence)
    except app["KPIParsingError"] as exc:
        return "parse_error", str(exc)
    except Exception as exc:
        return "exception", f"{type(exc).__name__}: {exc}"
    if validation.get("has_errors"):
        return "semantic_error", " | ".join(i["message"] for i in validation["issues"] if i.get("severity") == "error")
    return "ok", None


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# ----------------------------------------------------------------- scoring ---

def _norm(text) -> str:
    return str(text or "").lower().replace("\\'", "'").replace("’", "'").replace("_", " ")


def lenient_match(expected: str, kpi: dict) -> bool:
    name, formula = _norm(kpi.get("name")), _norm(kpi.get("suggested_formula"))
    text = name + "\n" + formula
    if expected == "Average Cycle Time":
        return kpi.get("process_scope") == "end_to_end" and kpi.get("category") == "time" and "cycle time" in text
    if expected.endswith(" Waiting Time"):
        activity = _norm(expected[: -len(" Waiting Time")])
        return kpi.get("process_scope") == "activity_level" and activity in text and "wait" in text
    if expected == "Resource Utilization":
        return kpi.get("category") == "utilization" or "utilization" in name
    return _norm(kpi.get("measurable_as")) == _norm(expected)


def score(rec: dict, cfg: dict) -> dict:
    kpis = (rec.get("result") or {}).get("kpis") or []
    expected = cfg["expected_measurable_as"]
    returned_lc = {k["measurable_as"].lower() for k in kpis if k.get("measurable_as")}
    expected_lc = {e.lower() for e in expected}
    s_found = [e for e in expected if e.lower() in returned_lc]
    s_extra = [k["measurable_as"] for k in kpis if k.get("measurable_as") and k["measurable_as"].lower() not in expected_lc]
    l_found = [e for e in expected if any(lenient_match(e, k) for k in kpis)]
    l_extra = [k.get("name") for k in kpis if not any(lenient_match(e, k) for e in expected)]
    attempts = rec.get("attempts") or []
    first = attempts[0] if attempts else {}
    has_answer = rec["status"] in ("ok", "out_of_scope")
    return {
        "config_id": cfg["id"], "process": cfg["process"], "goal_config": cfg["goal_config"],
        "rep": rec["rep"], "status": rec["status"], "scope_gate_passed": rec.get("scope_gate_passed"),
        "attempts": len(attempts),
        "first_attempt_parse_error": first.get("outcome") == "parse_error",
        "first_attempt_semantic_error": first.get("outcome") == "semantic_error",
        "first_attempt_error": first.get("error") or "",
        "expected_measurable_as": "|".join(expected),
        "returned_kpis": ";".join(f"{k.get('name')}|{k.get('measurable_as') or ''}|{k.get('target_direction')}|"
                                  f"{k.get('process_scope')}" for k in kpis),
        "n_returned": len(kpis),
        "n_null_measurable_as": sum(not k.get("measurable_as") for k in kpis),
        "n_filled_by_app": len(rec.get("fill_warnings") or []),
        "n_evidence_basis_normalised": sum(w["attempt"] == len(attempts) for w in rec.get("parse_warnings") or [])
        if has_answer else 0,
        "n_segmentation_dropped": sum(i.get("code") == "unsupported_context_condition_dropped"
                                      for i in (rec.get("semantic_validation") or {}).get("issues", [])),
        "strict_found": "|".join(s_found), "strict_missing": "|".join(e for e in expected if e not in s_found),
        "strict_extra": "|".join(s_extra), "strict_all_expected_found": has_answer and len(s_found) == len(expected),
        "strict_n_extra": len(s_extra),
        "utilization_direction": "|".join(str(k.get("target_direction")) for k in kpis
                                          if (k.get("measurable_as") or "").lower() == "resource utilization"),
        "lenient_found": "|".join(l_found), "lenient_missing": "|".join(e for e in expected if e not in l_found),
        "lenient_extra": "|".join(l_extra), "lenient_all_expected_found": has_answer and len(l_found) == len(expected),
        "lenient_n_extra": len(l_extra),
        "lenient_utilization_direction": "|".join(str(k.get("target_direction")) for k in kpis
                                                  if lenient_match("Resource Utilization", k)),
        "goal_structured": (rec.get("result") or {}).get("simulation_goal_structured", ""),
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    if rows:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)


def write_summaries(out: Path, configs: list[dict]) -> list[dict]:
    """Score every raw file on disk (all configs, all reps), so partial runs never drop data."""
    summary, per_config = [], []
    for cfg in configs:
        files = sorted((out / "raw" / cfg["id"]).glob("rep_*.json"), key=lambda p: int(p.stem.split("_")[1]))
        records = [load_json(p) for p in files]
        rows = [score(r, cfg) for r in records]
        if not rows:
            continue
        summary += rows
        ok = [r for r in rows if r["status"] == "ok"]
        row = {"config_id": cfg["id"], "process": cfg["process"], "goal_config": cfg["goal_config"],
               "scope_gate_passed": rows[0]["scope_gate_passed"], "reps": len(rows), "reps_ok": len(ok),
               "expected_measurable_as": "|".join(cfg["expected_measurable_as"])}
        for mode in ("strict", "lenient"):
            counts = Counter(e for r in rows for e in r[f"{mode}_found"].split("|") if e)
            row[f"{mode}_found_counts"] = "|".join(f"{e}:{counts[e]}" for e in cfg["expected_measurable_as"])
            row[f"{mode}_reps_all_expected_found"] = sum(r[f"{mode}_all_expected_found"] for r in rows)
            row[f"{mode}_mean_n_extra"] = round(mean(r[f"{mode}_n_extra"] for r in ok), 2) if ok else ""
        row["mean_attempts"] = round(mean(r["attempts"] for r in rows), 2)
        row["reps_first_attempt_failed"] = sum(r["first_attempt_parse_error"] or r["first_attempt_semantic_error"] for r in rows)
        distinct = Counter(m for rec in records if rec["status"] == "ok"
                           for m in {k.get("measurable_as") or "(null)" for k in rec["result"]["kpis"]})
        row["distinct_measurable_as"] = "|".join(f"{m}:{c}" for m, c in distinct.most_common())
        per_config.append(row)
    write_csv(out / "stage1_summary.csv", summary)
    write_csv(out / "stage1_per_config.csv", per_config)
    return summary


def rel(path) -> str:
    """Path relative to the repository root, with forward slashes (as written in READMEs)."""
    return Path(path).resolve().relative_to(REPO_ROOT).as_posix()


def write_readme(out, args, settings, configs, provider, temperature, app_sources, started, ended, patch) -> None:
    git = lambda *a: subprocess.run(["git", *a], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()
    stamps = [load_json(p)["timestamp"] for p in (out / "raw").glob("*/rep_*.json")]
    logs = sorted({c["event_log"] for c in configs})
    rules = __doc__.split("SCORING RULES", 1)[1].split("END SCORING RULES")[0]
    # command line as typed, with paths inside the repository written relative to it
    argv = [rel(a) if Path(a).is_absolute() and Path(a).resolve().is_relative_to(REPO_ROOT) else a
            for a in [rel(sys.argv[0])] + sys.argv[1:]]
    gated = [c["id"] for c in configs if (r := load_json(out / "raw" / c["id"] / "rep_1.json"))
             and not r.get("scope_gate_passed")]
    lines = [
        "# GLASS Stage-1 batch evaluation — replication record", "",
        f"Generated automatically by `{rel(__file__)}` at the end of the run.", "",
        "## Reproduce", "", "```", "pip install -r requirements.txt",
        "# put OPENROUTER_API_KEY (or OPENAI_API_KEY) in goal_to_parameters/.env",
        f"git checkout {git('rev-parse', 'HEAD')}",
        *([f"git apply {rel(out / PATCH_NAME)}"] if patch else []),
        "# plus run_stage1_batch.py, stage1_config.json and input_logs/ matching the SHA-256 below",
        "python " + " ".join(argv),
        f"python {rel(Path(__file__).with_name('verify_stage1_results.py'))}", "```", "",
        "## Run", "",
        f"- Repository commit: `{git('rev-parse', 'HEAD')}`",
        f"- Uncommitted changes in {rel(BASE)}/ and goal_to_parameters/ at run time: "
        + (", ".join(f"`{l}`" for l in git("status", "--porcelain", "--", rel(BASE), "goal_to_parameters").splitlines()) or "none"),
        f"- Command line: `{Path(sys.executable).name} {' '.join(argv)}`",
        f"- Provider / model string: `{type(provider).__name__}` / `{provider.get_model_name()}`",
        f"- Temperature: {temperature} (app.py {APP_PAGE_FN}: `temperature = {app_temperature()}`), json_mode: {APP_JSON_MODE}",
        f"- num_kpis: {settings['num_kpis']}, use_log_evidence: {settings['use_log_evidence']}",
        f"- Repetitions: {args.reps}; configurations: {len(configs)}",
        f"- This invocation: start {started}, end {ended}",
        f"- Earliest / latest rep timestamp on disk: {min(stamps, default='')} / {max(stamps, default='')}",
        f"- Python {platform.python_version()}; openai {version('openai')}; pydantic {version('pydantic')}", "",
        "## GLASS code: base commit + local patch", "",
        f"- Base commit: `{git('rev-parse', 'HEAD')}`",
        (f"- Local patch: `{rel(out / PATCH_NAME)}` (= `git diff -- goal_to_parameters`), "
         f"SHA-256 `{sha256(patch)}`" if patch else "- Local patch: none (goal_to_parameters/ equals the base commit)"),
        f"- Parallel workers: {args.workers} (thread pool; one provider instance per worker)", "",
        "## Deviation from the UI: scope gate", "",
        "The app's Generate button runs `validate_generation_scope` (a keyword gate) before",
        "`handle_generation`. The batch records its verdict per rep (`scope_gate_passed`,",
        "`scope_gate_message`) but always runs the Stage-1 LLM path. Configurations whose goal fails",
        f"the gate ({', '.join(gated) or 'none'}) would get an error and no KPIs in the UI.", "",
        "## Files (SHA-256)", "",
        f"- `{rel(CONFIG_PATH)}`: `{sha256(CONFIG_PATH.read_bytes())}`",
        f"- `{rel(__file__)}`: `{sha256(Path(__file__).read_bytes())}`",
        f"- `goal_to_parameters/app.py`: `{sha256(APP_PATH.read_bytes())}`",
        *[f"- `{rel(args.logs / name)}`: `{sha256((args.logs / name).read_bytes())}`" for name in logs], "",
        "## app.py Stage-1 code executed (compiled from app.py source; SHA-256 of each source text)", "",
        *[f"- `{name}`: `{sha256(text)}`" for name, text in app_sources.items()], "",
        "## Scoring rules (copied from the script header)", "", "```" + rules.rstrip(), "```", "",
    ]
    (out / "README.md").write_text("\n".join(lines), encoding="utf-8")


# -------------------------------------------------------------------- main ---

class FatalAPIError(Exception):
    """Authentication / permission / model-not-found error: no rep can succeed, stop the batch."""


def make_provider(model: str):
    if os.getenv("OPENAI_API_KEY"):
        from llm.openai_provider import OpenAIProvider
        return OpenAIProvider(api_key=os.environ["OPENAI_API_KEY"], model=model)
    if os.getenv("OPENROUTER_API_KEY"):
        from llm.openrouter_provider import OpenRouterProvider
        return OpenRouterProvider(api_key=os.environ["OPENROUTER_API_KEY"], model=f"openai/{model}")
    sys.exit("No API key found: set OPENAI_API_KEY or OPENROUTER_API_KEY in goal_to_parameters/.env")


def prepare_config(app, cfg, description, log_path, settings):
    """Log profile + prompt package, built exactly as handle_generation builds them."""
    log_profile = log_evidence = context_evidence = None
    if settings["use_log_evidence"]:
        with open(log_path, "rb") as fh:
            log_profile, log_evidence, context_evidence = app["extract_log_artifacts"](fh)
    prompts = app["build_smart_kpi_prompt"](
        process_description=app["sanitize_user_input"](description),
        simulation_goal=app["sanitize_user_input"](cfg["simulation_goal"]),
        num_kpis=settings["num_kpis"], log_evidence=log_evidence, context_evidence=context_evidence)
    return log_profile, context_evidence, prompts


def run_rep(job: dict, ctx: dict) -> None:
    """One (configuration, rep): the app's Stage-1 path, recorded to <out>/raw/<id>/rep_<k>.json."""
    app, settings, cfg, rep = ctx["app"], ctx["settings"], job["cfg"], job["rep"]
    if getattr(_rep_state, "provider", None) is None:
        _rep_state.provider = make_provider(settings["model"])   # one provider instance per worker
    elif PAUSE_S:
        time.sleep(PAUSE_S)                                      # pause between this worker's calls
    provider = _rep_state.provider
    goal, description = cfg["simulation_goal"], ctx["descriptions"][cfg["process"]]
    log_profile, context_evidence, (system_prompt, few_shot, user_prompt) = ctx["prepared"][cfg["id"]]
    rec_provider = RecordingProvider(provider)
    t_start = datetime.now(timezone.utc).isoformat(timespec="seconds")
    t0 = time.time()
    result = raw_output = validation = error = None
    _rep_state.parse_warnings, _rep_state.attempt = [], 0
    # The UI's Generate button runs this keyword gate first; it is recorded, not enforced.
    scope_gate_message = app["validate_generation_scope"](description, goal)
    try:
        result, raw_output, validation = app["parse_with_retries"](
            provider=rec_provider, system_prompt=system_prompt, user_prompt=user_prompt,
            simulation_goal=goal, temperature=ctx["temperature"], log_profile=log_profile,
            context_evidence=context_evidence, few_shot_messages=few_shot, json_mode=APP_JSON_MODE)
        out_of_scope = not result.kpis and "out of scope" in result.simulation_goal_structured.lower()
        status = "out_of_scope" if out_of_scope else "ok"
    except app["KPIParsingError"] as exc:
        status, error, raw_output = "parse_error", str(exc), exc.raw_output
    except FATAL_API_ERRORS as exc:
        raise FatalAPIError(f"{job['prefix']} fatal API error: {exc}") from exc
    except LLMCallFailed as exc:
        status, error = "llm_error", str(exc)
    except Exception:
        status, error = "app_error", traceback.format_exc()
    elapsed = round(time.time() - t0, 2)
    parse_warnings, _rep_state.parse_warnings = _rep_state.parse_warnings, None   # stop capturing

    attempts = []
    for n, call in enumerate(rec_provider.calls, 1):
        outcome, attempt_error = classify_attempt(app, call["raw"], goal, log_profile, context_evidence)
        attempts.append({"attempt": n, "elapsed_s": call["elapsed_s"], "outcome": outcome, "error": attempt_error,
                         "user_prompt": None if call["user_prompt"] == user_prompt else call["user_prompt"],
                         "raw": call["raw"]})
    parsed = app["parse_kpi_generation_payload"](raw_output).model_dump(mode="python") if result else None
    final = result.model_dump(mode="python") if result else None
    write_json(job["path"], {
        "config_id": cfg["id"], "process": cfg["process"], "goal_config": cfg["goal_config"],
        "rep": rep, "status": status, "error": error,
        "provider": type(provider).__name__, "model": provider.get_model_name(),
        "temperature": ctx["temperature"], "json_mode": APP_JSON_MODE, "num_kpis": settings["num_kpis"],
        "use_log_evidence": settings["use_log_evidence"],
        "event_log": cfg["event_log"], "event_log_sha256": ctx["log_sha"][cfg["id"]],
        "script_sha256": ctx["script_sha"], "app_py_sha256": ctx["app_sha"],
        "glass_patch_sha256": ctx["patch_sha"], "worker": threading.current_thread().name,
        "timestamp": t_start, "elapsed_s": elapsed, "simulation_goal": goal,
        "scope_gate_passed": scope_gate_message is None, "scope_gate_message": scope_gate_message,
        "raw": raw_output, "attempts": attempts,
        "parse_warnings": parse_warnings,   # GLASS parsing warnings per LLM call (evidence_basis normalisation)
        "result_parsed": parsed,   # parse_kpi_generation_payload(raw), before _finalize_generated_result
        "result": final,           # what the app delivers
        "measurable_as_trace": [{"name": f["name"], "measurable_as_raw": p["measurable_as"],
                                 "measurable_as": f["measurable_as"]}
                                for p, f in zip(parsed["kpis"], final["kpis"])] if result else [],
        "fill_warnings": [w for w in (validation or {}).get("issues", []) if w.get("code") == "inferred_measurable_as"],
        "semantic_validation": validation,
        "system_prompt": system_prompt, "few_shot_messages": few_shot, "user_prompt": user_prompt,
    })
    detail = f"{len(result.kpis)} KPIs" if result else (error or "").splitlines()[0][:80] if error else ""
    retry_note = f", {len(attempts)} attempts" if len(attempts) > 1 else ""
    log(f"{job['prefix']} {status} ({detail}{retry_note}, {elapsed:.1f} s)")


def main() -> None:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    settings = config["settings"]
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reps", type=int, default=settings["repetitions"])
    ap.add_argument("--only", help="run a single configuration id")
    ap.add_argument("--smoke", action="store_true", help="= --reps 1 --only LoanApp_G1")
    ap.add_argument("--out", type=Path, default=RESULTS_DIR / "stage1_results")
    ap.add_argument("--logs", type=Path, default=LOGS_DIR)
    ap.add_argument("--workers", type=int, default=1, help="parallel worker threads (one provider each)")
    args = ap.parse_args()
    if args.smoke:
        args.reps, args.only = 1, "LoanApp_G1"
    configs = config["configurations"]
    selected = [c for c in configs if args.only in (None, c["id"])]
    if not selected:
        sys.exit(f"Unknown configuration id: {args.only}")
    if missing := [c["event_log"] for c in selected if not (args.logs / c["event_log"]).exists()]:
        sys.exit(f"Event logs not found in {args.logs}: {', '.join(missing)}")
    temperature = settings["temperature"]
    if temperature != app_temperature():
        sys.exit(f"Config temperature {temperature} differs from app.py's {app_temperature()}")
    patch, patch_file = glass_patch(), args.out / PATCH_NAME
    if patch and (not patch_file.exists() or patch_file.read_bytes() != patch):
        sys.exit(f"goal_to_parameters/ has local changes that differ from {patch_file}; save them with\n"
                 f"  git diff --output={patch_file.relative_to(REPO_ROOT).as_posix()} -- goal_to_parameters")
    if not patch and patch_file.exists():
        sys.exit(f"{patch_file} exists but goal_to_parameters/ has no local changes")

    sys.stdout = _MuteCostTracker(sys.stdout)
    logging.getLogger("utils.parsing").addHandler(_ParseWarnings())
    app, app_sources = load_app_stage1()
    provider = make_provider(settings["model"])   # only to report; workers create their own
    log(f"Settings: {settings}  json_mode={APP_JSON_MODE}  workers={args.workers}")
    log(f"Provider: {type(provider).__name__}   model: {provider.get_model_name()}   "
        f"GLASS patch: {sha256(patch) if patch else 'none'}")

    jobs, total = [], len(selected) * args.reps
    for n, (cfg, rep) in enumerate(((c, r) for c in selected for r in range(1, args.reps + 1)), 1):
        path = args.out / "raw" / cfg["id"] / f"rep_{rep}.json"
        prefix = f"[{n}/{total}] {cfg['id']} rep {rep} ..."
        if (existing := load_json(path)) and existing.get("status") in FINAL_STATUSES:
            log(f"{prefix} skipped (exists: {existing['status']})")
        else:
            jobs.append({"cfg": cfg, "rep": rep, "path": path, "prefix": prefix})
    pending = {j["cfg"]["id"]: j["cfg"] for j in jobs}
    ctx = {
        "app": app, "settings": settings, "temperature": temperature,
        "descriptions": config["process_descriptions"],
        "prepared": {cid: prepare_config(app, c, config["process_descriptions"][c["process"]],
                                         args.logs / c["event_log"], settings) for cid, c in pending.items()},
        "log_sha": {cid: sha256((args.logs / c["event_log"]).read_bytes()) for cid, c in pending.items()},
        "script_sha": sha256(Path(__file__).read_bytes()), "app_sha": sha256(APP_PATH.read_bytes()),
        "patch_sha": sha256(patch) if patch else None,
    }
    with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="worker") as pool:
        futures = [pool.submit(run_rep, job, ctx) for job in jobs]
        try:
            for future in futures:
                future.result()
        except FatalAPIError as exc:
            pool.shutdown(wait=True, cancel_futures=True)
            sys.exit(f"{exc}\nAborted; reps written so far are kept, rerun to resume.")

    write_summaries(args.out, configs)
    write_readme(args.out, args, settings, configs, provider, temperature, app_sources, started,
                 datetime.now(timezone.utc).isoformat(timespec="seconds"), patch)
    log(f"\nWrote stage1_summary.csv, stage1_per_config.csv and README.md in {args.out}")


if __name__ == "__main__":
    main()

