"""
Post-hoc analysis of the number of evaluation seeds, from the run records (read-only).

Every Stage 2 run evaluated its initial and its final scenario on 10 seeds paired with the cached baseline
(seeds 42-51). For each KPI comparison and each k in 1..9, this script draws all k-subsets of the 10 paired
seeds (at most C(10,5) = 252, so no sampling is needed), recomputes the verdict on the subset and records how
often it agrees with the 10-seed verdict. It also computes the width of the 95 % confidence interval of the mean
KPI change on the subset.

Batches: LoanApp/P2P Stage 2 v1, v2, v3, v3.1, the v3.2 RAG comparison (V_full and V_no_kb) and the real-life
ablation (six variants). A comparison is one KPI of one 10-seed evaluation; a run's final evaluation counts only
if it is not the initial one (same_as_initial), runs without a final scenario contribute their initial only.

Verdict rules
  batch rule (primary)  the rule the batches used for `improved`:
                        - KPIs scored by GLASS: GLASS's direction rule on the means (minimize: proposed mean <
                          baseline mean; maximize: >; maintain: equal means, no tolerance was set);
                        - LoanApp/P2P utilization KPIs (scored_by utilization_rule): the target pool's
                          utilization, per seed in `evaluation.<phase>.utilization_rule`; improved = proposed mean
                          below the limit (0.85) and below the baseline mean.
                        The 10-seed verdict recomputed this way is checked against the stored `improved`.
  stated rule           improved = mean better in the KPI's direction and, for k >= 2, paired t-test p < 0.05;
                        for k = 1 the sign of the single difference. Applied to the KPI's own values; maintain KPIs
                        are left out of this rule.
CI width  paired t-interval of the mean change (proposed - baseline) on the k seeds, 2 * t(0.975, k-1) * s / sqrt(k),
          in % of the comparison's 10-seed baseline mean; per comparison averaged over its subsets, then the median
          and interquartile range over comparisons (k >= 2; a comparison with baseline mean 0 is left out).

Writes results/seed_analysis.csv, results/seed_analysis.md and results/seed_analysis.png.
"""
from __future__ import annotations

import csv
import itertools
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import stats

BASE = Path(__file__).resolve().parents[1]
RES = BASE / "results"
RL = BASE.parent / "real_life_ablation" / "results" / "stage2" / "raw"
BATCHES = [
    ("Stage 2 v1", [RES / "stage2_results" / "raw"]),
    ("Stage 2 v2", [RES / "stage2_results_v2" / "raw"]),
    ("Stage 2 v3", [RES / "stage2_results_v3" / "raw"]),
    ("Stage 2 v3.1", [RES / "stage2_results_v3_1" / "raw"]),
    ("v3.2 RAG comparison", [RES / "stage2_results_v32_rag" / "V_full" / "raw", RES / "stage2_results_v32_rag" / "V_no_kb" / "raw"]),
    ("Real-life ablation", [RL]),
]
KS = range(1, 11)
ALPHA = 0.05


def comparisons():
    """Yield one dict per KPI comparison (10 paired values)."""
    for batch, roots in BATCHES:
        for root in roots:
            for path in sorted(root.glob("*/rep_*.json")):
                rec = json.loads(path.read_text(encoding="utf-8"))
                evs = rec.get("evaluation") or {}
                for phase in ("initial", "final"):
                    ev = evs.get(phase) or {}
                    if phase == "final" and ev.get("same_as_initial"):
                        continue
                    util = ev.get("utilization_rule") or {}
                    for k in ev.get("kpis") or []:
                        b, p = k.get("baseline_values") or [], k.get("proposed_values") or []
                        if len(b) != 10 or len(p) != 10 or None in b or None in p:
                            continue
                        c = {"batch": batch, "record": path.relative_to(BASE.parent).as_posix(), "phase": phase,
                             "kpi": k["kpi"], "direction": k["direction"], "scored_by": k.get("scored_by"),
                             "b": np.array(b, float), "p": np.array(p, float), "stored": k.get("improved")}
                        if c["scored_by"] == "utilization_rule":
                            c["ub"] = np.array(util["baseline_per_seed"], float)
                            c["up"] = np.array(util["proposed_per_seed"], float)
                            c["limit"] = util["limit"]
                        yield c


def batch_verdict(c, idx) -> bool:
    if c["scored_by"] == "utilization_rule":
        mb, mp = c["ub"][idx].mean(), c["up"][idx].mean()
        return bool(mp < c["limit"] and mp < mb)
    mb, mp = c["b"][idx].mean(), c["p"][idx].mean()
    if c["direction"] == "minimize":
        return bool(mp < mb)
    if c["direction"] == "maximize":
        return bool(mp > mb)
    return bool(abs(mp - mb) < 1e-9)          # maintain without tolerance


def stated_verdict(c, idx) -> bool:
    d = c["p"][idx] - c["b"][idx]
    better = d.mean() < 0 if c["direction"] == "minimize" else d.mean() > 0
    if len(idx) == 1:
        return bool(better)
    if not better:
        return False
    with np.errstate(all="ignore"):
        pv = stats.ttest_rel(c["p"][idx], c["b"][idx]).pvalue
    return bool(pv < ALPHA) if not math.isnan(pv) else False


def ci_width_pct(c, idx) -> float | None:
    k = len(idx)
    base = abs(c["b"].mean())
    if k < 2 or base == 0:
        return None
    d = c["p"][idx] - c["b"][idx]
    half = stats.t.ppf(0.975, k - 1) * d.std(ddof=1) / math.sqrt(k)
    return 2 * half / base * 100


def main() -> None:
    subsets = {k: [np.array(s) for s in itertools.combinations(range(10), k)] for k in KS}
    rows, mismatch, n_by = [], [], defaultdict(int)
    per = []   # per comparison: batch, ref verdicts, agreement per k, width per k
    for c in comparisons():
        full = np.arange(10)
        ref = batch_verdict(c, full)
        if c["stored"] is not None and ref != bool(c["stored"]):
            mismatch.append((c["record"], c["phase"], c["kpi"]))
        use_stated = c["direction"] in ("minimize", "maximize")
        ref_s = stated_verdict(c, full) if use_stated else None
        item = {"batch": c["batch"], "ref": ref, "ref_s": ref_s, "agree": {}, "agree_s": {}, "width": {}}
        d = c["p"] - c["b"]
        base = abs(c["b"].mean())
        for k in KS:
            subs = subsets[k]
            item["agree"][k] = sum(batch_verdict(c, s) == ref for s in subs) / len(subs)
            D = d[np.stack(subs)]                      # subsets x k, the paired differences
            mean = D.mean(axis=1)
            if k >= 2:
                sd = D.std(axis=1, ddof=1)
                se = sd / math.sqrt(k)
                with np.errstate(all="ignore"):
                    pv = 2 * stats.t.sf(np.abs(mean / se), k - 1)   # paired t-test, as scipy.stats.ttest_rel
                pv = np.where(np.isnan(pv), 1.0, pv)
                width = 2 * stats.t.ppf(0.975, k - 1) * se / base * 100 if base else None
                item["width"][k] = float(width.mean()) if base else None
            else:
                pv = np.zeros(len(subs))
                item["width"][k] = None
            if use_stated:
                better = mean < 0 if c["direction"] == "minimize" else mean > 0
                verdict = better & (pv < ALPHA) if k >= 2 else better
                item["agree_s"][k] = float((verdict == ref_s).mean())
        per.append(item)
        n_by[c["batch"]] += 1

    groups = [b for b, _ in BATCHES] + ["All batches"]
    for g in groups:
        items = [i for i in per if g == "All batches" or i["batch"] == g]
        for k in KS:
            ag = [i["agree"][k] for i in items]
            ag_imp = [i["agree"][k] for i in items if i["ref"]]
            ag_not = [i["agree"][k] for i in items if not i["ref"]]
            ags = [i["agree_s"][k] for i in items if i["ref_s"] is not None]
            ws = sorted(i["width"][k] for i in items if i["width"][k] is not None)
            q = (np.percentile(ws, [25, 50, 75]) if ws else [None] * 3)
            rnd = lambda x, n=4: round(float(x), n) if x is not None else ""
            rows.append({
                "batch": g, "k": k, "kpi_comparisons": len(items),
                "subsets_per_comparison": math.comb(10, k),
                "agreement_batch_rule": rnd(statistics.mean(ag)) if ag else "",
                "agreement_when_10seed_improved": rnd(statistics.mean(ag_imp)) if ag_imp else "",
                "agreement_when_10seed_not_improved": rnd(statistics.mean(ag_not)) if ag_not else "",
                "comparisons_10seed_improved": len(ag_imp),
                "agreement_stated_rule": rnd(statistics.mean(ags)) if ags else "",
                "comparisons_stated_rule": len(ags),
                "ci_width_pct_median": rnd(q[1], 2) if ws else "",
                "ci_width_pct_q1": rnd(q[0], 2) if ws else "",
                "ci_width_pct_q3": rnd(q[2], 2) if ws else "",
                "comparisons_with_ci": len(ws),
            })

    with open(RES / "seed_analysis.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    write_md(rows, n_by, mismatch, len(per))
    plot(rows)
    print(f"{len(per)} comparisons; stored-verdict mismatches at 10 seeds: {len(mismatch)}")


def write_md(rows, n_by, mismatch, total) -> None:
    get = lambda g, k, col: next(r[col] for r in rows if r["batch"] == g and r["k"] == k)
    groups = list(dict.fromkeys(r["batch"] for r in rows))
    pct = lambda x: f"{x * 100:.1f}" if x != "" else "–"
    L = ["# How many evaluation seeds? A post-hoc analysis", "",
         "For every Stage 2 run with a 10-seed evaluation, every KPI comparison (the initial and, if different, the "
         "final scenario against the cached baseline, paired by seed) was re-evaluated on every k-subset of the "
         "10 seeds (k = 1 … 9; all C(10, k) subsets). Agreement = share of subsets whose verdict equals the "
         "10-seed verdict, averaged over comparisons. Script: `scripts/seed_analysis.py`; per batch and k: "
         "`seed_analysis.csv`; plot: `seed_analysis.png`.", "",
         "- **Batch rule (primary)** — the rule the batches stored as `improved`: direction of the mean change "
         "(GLASS), and for the LoanApp/P2P utilization KPIs the utilization rule (target-pool utilization below 0.85 "
         "and below the baseline). Recomputed at 10 seeds it matches the stored verdict in "
         f"{total - len(mismatch)} of {total} comparisons.",
         "- **Stated rule (secondary)** — improved = mean better in the KPI's direction and paired t-test p < 0.05 "
         "(k ≥ 2); the sign of the single difference for k = 1. The 10-seed reference uses the same rule. Maintain "
         "KPIs are left out of this rule.",
         "- **CI width** — width of the paired 95 % t-interval of the mean change, in % of the comparison's 10-seed "
         "baseline mean; per comparison the mean over its subsets, then the median (IQR) over comparisons.", "",
         "## Comparisons", "", "| Batch | KPI comparisons | 10-seed verdict improved (batch rule) |", "|---|---|---|"]
    for g in groups:
        L.append(f"| {g} | {get(g, 10, 'kpi_comparisons')} | {get(g, 10, 'comparisons_10seed_improved')} |")
    L += ["", "## Agreement with the 10-seed verdict, batch rule (%)", "",
          "| Batch | " + " | ".join(f"k={k}" for k in range(1, 10)) + " |", "|---|" + "---|" * 9]
    for g in groups:
        L.append(f"| {g} | " + " | ".join(pct(get(g, k, "agreement_batch_rule")) for k in range(1, 10)) + " |")
    L += ["", "Split by the 10-seed verdict (all batches):", "",
          "| 10-seed verdict | " + " | ".join(f"k={k}" for k in range(1, 10)) + " |", "|---|" + "---|" * 9,
          "| improved | " + " | ".join(pct(get("All batches", k, "agreement_when_10seed_improved")) for k in range(1, 10)) + " |",
          "| not improved | " + " | ".join(pct(get("All batches", k, "agreement_when_10seed_not_improved")) for k in range(1, 10)) + " |",
          "", "## Agreement with the 10-seed verdict, stated rule (%)", "",
          "| Batch | " + " | ".join(f"k={k}" for k in range(1, 10)) + " |", "|---|" + "---|" * 9]
    for g in groups:
        L.append(f"| {g} | " + " | ".join(pct(get(g, k, "agreement_stated_rule")) for k in range(1, 10)) + " |")
    L += ["", "## Width of the 95 % CI of the mean KPI change (% of the baseline mean, median [IQR])", "",
          "| Batch | " + " | ".join(f"k={k}" for k in range(2, 11)) + " |", "|---|" + "---|" * 9]
    for g in groups:
        L.append(f"| {g} | " + " | ".join(
            f"{get(g, k, 'ci_width_pct_median')} [{get(g, k, 'ci_width_pct_q1')}–{get(g, k, 'ci_width_pct_q3')}]"
            for k in range(2, 11)) + " |")
    a = lambda k: pct(get("All batches", k, "agreement_batch_rule"))
    L += ["", "## Findings", "",
          f"- **One seed decides like ten in {a(1)} % of the comparisons** (batch rule, all batches); five seeds in "
          f"{a(5)} %, nine in {a(9)} %. The LoanApp/P2P batches agree more (k = 1: 89–93 %) than the real-life "
          f"ablation (k = 1: {pct(get('Real-life ablation', 1, 'agreement_batch_rule'))} %), where fewer comparisons are "
          f"improved at 10 seeds ({get('Real-life ablation', 10, 'comparisons_10seed_improved')} of "
          f"{get('Real-life ablation', 10, 'kpi_comparisons')}).",
          f"- **Disagreements fall mostly on comparisons that are not improved at 10 seeds:** at k = 1, "
          f"{pct(get('All batches', 1, 'agreement_when_10seed_not_improved'))} % agreement there against "
          f"{pct(get('All batches', 1, 'agreement_when_10seed_improved'))} % for improved ones; a single seed tends to "
          "report an improvement that the 10-seed mean does not confirm.",
          "- **With the significance test (stated rule) k = 2 is worse than k = 1:** a paired t-test on two seeds has one "
          "degree of freedom and almost never reaches p < 0.05, so nearly every comparison is 'not improved'. From "
          "k = 3 on, the stated rule agrees more often than the batch rule (all batches).",
          f"- **The CI of the mean change narrows from {get('All batches', 2, 'ci_width_pct_median')} % of the baseline "
          f"at k = 2 to {get('All batches', 5, 'ci_width_pct_median')} % at k = 5 and "
          f"{get('All batches', 10, 'ci_width_pct_median')} % at k = 10** (medians, all batches)."]
    if mismatch:
        L += ["", "## Stored verdicts not reproduced at 10 seeds", ""] + [f"- `{r}` {p}: {k}" for r, p, k in mismatch]
    (RES / "seed_analysis.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def plot(rows) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ink, ink2, surface, grid = "#0b0b0b", "#52514e", "#fcfcfb", "#e4e3df"
    series = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]   # fixed categorical order
    markers = ["o", "s", "^", "D", "v", "P"]
    groups = list(dict.fromkeys(r["batch"] for r in rows))
    val = lambda g, k, col: next(r[col] for r in rows if r["batch"] == g and r["k"] == k)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.8), facecolor=surface)
    for ax in (a1, a2):
        ax.set_facecolor(surface)
        ax.grid(True, color=grid, linewidth=0.8)
        ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            ax.spines[s].set_color(ink2)
        ax.tick_params(colors=ink2)
    ks = list(range(1, 10))
    for i, g in enumerate(groups[:-1]):
        a1.plot(ks, [val(g, k, "agreement_batch_rule") * 100 for k in ks], color=series[i], linewidth=2,
                marker=markers[i], markersize=6, label=g)
    a1.plot(ks, [val("All batches", k, "agreement_batch_rule") * 100 for k in ks], color=ink, linewidth=2.6,
            marker="o", markersize=7, label="All batches")
    a1.plot(ks, [val("All batches", k, "agreement_stated_rule") * 100 for k in ks], color=ink, linewidth=2,
            linestyle="--", label="All batches, stated rule (p < 0.05)")
    a1.annotate(f"{val('All batches', 1, 'agreement_batch_rule') * 100:.0f} %", (1, val("All batches", 1, "agreement_batch_rule") * 100),
                textcoords="offset points", xytext=(6, -14), color=ink, fontsize=9)
    a1.annotate(f"{val('All batches', 9, 'agreement_batch_rule') * 100:.0f} %", (9, val("All batches", 9, "agreement_batch_rule") * 100),
                textcoords="offset points", xytext=(9, -4), color=ink, fontsize=9)
    a1.set_xlabel("seeds k", color=ink)
    a1.set_ylabel("agreement with the 10-seed verdict (%)", color=ink)
    a1.set_title("Verdict agreement with the 10-seed verdict", color=ink, loc="left", fontsize=11)
    a1.set_xticks(ks)
    ks2 = list(range(2, 11))
    for i, g in enumerate(groups[:-1]):
        a2.plot(ks2, [val(g, k, "ci_width_pct_median") for k in ks2], color=series[i], linewidth=2,
                marker=markers[i], markersize=6, label=g)
    med = [val("All batches", k, "ci_width_pct_median") for k in ks2]
    a2.fill_between(ks2, [val("All batches", k, "ci_width_pct_q1") for k in ks2],
                    [val("All batches", k, "ci_width_pct_q3") for k in ks2], color=ink, alpha=0.08, linewidth=0,
                    label="All batches, IQR")
    a2.plot(ks2, med, color=ink, linewidth=2.6, marker="o", markersize=7, label="All batches, median")
    for k in (2, 5, 10):
        a2.annotate(f"{med[k - 2]:.1f} %", (k, med[k - 2]), textcoords="offset points", xytext=(6, 6), color=ink,
                    fontsize=9)
    a2.set_xlabel("seeds k", color=ink)
    a2.set_ylabel("95 % CI width of the mean change (% of baseline)", color=ink)
    a2.set_title("Width of the 95 % CI (median per batch; gray: IQR, all batches)", color=ink, loc="left", fontsize=11)
    a2.set_xticks(ks2)
    a2.set_ylim(bottom=0)
    h, l = a1.get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", ncol=4, frameon=False, fontsize=9, labelcolor=ink)
    fig.tight_layout(rect=(0, 0.12, 1, 1))
    fig.savefig(RES / "seed_analysis.png", dpi=150, facecolor=surface)


if __name__ == "__main__":
    main()
