"""Shared KPI comparison rendering components for Streamlit panels."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

import pandas as pd
import streamlit as st

from second_llm.kpi_computation import ComputedKPI
from second_llm.scenario_evaluation import (
    EvaluationSummary,
    KPIComparisonEntry,
    OverallStatus,
)

if TYPE_CHECKING:
    from second_llm.multi_seed_evaluation import MultiSeedKPIComparison

STATUS_ICONS = {
    OverallStatus.IMPROVED: "✅",
    OverallStatus.WORSENED: "❌",
    OverallStatus.TRADE_OFF_DETECTED: "⚠️",
    OverallStatus.INCONCLUSIVE: "❓",
    OverallStatus.INVALID: "⛔",
}

STATUS_LABELS = {
    OverallStatus.IMPROVED: "Scenario Improved",
    OverallStatus.WORSENED: "Scenario Worsened",
    OverallStatus.TRADE_OFF_DETECTED: "Trade-off Detected",
    OverallStatus.INCONCLUSIVE: "Inconclusive",
    OverallStatus.INVALID: "Invalid Scenario",
}

_STATUS_COLORS = {
    OverallStatus.IMPROVED: "#d4edda",
    OverallStatus.WORSENED: "#f8d7da",
    OverallStatus.TRADE_OFF_DETECTED: "#fff3cd",
    OverallStatus.INCONCLUSIVE: "#e2e3e5",
    OverallStatus.INVALID: "#f8d7da",
}


def render_summary_card(summary: EvaluationSummary) -> None:
    """Render the evaluation summary as a highlighted card."""
    status = summary.overall_status
    icon = STATUS_ICONS.get(status, "")
    label = STATUS_LABELS.get(status, str(status.value))
    bg_color = _STATUS_COLORS.get(status, "#e2e3e5")

    st.markdown(
        f"""
        <div style="background:{bg_color}; border-radius:10px; padding:20px 28px; margin-bottom:16px;">
            <div style="font-size:2rem; font-weight:700; margin-bottom:4px;">
                {icon} {label}
            </div>
            <div style="display:flex; gap:32px; margin-top:12px;">
                <div>
                    <span style="font-size:0.8rem; text-transform:uppercase; color:#555;">All Targets Improved</span><br/>
                    <span style="font-size:1.3rem; font-weight:600;">
                        {"Yes" if summary.target_kpis_improved else "Partial / No" if summary.target_kpis_improved is False else "N/A"}
                    </span>
                </div>
                <div>
                    <span style="font-size:0.8rem; text-transform:uppercase; color:#555;">Safeguards Respected</span><br/>
                    <span style="font-size:1.3rem; font-weight:600;">
                        {"Yes" if summary.safeguards_respected else "No" if summary.safeguards_respected is False else "N/A"}
                    </span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if summary.trade_offs:
        st.warning("**Trade-offs detected:**\n" + "\n".join(
            f"- {t}" for t in summary.trade_offs
        ))

    if summary.recommendation:
        rec_map = {
            "scenario_accepted": ("The proposed scenario achieves its targets. Consider deploying.", "success"),
            "scenario_rejected": ("The proposed scenario worsens KPIs. Revise the scenario.", "error"),
            "needs_human_review": ("The scenario has mixed results. Review trade-offs before deciding.", "info"),
            "invalid_scenario": ("The scenario could not be properly evaluated.", "error"),
        }
        msg, msg_type = rec_map.get(summary.recommendation, (summary.recommendation, "info"))
        if msg_type == "success":
            st.success(f"**Recommendation:** {msg}")
        elif msg_type == "error":
            st.error(f"**Recommendation:** {msg}")
        else:
            st.info(f"**Recommendation:** {msg}")


def render_all_kpis_comparison(
    baseline_kpis: KPIComputationResult,
    proposed_kpis: KPIComputationResult,
) -> None:
    """Render a full side-by-side comparison of every computed KPI (not just targets).

    Shows all KPIs from compute_kpis() — cycle time, waiting time, processing
    time, throughput, utilization, cost, and every per-activity waiting time —
    with absolute and % change, sorted by largest absolute % change first.
    """
    from second_llm.kpi_computation import KPIComputationResult as _KCR  # noqa: F401

    baseline_map = {k.name: k for k in baseline_kpis.kpis}
    proposed_map = {k.name: k for k in proposed_kpis.kpis}
    all_names = sorted(
        set(baseline_map) | set(proposed_map),
        key=lambda n: n,
    )

    rows: list[dict[str, Any]] = []
    for name in all_names:
        b = baseline_map.get(name)
        p = proposed_map.get(name)
        b_val = b.value if b else None
        p_val = p.value if p else None
        unit = (b or p).unit if (b or p) else ""
        category = (b or p).category if (b or p) else ""

        abs_change: float | None = None
        pct_change: float | None = None
        if b_val is not None and p_val is not None:
            abs_change = round(p_val - b_val, 4)
            if b_val != 0:
                pct_change = round((abs_change / abs(b_val)) * 100, 2)

        # Extract detail fields if available
        b_details = b.details if b else {}
        p_details = p.details if p else {}

        row: dict[str, Any] = {
            "KPI": name,
            "Category": category,
            "Unit": unit,
            "Baseline": f"{b_val:.4g}" if b_val is not None else "-",
            "Proposed": f"{p_val:.4g}" if p_val is not None else "-",
            "Δ": f"{abs_change:+.4g}" if abs_change is not None else "-",
            "% Δ": f"{pct_change:+.1f}%" if pct_change is not None else "-",
        }

        # Append percentile / seed-std details where available
        for label, key in (("Median (B)", "median"), ("P90 (B)", "p90"), ("Std (B)", "std")):
            val = b_details.get(key)
            row[label] = f"{val:.4g}" if val is not None else ""
        for label, key in (("Median (P)", "median"), ("P90 (P)", "p90"), ("Std (P)", "std")):
            val = p_details.get(key)
            row[label] = f"{val:.4g}" if val is not None else ""

        rows.append((abs(pct_change) if pct_change is not None else -1, row))

    # Sort by absolute % change descending
    rows.sort(key=lambda x: x[0], reverse=True)
    df = pd.DataFrame([r for _, r in rows])

    # Drop percentile columns if they're all empty
    for col in ("Median (B)", "P90 (B)", "Std (B)", "Median (P)", "P90 (P)", "Std (P)"):
        if col in df.columns and df[col].eq("").all():
            df.drop(columns=[col], inplace=True)

    st.dataframe(df, hide_index=True, use_container_width=True)

    # Per-resource utilization breakdown
    b_util = baseline_map.get("Resource Utilization")
    p_util = proposed_map.get("Resource Utilization")
    if b_util and p_util:
        b_res = b_util.details.get("per_resource", {})
        p_res = p_util.details.get("per_resource", {})
        all_resources = sorted(set(b_res) | set(p_res))
        if all_resources:
            st.caption("Resource utilization breakdown:")
            res_rows = []
            for r in all_resources:
                bv = b_res.get(r)
                pv = p_res.get(r)
                delta = round(pv - bv, 4) if bv is not None and pv is not None else None
                res_rows.append({
                    "Resource": r,
                    "Baseline": f"{bv:.1%}" if bv is not None else "-",
                    "Proposed": f"{pv:.1%}" if pv is not None else "-",
                    "Δ": f"{delta:+.1%}" if delta is not None else "-",
                })
            st.dataframe(pd.DataFrame(res_rows), hide_index=True, use_container_width=True)


def render_raw_kpi_details(kpis: list[ComputedKPI]) -> None:
    """Render detailed KPI values: mean, median, p90, per-resource breakdown."""
    if not kpis:
        st.info("No KPIs computed.")
        return

    summary_rows: list[dict[str, Any]] = []
    per_resource_kpi: ComputedKPI | None = None

    for k in kpis:
        d = k.details or {}
        row: dict[str, Any] = {
            "KPI": k.name,
            "Mean": f"{k.value} {k.unit}" if k.value is not None else "-",
        }
        if "median" in d:
            row["Median"] = f"{d['median']} {k.unit}"
        if "p90" in d:
            row["P90"] = f"{d['p90']} {k.unit}"
        if "max_utilization" in d:
            row["Max"] = f"{d['max_utilization']:.1%}"
            row["Min"] = f"{d['min_utilization']:.1%}"
        if "total_cost" in d:
            row["Total"] = f"{d['total_cost']} {k.unit}"
        if "total_cases" in d:
            row["Cases"] = str(d["total_cases"])
        if "simulation_days" in d:
            row["Days"] = str(d["simulation_days"])
        summary_rows.append(row)

        if "per_resource" in d and d["per_resource"]:
            per_resource_kpi = k

    st.dataframe(pd.DataFrame(summary_rows), hide_index=True, use_container_width=True)

    if per_resource_kpi is not None:
        per_res = per_resource_kpi.details["per_resource"]
        st.caption("Resource utilization breakdown:")
        res_rows = [
            {"Resource": r, "Utilization": v, "Utilization %": f"{v:.1%}"}
            for r, v in sorted(per_res.items(), key=lambda x: x[1], reverse=True)
        ]
        st.dataframe(
            pd.DataFrame(res_rows).drop(columns=["Utilization"]),
            hide_index=True,
            use_container_width=True,
        )


def render_per_seed_table(
    comparisons: list[MultiSeedKPIComparison],
    seeds_used: list[int],
) -> None:
    """Render per-seed raw values for each computed KPI."""
    computed = [c for c in comparisons if c.status == "computed"
                and c.baseline_stats and c.proposed_stats]
    if not computed:
        st.info("No per-seed data available.")
        return

    for comp in computed:
        b_vals = comp.baseline_stats.values  # type: ignore[union-attr]
        p_vals = comp.proposed_stats.values  # type: ignore[union-attr]
        n = min(len(b_vals), len(p_vals), len(seeds_used))
        rows = []
        for i in range(n):
            delta = round(p_vals[i] - b_vals[i], 4)
            rows.append({
                "Seed": seeds_used[i],
                "Baseline": round(b_vals[i], 4),
                "Proposed": round(p_vals[i], 4),
                "Delta": delta,
            })
        st.caption(f"**{comp.kpi_name}** ({comp.unit})")
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=False)


def render_comparison_table(entries: list[KPIComparisonEntry]) -> None:
    """Render the KPI comparison as a styled table."""
    if not entries:
        st.info("No KPI comparisons available.")
        return

    rows: list[dict[str, Any]] = []
    for e in entries:
        status_icon = ""
        if e.violated_safeguard:
            status_icon = "❌"
        elif e.improved is True:
            status_icon = "✅"
        elif e.improved is False:
            status_icon = "⚠️"
        else:
            status_icon = "➖"

        pct_str = f"{e.percentage_change:+.1f}%" if e.percentage_change is not None else "-"

        rows.append({
            "Status": status_icon,
            "KPI": e.kpi_name,
            "Category": e.category,
            "Direction": e.target_direction,
            "Baseline": f"{e.baseline_value:.2f}" if e.baseline_value is not None else "-",
            "Proposed": f"{e.proposed_value:.2f}" if e.proposed_value is not None else "-",
            "Change": f"{e.absolute_change:+.2f}" if e.absolute_change is not None else "-",
            "% Change": pct_str,
            "Safeguard": "Yes" if e.is_safeguard else "",
            "Notes": e.interpretation,
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, width="stretch", hide_index=True)


def render_kpi_chart(entries: list[KPIComparisonEntry]) -> None:
    """Render grouped bar charts comparing baseline vs proposed values."""
    import altair as alt

    computable = [
        e for e in entries
        if e.baseline_value is not None and e.proposed_value is not None
        and e.status == "computed"
    ]
    if not computable:
        return

    categories: dict[str, list[KPIComparisonEntry]] = {}
    for e in computable:
        cat = e.category or "other"
        categories.setdefault(cat, []).append(e)

    for cat, cat_entries in categories.items():
        if not cat_entries:
            continue

        unit = cat_entries[0].unit if cat_entries else ""
        caption = f"**{cat.title()}** KPIs ({unit})" if unit else f"**{cat.title()}** KPIs"

        sorted_entries = sorted(cat_entries, key=lambda e: max(e.baseline_value, e.proposed_value), reverse=True)
        groups: list[list[KPIComparisonEntry]] = []
        current_group: list[KPIComparisonEntry] = []

        for entry in sorted_entries:
            entry_max = max(entry.baseline_value, entry.proposed_value)
            if not current_group:
                current_group.append(entry)
            else:
                group_max = max(
                    max(e.baseline_value, e.proposed_value) for e in current_group
                )
                if group_max > 0 and entry_max > 0 and group_max / entry_max > 10:
                    groups.append(current_group)
                    current_group = [entry]
                else:
                    current_group.append(entry)

        if current_group:
            groups.append(current_group)

        st.caption(caption)

        for group in groups:
            rows = []
            for e in group:
                rows.append({"KPI": e.kpi_name, "Scenario": "Baseline", "Value": e.baseline_value})
                rows.append({"KPI": e.kpi_name, "Scenario": "Proposed", "Value": e.proposed_value})

            chart_df = pd.DataFrame(rows)

            chart = (
                alt.Chart(chart_df)
                .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                .encode(
                    x=alt.X("KPI:N", axis=alt.Axis(labelAngle=0, labelLimit=300), title=None),
                    y=alt.Y("Value:Q", title=unit or "Value"),
                    xOffset="Scenario:N",
                    color=alt.Color(
                        "Scenario:N",
                        scale=alt.Scale(
                            domain=["Baseline", "Proposed"],
                            range=["#4a90d9", "#2ecc71"],
                        ),
                        legend=alt.Legend(orient="top"),
                    ),
                    tooltip=["KPI:N", "Scenario:N", alt.Tooltip("Value:Q", format=",.2f")],
                )
                .properties(height=300)
            )

            st.altair_chart(chart, width="stretch")


def render_multi_seed_comparison_table(entries: list[MultiSeedKPIComparison]) -> None:
    """Render KPI comparison table with 95% CI columns."""
    from second_llm.multi_seed_evaluation import MultiSeedKPIComparison as _MS  # noqa: F401

    if not entries:
        st.info("No KPI comparisons available.")
        return

    sorted_entries = sorted(
        entries,
        key=lambda e: abs(e.mean_percentage_change) if e.mean_percentage_change is not None else -1,
        reverse=True,
    )

    rows: list[dict[str, Any]] = []
    for e in sorted_entries:
        if e.violated_safeguard:
            status_icon = "❌"
        elif e.improved is True:
            status_icon = "✅"
        elif e.improved is False:
            status_icon = "⚠️"
        else:
            status_icon = "➖"

        sig_icon = "✓" if e.statistically_significant is True else ("✗" if e.statistically_significant is False else "")
        p_val_str = f"{e.p_value:.3f}" if e.p_value is not None else "-"

        pct_str = f"{e.mean_percentage_change:+.1f}%" if e.mean_percentage_change is not None else "-"

        ci_str = "-"
        if e.ci_lower_change is not None and e.ci_upper_change is not None:
            ci_str = f"[{e.ci_lower_change:+.2f}, {e.ci_upper_change:+.2f}]"

        baseline_str = "-"
        if e.mean_baseline is not None and e.baseline_stats:
            baseline_str = f"{e.mean_baseline:.2f} ± {e.baseline_stats.std:.2f}"

        proposed_str = "-"
        if e.mean_proposed is not None and e.proposed_stats:
            proposed_str = f"{e.mean_proposed:.2f} ± {e.proposed_stats.std:.2f}"

        rows.append({
            "Status": status_icon,
            "KPI": e.kpi_name,
            "Category": e.category,
            "Direction": e.target_direction,
            "Baseline (mean±std)": baseline_str,
            "Proposed (mean±std)": proposed_str,
            "% Change": pct_str,
            "95% CI (Δ)": ci_str,
            "p-value": p_val_str,
            "Sig.": sig_icon,
            "Safeguard": "Yes" if e.is_safeguard else "",
        })

    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    st.caption("Sig. = statistically significant (paired t-test, p < 0.05). ✓ = significant, ✗ = not significant. p-value shown to 3 decimal places.")


def render_multi_seed_chart(entries: list[MultiSeedKPIComparison]) -> None:
    """Render grouped bar chart with error bars for multi-seed results."""
    import altair as alt
    from second_llm.multi_seed_evaluation import MultiSeedKPIComparison as _MS  # noqa: F401

    computable = [
        e for e in entries
        if e.mean_baseline is not None and e.mean_proposed is not None
        and e.status == "computed"
    ]
    if not computable:
        return

    categories: dict[str, list[MultiSeedKPIComparison]] = {}
    for e in computable:
        cat = e.category or "other"
        categories.setdefault(cat, []).append(e)

    for cat, cat_entries in categories.items():
        unit = cat_entries[0].unit if cat_entries else ""
        caption = f"**{cat.title()}** KPIs ({unit})" if unit else f"**{cat.title()}** KPIs"

        # Split entries into scale-compatible groups (ratio > 10x → separate chart)
        sorted_cat = sorted(cat_entries, key=lambda e: max(e.mean_baseline, e.mean_proposed), reverse=True)
        groups: list[list[MultiSeedKPIComparison]] = []
        current_group: list[MultiSeedKPIComparison] = []
        for entry in sorted_cat:
            entry_max = max(entry.mean_baseline, entry.mean_proposed)
            if not current_group:
                current_group.append(entry)
            else:
                group_max = max(max(e.mean_baseline, e.mean_proposed) for e in current_group)
                if group_max > 0 and entry_max > 0 and group_max / entry_max > 10:
                    groups.append(current_group)
                    current_group = [entry]
                else:
                    current_group.append(entry)
        if current_group:
            groups.append(current_group)

        st.caption(caption)

        for group in groups:
            rows = []
            for e in group:
                b_lo = e.baseline_stats.ci_lower if e.baseline_stats else e.mean_baseline
                b_hi = e.baseline_stats.ci_upper if e.baseline_stats else e.mean_baseline
                p_lo = e.proposed_stats.ci_lower if e.proposed_stats else e.mean_proposed
                p_hi = e.proposed_stats.ci_upper if e.proposed_stats else e.mean_proposed
                rows += [
                    {"KPI": e.kpi_name, "Scenario": "Baseline", "Value": e.mean_baseline, "CI_Lower": b_lo, "CI_Upper": b_hi},
                    {"KPI": e.kpi_name, "Scenario": "Proposed", "Value": e.mean_proposed, "CI_Lower": p_lo, "CI_Upper": p_hi},
                ]

            chart_df = pd.DataFrame(rows)

            bars = (
                alt.Chart(chart_df)
                .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                .encode(
                    x=alt.X("KPI:N", axis=alt.Axis(labelAngle=0, labelLimit=300), title=None),
                    y=alt.Y("Value:Q", title=unit or "Value"),
                    xOffset="Scenario:N",
                    color=alt.Color(
                        "Scenario:N",
                        scale=alt.Scale(domain=["Baseline", "Proposed"], range=["#4a90d9", "#2ecc71"]),
                        legend=alt.Legend(orient="top"),
                    ),
                    tooltip=["KPI:N", "Scenario:N", alt.Tooltip("Value:Q", format=",.2f")],
                )
            )

            error_bars = (
                alt.Chart(chart_df)
                .mark_errorbar()
                .encode(
                    x=alt.X("KPI:N", title=None),
                    xOffset="Scenario:N",
                    y=alt.Y("CI_Lower:Q", title=unit or "Value"),
                    y2="CI_Upper:Q",
                    color=alt.Color("Scenario:N", legend=None),
                )
            )

            st.altair_chart((bars + error_bars).properties(height=300), width="stretch")
