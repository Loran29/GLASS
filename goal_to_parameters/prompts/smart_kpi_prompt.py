"""Prompt builder for SMART KPI generation with explicit schema and multi-turn few-shot examples."""

from __future__ import annotations

import json


REQUIRED_SCHEMA = """\
## Required JSON Schema

Your output must be a single JSON object with exactly these fields:

{
  "simulation_goal_structured": "<string - precise BPM-focused restatement of the goal>",
  "kpis": [
    {
      "name": "<string - short descriptive name>",
      "description": "<string - what the KPI measures>",
      "category": "<string - one of: time, cost, quality, utilization, throughput, compliance, flexibility>",
      "smart_breakdown": {
        "specific": "<string - what exactly is being measured and in which part of the process>",
        "measurable": "<string - metric, unit of measurement, and how it is quantified>",
        "achievable": "<string - why this target is realistic given the process context>",
        "relevant": "<string - how this KPI connects to the stated simulation goal>",
        "time_bound": "<string - measurement period relative to the simulation run, e.g. 'Evaluated across all simulated cases in the run'. Do not use real calendar periods.>"
      },
      "target_direction": "<string - one of: minimize, maximize, maintain>",
      "suggested_formula": "<string - case-level aggregation formula, e.g. AVG(...) across completed cases or COUNT(...) across the simulation run. No per-month or per-week suffixes.>",
      "supported_by_log": "<boolean - whether this KPI is directly or reasonably supportable by the available event-log evidence>",
      "evidence_basis": "<string - one of: process_description_only, event_log_only, both, proxy_from_log>",
      "process_scope": "<string - one of: end_to_end, subprocess, activity_level>",
      "context_segmentation": [
        {
          "condition": "<string - context condition such as customer_type = premium>",
          "target": "<string - relative improvement goal grounded in the evidence, e.g. 'below the observed baseline of 22h for this segment' or 'above current level'. Do not invent a specific number when observed_baseline is absent from the evidence.>",
          "rationale": "<string|null - optional short explanation>",
          "evidence_factor": "<string - REQUIRED when context_segmentation is non-empty: the context factor from the evidence>",
          "evidence_metric": "<string - REQUIRED when context_segmentation is non-empty: the metric from the evidence>",
          "adjusted_p_value": "<number|null - optional: adjusted p-value from the evidence, only when explicitly present>",
          "effect_size": "<number|null - optional: practical effect size from the evidence>",
          "sample_size": "<integer|null - optional: sample size from the evidence>",
          "observed_baseline": "<number|null - optional: observed baseline such as a median from the evidence>",
          "target_type": "<string|null - optional: label such as 'direct' or 'proxy'>"
        }
      ],
      "measurable_as": "<string|null - exact computed KPI name for simulation evaluation, or null>"
    }
  ],
  "reasoning": "<string - 2 to 4 sentences explaining why these KPIs fit the goal>"
}

Valid "category" values: "time", "cost", "quality", "utilization", "throughput", "compliance", "flexibility"
Valid "target_direction" values: "minimize", "maximize", "maintain"
Valid "evidence_basis" values: "process_description_only", "event_log_only", "both", "proxy_from_log"
Valid "process_scope" values: "end_to_end", "subprocess", "activity_level"

## Computable KPI names for measurable_as

The simulation evaluation engine computes only the following KPIs from a Prosimos event log.
You MUST use one of these exact names for "measurable_as", or set it to null:

Fixed KPIs (always computed when data is available):
- "Average Cycle Time"        — end-to-end or overall cycle/lead/turnaround time KPI. process_scope must be "end_to_end".  (category: time)
- "Average Waiting Time"      — ONLY for a KPI that measures overall case waiting time (the total time a case spends waiting across ALL activities). process_scope must be "end_to_end". Do NOT use this for a KPI that targets waiting time before a specific activity — use the activity-specific pattern below instead.  (category: time)
- "Average Processing Time"   — overall processing/service/activity-duration time KPI       (category: time)
- "Throughput"                — any completion rate, cases-per-day, or volume KPI               (category: throughput)
- "Resource Utilization"      — the GLOBAL average utilization across ALL resources in the simulation. This is a single number. If the goal names multiple roles separately (e.g. buyer utilization AND analyst utilization), assign "Resource Utilization" to at most ONE KPI (the primary or combined one) and set measurable_as to null for the others. Do NOT assign "Resource Utilization" to more than one KPI.  (category: utilization)
- "Cost per Case"             — any per-case cost or labor-cost KPI                             (category: cost)

Activity-specific waiting time KPIs (computed for each activity in the log):
- "{activity_name} Waiting Time" — waiting time before a SPECIFIC activity. Use the exact activity name from the process description. Any KPI with process_scope "activity_level" that measures waiting time MUST use this pattern, not "Average Waiting Time".
  e.g. "Triage Waiting Time" or "Physician Reassessment Waiting Time"                          (category: time)

Set "measurable_as" to null when:
- The KPI category is quality, compliance, or flexibility (no simulation equivalent exists).
- The KPI measures something not derivable from timestamps/resources in a simulated event log
  (e.g. rework rate, pass/fail rate, error rate, SLA compliance, first-contact resolution).
- The KPI is a subprocess time KPI with no matching single activity name.
- The KPI targets a specific resource role's utilization and another KPI already uses "Resource Utilization".

Category guidance:
- Use "time" for duration, waiting time, cycle time, delay, and elapsed-time measures.
- Use "cost" for monetary or effort-related consumption.
- Use "quality" for correctness, defect rate, pass quality, rework quality, or error-related quality.
- Use "utilization" for workload usage, allocation, occupancy, or productive-resource usage measures.
- Use "throughput" for counts, completion volume, flow rate, frequency, or processed-case volume.
- Use "compliance" for adherence to required checks, mandatory documentation, policy rules, or explicit control requirements.
- Use "flexibility" for adaptability, responsiveness, reassignment capability, or capacity-adjustment capability.

Field guidance:
- Use "supported_by_log" = true only when the KPI is directly measurable or reasonably proxied by the available event-log evidence.
- Use "evidence_basis" = "process_description_only" when no event log is available or the KPI is grounded only in the text.
- Use "evidence_basis" = "both" when the KPI is grounded in both the process description and the event log.
- Use "evidence_basis" = "event_log_only" only when the KPI depends entirely on event-log evidence.
- Use "evidence_basis" = "proxy_from_log" when the exact KPI is not directly present in the log but a supportable proxy is used.
- Use "process_scope" = "end_to_end" for whole-process KPIs, "subprocess" for a process segment, and "activity_level" for a single activity or tightly localized step.
- Use "context_segmentation" only when the provided context evidence shows an evidence-supported relationship that justifies different targets across context segments.
- When "context_segmentation" is non-empty, each segment MUST include "evidence_factor" (the context factor from the evidence) and "evidence_metric" (the metric from the evidence). These are required for traceability.
- When "context_segmentation" is non-empty, populate "adjusted_p_value", "effect_size", "sample_size", and "observed_baseline" from the provided context evidence only when those values are explicitly available.
- Use "adjusted_p_value" only when the context evidence explicitly provides an adjusted p-value. Do not relabel a raw "p_value" as an "adjusted_p_value".
- If a traceability field is not present in the supplied context evidence, omit it rather than inferring or inventing it. This applies especially to "adjusted_p_value", "effect_size", "sample_size", "observed_baseline", and "target_type" when it is not warranted.
- "target_type" may be set to "direct" or "proxy" when appropriate to indicate whether the segment target is based on a directly measured metric or a log-based proxy.
- When "context_segmentation" is non-empty, each segment "target" MUST describe a relative improvement goal grounded in the evidence. If "observed_baseline" is present for the segment, reference it explicitly (e.g. "below the observed median of 4.6h for this segment" or "above the observed baseline of 3.4h"). If "observed_baseline" is absent, use a directional phrase such as "below current baseline" or "above current level". Never invent a specific number that is not present in the evidence.
- Use an empty list for "context_segmentation" when no context-specific segmentation is warranted.
- If no accepted context relationship survives filtering, keep KPI titles and descriptions generic rather than implying unsupported context sensitivity.
- Use "measurable_as" to declare the exact computed KPI name the simulation evaluator should use. Choose from the list in "Computable KPI names for measurable_as" above. Set to null for quality, compliance, flexibility, or any KPI that cannot be measured from timestamps and resources alone.
"""


def _example_kpi(
    *,
    name: str,
    description: str,
    category: str,
    smart_breakdown: dict[str, str],
    target_direction: str,
    suggested_formula: str,
    process_scope: str,
    context_segmentation: list[dict[str, str | None]] | None = None,
    supported_by_log: bool = False,
    evidence_basis: str = "process_description_only",
    measurable_as: str | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "description": description,
        "category": category,
        "smart_breakdown": smart_breakdown,
        "target_direction": target_direction,
        "suggested_formula": suggested_formula,
        "supported_by_log": supported_by_log,
        "evidence_basis": evidence_basis,
        "process_scope": process_scope,
        "context_segmentation": context_segmentation or [],
        "measurable_as": measurable_as,
    }


# ---------------------------------------------------------------------------
# Few-shot example data
# ---------------------------------------------------------------------------

_EXAMPLE_1_PROCESS = (
    "The order fulfillment process starts when a customer places an order. "
    "The sales team verifies the order. The warehouse team picks and packs the items. "
    "A quality check is performed before shipping. The logistics team arranges delivery. "
    "The process ends when the customer confirms receipt."
)

_EXAMPLE_1_GOAL = "Reduce the overall order fulfillment cycle time while maintaining quality standards"

_EXAMPLE_1_OUTPUT = {
    "simulation_goal_structured": (
        "Reduce the end-to-end cycle time of the order fulfillment process "
        "from customer order placement to customer receipt confirmation, "
        "while maintaining quality performance at the pre-shipping quality check step"
    ),
    "kpis": [
        _example_kpi(
            name="Average Order Fulfillment Cycle Time",
            description="The average elapsed time from customer order placement to customer receipt confirmation across completed orders",
            category="time",
            smart_breakdown={
                "specific": "Measures the full duration of the order fulfillment process from customer order placement to customer receipt confirmation",
                "measurable": "Computed as the average elapsed time between order placement and receipt confirmation across completed orders. Unit: hours",
                "achievable": "Cycle time can be reduced through better coordination across order verification, warehouse handling, quality check, and delivery planning without changing the process structure",
                "relevant": "Directly measures the main objective of reducing the overall order fulfillment cycle time",
                "time_bound": "Evaluated across all simulated cases in the run",
            },
            target_direction="minimize",
            suggested_formula="AVG(receipt_confirmation_time - order_placement_time) across completed cases",
            process_scope="end_to_end",
            measurable_as="Average Cycle Time",
        ),
        _example_kpi(
            name="Quality Check Pass Rate",
            description="The percentage of orders that pass the quality check before shipping",
            category="quality",
            smart_breakdown={
                "specific": "Measures the share of orders that successfully pass the pre-shipping quality check",
                "measurable": "Computed as the number of orders passing the quality check divided by the total number of orders undergoing the quality check, multiplied by 100. Unit: percentage",
                "achievable": "Maintaining the current quality level is realistic while improving time performance in upstream and downstream process steps",
                "relevant": "Directly protects the quality constraint stated in the simulation goal",
                "time_bound": "Evaluated across all simulated cases in the run",
            },
            target_direction="maintain",
            suggested_formula="COUNT(orders_passing_quality_check) / COUNT(orders_undergoing_quality_check) * 100 across completed cases",
            process_scope="activity_level",
            measurable_as=None,
        ),
        _example_kpi(
            name="Average Warehouse Picking and Packing Time",
            description="The average time spent by the warehouse team on picking and packing items for an order",
            category="time",
            smart_breakdown={
                "specific": "Measures the average duration of the warehouse picking and packing activities performed for each order",
                "measurable": "Computed as the average time spent on picking and packing across completed warehouse handling instances. Unit: hours",
                "achievable": "Warehouse handling time can be improved through workload balancing, staffing adjustments, and better internal coordination",
                "relevant": "Targets a core subprocess that directly contributes to the overall fulfillment cycle time",
                "time_bound": "Evaluated across all simulated cases in the run",
            },
            target_direction="minimize",
            suggested_formula="AVG(warehouse_picking_and_packing_end_time - warehouse_picking_and_packing_start_time) across completed cases",
            process_scope="subprocess",
            measurable_as="Average Processing Time",
        ),
    ],
    "reasoning": (
        "This KPI set is aligned with both parts of the simulation goal: reducing end-to-end fulfillment time "
        "and maintaining quality standards. It includes one end-to-end time KPI to evaluate the main objective, "
        "one quality safeguard KPI to preserve the stated constraint, and one subprocess time KPI to support "
        "diagnosis of a likely operational bottleneck. All KPIs are grounded in activities explicitly described "
        "in the process and avoid introducing unsupported process elements or assumptions."
    ),
}

_EXAMPLE_2_PROCESS = (
    "The loan application process begins when a customer submits a loan request online. "
    "A loan officer reviews the application and requests additional documents if needed. "
    "The credit assessment team performs a credit check. The risk department evaluates the application. "
    "A senior manager approves or rejects the loan. If approved, the disbursement team processes the payment."
)
_EXAMPLE_2_GOAL = "Improve resource utilization of loan officers while reducing customer waiting time"

_EXAMPLE_2_OUTPUT = {
    "simulation_goal_structured": (
        "Increase the productive utilization of loan officers during application review while reducing the waiting time "
        "customers experience between processing steps in the loan application process"
    ),
    "kpis": [
        _example_kpi(
            name="Loan Officer Utilization Rate",
            description="The percentage of available working time that loan officers spend actively reviewing applications",
            category="utilization",
            smart_breakdown={
                "specific": "Measures utilization during the loan officer review activity",
                "measurable": "Computed as SUM(active review time) / SUM(available working time) * 100. Unit: percentage",
                "achievable": "Utilization can be improved through workload balancing and scheduling without changing the process design",
                "relevant": "Directly addresses the resource utilization objective",
                "time_bound": "Evaluated across all simulated cases in the run",
            },
            target_direction="maximize",
            suggested_formula="SUM(active_review_time) / SUM(available_time) * 100 over the simulation run",
            process_scope="activity_level",
            supported_by_log=True,
            evidence_basis="both",
            measurable_as="Resource Utilization",
        ),
        _example_kpi(
            name="Average Customer Waiting Time",
            description="The average total time a customer's application spends waiting between activities",
            category="time",
            smart_breakdown={
                "specific": "Measures waiting gaps between one activity ending and the next activity beginning across loan applications",
                "measurable": "Computed as AVG(sum of waiting periods per case). Unit: hours",
                "achievable": "Waiting time can be reduced through simulation of better resource allocation and sequencing",
                "relevant": "Directly addresses the waiting time reduction objective",
                "time_bound": "Evaluated across all simulated cases in the run",
            },
            target_direction="minimize",
            suggested_formula="AVG(SUM(waiting_periods_per_case)) across completed cases",
            process_scope="end_to_end",
            supported_by_log=True,
            evidence_basis="both",
            measurable_as="Average Waiting Time",
        ),
        _example_kpi(
            name="Application Throughput Rate",
            description="The total number of loan applications reaching a final decision across the simulation run",
            category="throughput",
            smart_breakdown={
                "specific": "Counts loan applications that reach a final decision outcome such as approval or rejection",
                "measurable": "Computed as COUNT(applications_with_final_decision) across the simulation run. Unit: total cases",
                "achievable": "Throughput can improve if utilization rises and waiting time falls without introducing unsupported process changes",
                "relevant": "Acts as a balancing KPI showing whether the process performs better overall while pursuing the stated goal",
                "time_bound": "Evaluated across all simulated cases in the run",
            },
            target_direction="maximize",
            suggested_formula="COUNT(applications_with_final_decision) across the simulation run",
            process_scope="end_to_end",
            supported_by_log=True,
            evidence_basis="both",
            measurable_as="Throughput",
        ),
    ],
    "reasoning": (
        "These KPIs align closely with the two explicit goal dimensions: loan officer utilization and customer waiting time. "
        "The throughput KPI complements them by showing whether those improvements translate into stronger overall process performance. "
        "This set also teaches that utilization, waiting time, and final-decision throughput can be grounded in both the textual process description "
        "and event-log evidence when activities, timestamps, and resources are available."
    ),
}

_EXAMPLE_3_PROCESS = (
    "Customer support ticket resolution process. A customer submits a support ticket through one of "
    "three channels: email, chat, or phone. An L1 support agent classifies the ticket by category and "
    "priority, then investigates the issue. The agent attempts to resolve the ticket directly. If the "
    "L1 agent cannot resolve the issue, the ticket is escalated to an L2 specialist who investigates "
    "further and applies a resolution. After resolution, the ticket is closed and a customer satisfaction "
    "survey is sent. The event log records case_id, activity, timestamps, support_channel (email, chat, "
    "phone), priority, and agent_id. Activities include Submit Ticket, Classify Ticket, L1 Investigation, "
    "L1 Resolution Attempt, Escalate to L2, L2 Investigation, L2 Resolution, Close Ticket, and "
    "Send Survey."
)

_EXAMPLE_3_GOAL = (
    "Reduce first-contact resolution failure rate while maintaining response time targets and "
    "minimizing escalation-related delays across support channels"
)

_EXAMPLE_3_CONTEXT_EVIDENCE = {
    "summary": {
        "case_level_factors": 1,
        "event_level_factors": 0,
        "temporal_factors": 0,
        "significant_relationships": 2,
    },
    "available_metrics": [
        "case_cycle_time_hours",
        "case_wait_time_hours",
        "activity_wait_time_hours",
    ],
    "significant_relationships": [
        {
            "factor": "support_channel",
            "factor_scope": "case_level",
            "metric": "case_cycle_time_hours",
            "metric_scope": "case_level",
            "activity": None,
            "test": "kruskal_wallis",
            "p_value": 0.003,
            "summary": "Case cycle time differs significantly across support_channel groups, with email cases showing a substantially higher median than chat or phone cases.",
            "segments": [
                {"condition": "support_channel = 'email'", "observed_median": 8.2, "sample_size": 156},
                {"condition": "support_channel = 'chat'", "observed_median": 3.4, "sample_size": 203},
                {"condition": "support_channel = 'phone'", "observed_median": 4.1, "sample_size": 178},
            ],
            "is_significant": True,
        },
        {
            "factor": "support_channel",
            "factor_scope": "case_level",
            "metric": "activity_wait_time_hours",
            "metric_scope": "activity_level",
            "activity": "L2 Investigation",
            "test": "kruskal_wallis",
            "p_value": 0.014,
            "summary": "Waiting time before L2 Investigation differs significantly across support_channel groups, with email-originated escalations waiting notably longer.",
            "segments": [
                {"condition": "support_channel = 'email'", "observed_median": 2.1, "sample_size": 67},
                {"condition": "support_channel = 'chat'", "observed_median": 0.8, "sample_size": 42},
                {"condition": "support_channel = 'phone'", "observed_median": 1.2, "sample_size": 54},
            ],
            "is_significant": True,
        },
    ],
    "filtered_out_factors": [
        "priority",
    ],
    "notes": [
        "Use only supported factors from the significant relationships when creating segmented KPIs.",
        "Preserve factor meaning in KPI names and segmentation wording.",
    ],
}

_EXAMPLE_3_OUTPUT = {
    "simulation_goal_structured": (
        "Reduce the rate of tickets requiring L2 escalation by improving first-contact resolution "
        "across support channels, while maintaining response time compliance and minimizing "
        "channel-specific delays in the escalation path"
    ),
    "kpis": [
        {
            "name": "First-Contact Resolution Proxy Rate by Support Channel",
            "description": "Percentage of tickets resolved without L2 escalation, segmented by support_channel to reflect channel-specific resolution patterns.",
            "category": "quality",
            "smart_breakdown": {
                "specific": "Measures the share of tickets closed without triggering Escalate to L2, separately for each support channel.",
                "measurable": "Computed as the percentage of completed cases with no Escalate to L2 activity, grouped by support_channel. Unit: percentage.",
                "achievable": "Improvement is realistic through better L1 knowledge bases and triage accuracy, tailored to channel-specific issue profiles.",
                "relevant": "Directly operationalizes the goal of reducing first-contact resolution failure rate.",
                "time_bound": "Evaluated across all simulated cases in the run",
            },
            "target_direction": "maximize",
            "suggested_formula": "count(cases without 'Escalate to L2') / count(all completed cases) * 100, grouped by support_channel",
            "supported_by_log": True,
            "evidence_basis": "proxy_from_log",
            "process_scope": "end_to_end",
            "measurable_as": None,
            "context_segmentation": [
                {
                    "condition": "support_channel = 'email'",
                    "target": "above current first-contact resolution baseline for email channel",
                    "rationale": "Context evidence shows email cases have the highest median cycle time, suggesting more complex issues and lower baseline first-contact resolution.",
                    "evidence_factor": "support_channel",
                    "evidence_metric": "case_cycle_time_hours",
                    "sample_size": 156,
                    "observed_baseline": 8.2,
                    "target_type": "proxy",
                },
                {
                    "condition": "support_channel = 'chat'",
                    "target": "above current first-contact resolution baseline for chat channel",
                    "rationale": "Context evidence shows chat cases have the lowest median cycle time, suggesting simpler issues more amenable to first-contact resolution.",
                    "evidence_factor": "support_channel",
                    "evidence_metric": "case_cycle_time_hours",
                    "sample_size": 203,
                    "observed_baseline": 3.4,
                    "target_type": "proxy",
                },
                {
                    "condition": "support_channel = 'phone'",
                    "target": "above current first-contact resolution baseline for phone channel",
                    "rationale": "Context evidence shows phone cases have an intermediate median cycle time, so the target reflects a middle-ground resolution expectation.",
                    "evidence_factor": "support_channel",
                    "evidence_metric": "case_cycle_time_hours",
                    "sample_size": 178,
                    "observed_baseline": 4.1,
                    "target_type": "proxy",
                },
            ],
        },
        {
            "name": "Response Time SLA Compliance Rate",
            "description": "Percentage of tickets where the first agent activity occurs within the response time target after ticket submission.",
            "category": "compliance",
            "smart_breakdown": {
                "specific": "Measures whether the time from Submit Ticket to Classify Ticket stays within the defined response window.",
                "measurable": "Computed as COUNT(cases where classify_time - submit_time <= SLA threshold) / COUNT(all cases) * 100. Unit: percentage.",
                "achievable": "Compliance can be maintained through consistent staffing and queue management without changing the process structure.",
                "relevant": "Directly protects the response time maintenance requirement stated in the simulation goal.",
                "time_bound": "Evaluated across all simulated cases in the run",
            },
            "target_direction": "maintain",
            "suggested_formula": "COUNT(cases meeting response SLA) / COUNT(all cases) * 100 across completed cases",
            "supported_by_log": True,
            "evidence_basis": "both",
            "process_scope": "end_to_end",
            "measurable_as": None,
            "context_segmentation": [],
        },
        {
            "name": "Wait Before L2 Investigation by Support Channel",
            "description": "Median waiting time from escalation to the start of L2 Investigation, segmented by support_channel.",
            "category": "time",
            "smart_breakdown": {
                "specific": "Measures the delay between Escalate to L2 and L2 Investigation for each support channel.",
                "measurable": "Computed from timestamps of Escalate to L2 and L2 Investigation, grouped by support_channel. Unit: hours.",
                "achievable": "Escalation delays can be reduced through better L2 queue prioritization and channel-specific routing.",
                "relevant": "Directly addresses the goal of minimizing escalation-related delays, with the context evidence confirming channel-dependent bottleneck severity.",
                "time_bound": "Evaluated across all simulated cases in the run",
            },
            "target_direction": "minimize",
            "suggested_formula": "median(start_time('L2 Investigation') - complete_time('Escalate to L2')) grouped by support_channel",
            "supported_by_log": True,
            "evidence_basis": "both",
            "process_scope": "activity_level",
            "measurable_as": "L2 Investigation Waiting Time",
            "context_segmentation": [
                {
                    "condition": "support_channel = 'email'",
                    "target": "below the observed median of 2.1h for email escalations",
                    "rationale": "Context evidence shows email-originated escalations have the highest median wait before L2 Investigation, making this the primary improvement target.",
                    "evidence_factor": "support_channel",
                    "evidence_metric": "activity_wait_time_hours",
                    "sample_size": 67,
                    "observed_baseline": 2.1,
                    "target_type": "direct",
                },
                {
                    "condition": "support_channel = 'chat'",
                    "target": "below the observed median of 0.8h for chat escalations",
                    "rationale": "Context evidence shows chat escalations have the lowest median wait, so the target preserves this stronger performance.",
                    "evidence_factor": "support_channel",
                    "evidence_metric": "activity_wait_time_hours",
                    "sample_size": 42,
                    "observed_baseline": 0.8,
                    "target_type": "direct",
                },
                {
                    "condition": "support_channel = 'phone'",
                    "target": "below the observed median of 1.2h for phone escalations",
                    "rationale": "Context evidence shows phone escalations have an intermediate wait time, so the target reflects a proportional improvement expectation.",
                    "evidence_factor": "support_channel",
                    "evidence_metric": "activity_wait_time_hours",
                    "sample_size": 54,
                    "observed_baseline": 1.2,
                    "target_type": "direct",
                },
            ],
        },
    ],
    "reasoning": (
        "This KPI set covers all three goal dimensions: first-contact resolution quality via a proxy KPI segmented "
        "by support channel, response time compliance as a maintain-style safeguard, and escalation delay reduction "
        "as a context-aware bottleneck KPI. The quality proxy uses log-derivable escalation patterns because the log "
        "does not contain a direct resolution-quality field. Channel-specific targets are grounded in the context "
        "evidence showing significant cycle time and escalation wait differences across support channels."
    ),
}

_EXAMPLE_4_PROCESS = (
    "The procurement process starts when a department submits a purchase request. "
    "A procurement officer reviews the request and checks the budget. "
    "If the budget is available, the officer requests quotes from suppliers. "
    "The department manager approves the selected quote. "
    "The procurement team creates the purchase order and sends it to the supplier. "
    "The process ends when the purchase order is issued."
)

_EXAMPLE_4_GOAL = "Reduce procurement processing cost while keeping purchase order issuance time stable"

_EXAMPLE_4_OUTPUT = {
    "simulation_goal_structured": (
        "Reduce the processing cost of the procurement process from purchase request submission "
        "to purchase order issuance while maintaining stable issuance time performance"
    ),
    "kpis": [
        _example_kpi(
            name="Average Procurement Processing Cost per Request",
            description="The average internal processing cost incurred for each purchase request from submission to purchase order issuance",
            category="cost",
            smart_breakdown={
                "specific": "Measures the internal processing cost of handling a purchase request across review, budget check, supplier quote request, approval, and purchase order creation",
                "measurable": "Computed as the average labor-related processing cost per completed purchase request. Unit: currency per request",
                "achievable": "Processing cost can be reduced through better allocation of procurement effort and reduced handling overhead without changing the fundamental process flow",
                "relevant": "Directly measures the main objective of reducing procurement processing cost",
                "time_bound": "Evaluated across all simulated cases in the run",
            },
            target_direction="minimize",
            suggested_formula="AVG(total_processing_cost_per_request) across completed cases",
            process_scope="end_to_end",
            measurable_as="Cost per Case",
        ),
        _example_kpi(
            name="Average Purchase Order Issuance Time",
            description="The average elapsed time from purchase request submission to purchase order issuance",
            category="time",
            smart_breakdown={
                "specific": "Measures the full duration of the procurement process from purchase request submission until the purchase order is issued",
                "measurable": "Computed as the average elapsed time between request submission and purchase order issuance across completed requests. Unit: days",
                "achievable": "Issuance time can be maintained while reducing cost by improving workload distribution and reducing unnecessary processing effort",
                "relevant": "Protects the stated requirement that purchase order issuance time should remain stable while cost is reduced",
                "time_bound": "Evaluated across all simulated cases in the run",
            },
            target_direction="maintain",
            suggested_formula="AVG(purchase_order_issued_time - purchase_request_submission_time) across completed cases",
            process_scope="end_to_end",
            measurable_as="Average Cycle Time",
        ),
        _example_kpi(
            name="Average Supplier Quote Handling Time",
            description="The average time spent handling supplier quote requests and quote selection activities",
            category="time",
            smart_breakdown={
                "specific": "Measures the average duration of the supplier quote request and quote handling portion of the procurement process",
                "measurable": "Computed as the average elapsed time spent between initiating supplier quote requests and selecting the quote for managerial approval. Unit: days",
                "achievable": "Quote handling time can be reduced through better coordination and prioritization without introducing unsupported process changes",
                "relevant": "Targets a likely operational lever that affects both processing cost and overall efficiency",
                "time_bound": "Evaluated across all simulated cases in the run",
            },
            target_direction="minimize",
            suggested_formula="AVG(quote_selection_time - quote_request_start_time) across completed cases",
            process_scope="subprocess",
            measurable_as="Average Processing Time",
        ),
    ],
    "reasoning": (
        "This KPI set fits the goal by combining one direct cost KPI, one safeguard time KPI, and one subprocess KPI for a likely cost-driving activity segment. "
        "The KPIs remain grounded in the explicitly described procurement activities and avoid introducing unsupported assumptions about external supplier behavior. "
        "They also support later simulation trade-off analysis between lower processing cost and stable issuance time."
    ),
}

_EXAMPLE_5_PROCESS = (
    "University procurement process for software purchases. An employee submits a purchase request "
    "with vendor quote and justification. Procurement checks completeness, requests missing information "
    "if needed, routes the request for budget approval, and then performs compliance review. If the "
    "request passes both approvals, a purchase order is created and sent to the vendor, after which the "
    "request is closed. If information is incomplete or inconsistent, the case is sent back to the requester "
    "for clarification and later resubmitted. The event log reliably records activities such as Submit Request, "
    "Check Completeness, Request Clarification, Resubmit Request, Budget Approve, Budget Reject, "
    "Compliance Review, Create Purchase Order, Send Purchase Order, and Close Request. The log contains "
    "timestamps, case ids, activity names, and resources, but it does not contain an explicit field for document "
    "quality, policy violations, or whether a request was correct on first submission."
)

_EXAMPLE_5_GOAL = "Improve request quality and reduce avoidable back-and-forth in the procurement process without slowing down overall turnaround."

_EXAMPLE_5_OUTPUT = {
    "simulation_goal_structured": "Improve request quality by reducing avoidable clarification and resubmission loops while maintaining overall procurement turnaround time.",
    "reasoning": (
        "The process goal emphasizes request quality, but the available process description and event log do not "
        "provide a direct observable quality field such as correctness, defect status, or documentation score. "
        "A good few-shot example here should teach the model not to invent unsupported direct quality KPIs. "
        "Instead, it should use measurable proxy indicators derived from clarification and resubmission behavior. "
        "Because the goal explicitly says 'without slowing down overall turnaround', the KPI set should also include "
        "a maintain-style end-to-end time KPI that protects turnaround while quality proxies improve."
    ),
    "kpis": [
        {
            "name": "Procurement Request Cycle Time",
            "description": "Average elapsed time from Submit Request to Close Request for completed procurement requests.",
            "category": "time",
            "smart_breakdown": {
                "specific": "Measures end-to-end turnaround for completed procurement requests.",
                "measurable": "Computed directly from the timestamps of Submit Request and Close Request in the event log.",
                "achievable": "Maintaining or slightly reducing turnaround is realistic while improving request handling quality.",
                "relevant": "Directly represents the goal constraint that quality improvements should not slow down overall turnaround.",
                "time_bound": "Evaluated across all simulated cases in the run",
            },
            "target_direction": "maintain",
            "suggested_formula": "average(complete_time('Close Request') - start_time('Submit Request')) over completed cases",
            "supported_by_log": True,
            "evidence_basis": "event_log_only",
            "process_scope": "end_to_end",
            "measurable_as": "Average Cycle Time",
            "context_segmentation": [],
        },
        {
            "name": "First-Pass Completion Proxy Rate",
            "description": "Share of completed requests that reach Create Purchase Order without any Request Clarification or Resubmit Request activity.",
            "category": "quality",
            "smart_breakdown": {
                "specific": "Measures how often requests appear complete enough to proceed without recorded clarification loops.",
                "measurable": "Computed from the presence or absence of Request Clarification and Resubmit Request in each completed case.",
                "achievable": "Improvement is realistic through better submission guidance and earlier completeness screening.",
                "relevant": "Serves as a supportable proxy for request quality when no direct quality label exists in the log.",
                "time_bound": "Evaluated across all simulated cases in the run",
            },
            "target_direction": "maximize",
            "suggested_formula": "count(completed cases with no 'Request Clarification' and no 'Resubmit Request') / count(all completed cases)",
            "supported_by_log": True,
            "evidence_basis": "proxy_from_log",
            "process_scope": "end_to_end",
            "measurable_as": None,
            "context_segmentation": [],
        },
        {
            "name": "Clarification Loop Rate",
            "description": "Percentage of procurement requests that contain at least one clarification and resubmission loop before final resolution.",
            "category": "quality",
            "smart_breakdown": {
                "specific": "Measures the frequency of avoidable back-and-forth in procurement handling.",
                "measurable": "Derived from cases containing both Request Clarification and Resubmit Request.",
                "achievable": "Reducing clarification loops is feasible through improved requester instructions and better initial completeness checks.",
                "relevant": "Operationalizes the quality-related part of the goal without inventing unsupported error or defect labels.",
                "time_bound": "Evaluated across all simulated cases in the run",
            },
            "target_direction": "minimize",
            "suggested_formula": "count(cases with at least one 'Request Clarification' and at least one 'Resubmit Request') / count(all cases)",
            "supported_by_log": True,
            "evidence_basis": "proxy_from_log",
            "process_scope": "end_to_end",
            "measurable_as": None,
            "context_segmentation": [],
        },
    ],
}

_EXAMPLE_6_PROCESS = (
    "Emergency department patient treatment process. A patient arrives, is registered, triaged by a nurse, "
    "waits for physician assessment, may receive laboratory tests or imaging, is reassessed after diagnostic "
    "results, and is then discharged or admitted. The event log records case-level and event-level data, including "
    "patient_type values such as walk_in and ambulance, triage category, activity timestamps, event_hour_of_day, "
    "and day of week. The log also records activities such as Arrival, Registration, Triage, Physician Assessment, "
    "Lab Test Ordered, Lab Result Available, Imaging Ordered, Imaging Result Available, Physician Reassessment, "
    "Discharge, and Admit."
)

_EXAMPLE_6_GOAL = (
    "Reduce emergency department delays while preserving safe treatment flow. Focus especially on whether "
    "different patient types experience different waiting patterns, whether diagnostic-result handling creates a "
    "bottleneck before physician reassessment, and whether delays vary systematically by time of day."
)

_EXAMPLE_6_CONTEXT_EVIDENCE = {
    "summary": {
        "case_level_factors": 1,
        "event_level_factors": 0,
        "temporal_factors": 1,
        "significant_relationships": 3,
    },
    "available_metrics": [
        "case_cycle_time_hours",
        "activity_wait_time_hours",
        "activity_duration_hours",
    ],
    "significant_relationships": [
        {
            "factor": "patient_type",
            "factor_scope": "case_level",
            "metric": "case_cycle_time_hours",
            "metric_scope": "case_level",
            "activity": None,
            "test": "kruskal_wallis",
            "p_value": 0.011,
            "summary": "Case cycle time differs significantly across patient_type groups, with walk_in cases showing a higher median than ambulance cases.",
            "segments": [
                {"condition": "patient_type = 'walk_in'", "observed_median": 4.6, "sample_size": 842},
                {"condition": "patient_type = 'ambulance'", "observed_median": 3.8, "sample_size": 291},
            ],
            "is_significant": True,
        },
        {
            "factor": "patient_type",
            "factor_scope": "case_level",
            "metric": "activity_wait_time_hours",
            "metric_scope": "activity_level",
            "activity": "Physician Reassessment",
            "test": "kruskal_wallis",
            "p_value": 0.018,
            "summary": "Waiting time before Physician Reassessment differs significantly across patient_type groups for cases with prior laboratory work, indicating a meaningful bottleneck.",
            "segments": [
                {"condition": "patient_type = 'walk_in'", "observed_median": 0.90, "sample_size": 403},
                {"condition": "patient_type = 'ambulance'", "observed_median": 0.65, "sample_size": 167},
            ],
            "is_significant": True,
        },
        {
            "factor": "event_hour_of_day",
            "factor_scope": "temporal",
            "metric": "activity_wait_time_hours",
            "metric_scope": "activity_level",
            "activity": "Triage",
            "test": "spearman_correlation",
            "p_value": 0.007,
            "summary": "Waiting time before Triage increases significantly with later event_hour_of_day values, with a clear median difference around a late-day split.",
            "segments": [
                {"condition": "event_hour_of_day <= 16", "observed_median": 0.35, "sample_size": 769},
                {"condition": "event_hour_of_day > 16", "observed_median": 0.58, "sample_size": 289},
            ],
            "is_significant": True,
        },
    ],
    "filtered_out_factors": [
        "event_day_of_week",
        "triage_category",
    ],
    "notes": [
        "Use only supported factors from the significant relationships when creating segmented KPIs.",
        "Preserve factor meaning in KPI names and segmentation wording.",
        "Use temporal wording for event_hour_of_day, such as 'by Time of Day'.",
    ],
}

_EXAMPLE_6_OUTPUT = {
    "simulation_goal_structured": "Reduce emergency department delays by identifying patient-type-specific and time-of-day-specific waiting patterns in a way that supports timely and orderly treatment flow.",
    "reasoning": (
        "This example should teach disciplined context-aware KPI generation. The context evidence supports three "
        "specific uses of segmentation and nothing broader. First, patient_type is supported for an end-to-end "
        "case cycle time KPI, so the title and segmentation should explicitly reflect patient type. Second, the "
        "supported bottleneck is the waiting time before Physician Reassessment for cases with prior lab work, "
        "again segmented by patient_type because that is the factor actually evidenced. Third, the temporal "
        "factor is event_hour_of_day for waiting time before Triage, so the KPI should explicitly use time-of-day "
        "wording. The reference to orderly treatment flow is reflected lightly through the focus on timely triage "
        "and reassessment, without adding an unsupported fourth KPI."
    ),
    "kpis": [
        {
            "name": "ED Case Cycle Time by Patient Type",
            "description": "Median elapsed time from Arrival to final outcome, segmented by patient_type to compare walk_in and ambulance cases.",
            "category": "time",
            "smart_breakdown": {
                "specific": "Measures complete emergency department throughput separately for each patient_type group.",
                "measurable": "Computed from the timestamps of Arrival and the first occurring final outcome event, either Discharge or Admit, together with the patient_type case attribute.",
                "achievable": "A reduction target is realistic because the event log and context evidence already show measurable variation across patient groups.",
                "relevant": "Matches the goal of checking whether different patient types experience different waiting patterns across the full patient journey.",
                "time_bound": "Evaluated across all simulated cases in the run",
            },
            "target_direction": "minimize",
            "suggested_formula": "median(final_outcome_time - start_time('Arrival')) grouped by patient_type",
            "supported_by_log": True,
            "evidence_basis": "both",
            "process_scope": "end_to_end",
            "measurable_as": "Average Cycle Time",
            "context_segmentation": [
                {
                    "condition": "patient_type = 'walk_in'",
                    "target": "below the observed median of 4.6h for walk_in cases",
                    "rationale": "Context evidence shows a higher observed median case cycle time for walk_in cases, so the target should reference that segment-specific baseline.",
                    "evidence_factor": "patient_type",
                    "evidence_metric": "case_cycle_time_hours",
                    "sample_size": 842,
                    "observed_baseline": 4.6,
                    "target_type": "direct",
                },
                {
                    "condition": "patient_type = 'ambulance'",
                    "target": "below the observed median of 3.8h for ambulance cases",
                    "rationale": "Context evidence shows a lower observed median case cycle time for ambulance cases, so the target should reflect that segment-specific baseline.",
                    "evidence_factor": "patient_type",
                    "evidence_metric": "case_cycle_time_hours",
                    "sample_size": 291,
                    "observed_baseline": 3.8,
                    "target_type": "direct",
                },
            ],
        },
        {
            "name": "Wait Before Physician Reassessment by Patient Type",
            "description": "Median waiting time before Physician Reassessment for cases with prior laboratory work, segmented by patient_type.",
            "category": "time",
            "smart_breakdown": {
                "specific": "Measures the waiting bottleneck before Physician Reassessment for each patient_type group.",
                "measurable": "Computed from the completion of Lab Result Available to the start of Physician Reassessment together with the patient_type case attribute.",
                "achievable": "The interval is directly observable and can be improved through better physician queueing or result-handling practices.",
                "relevant": "Directly reflects the supported bottleneck identified in the context evidence and supports timely treatment continuation.",
                "time_bound": "Evaluated across all simulated cases in the run",
            },
            "target_direction": "minimize",
            "suggested_formula": "median(start_time('Physician Reassessment') - complete_time('Lab Result Available')) grouped by patient_type",
            "supported_by_log": True,
            "evidence_basis": "both",
            "process_scope": "activity_level",
            "measurable_as": "Physician Reassessment Waiting Time",
            "context_segmentation": [
                {
                    "condition": "patient_type = 'walk_in'",
                    "target": "below the observed median of 0.90h for walk_in cases",
                    "rationale": "Context evidence shows a higher observed median waiting time before Physician Reassessment for walk_in cases, so the target should directly address that bottleneck.",
                    "evidence_factor": "patient_type",
                    "evidence_metric": "activity_wait_time_hours",
                    "sample_size": 403,
                    "observed_baseline": 0.90,
                    "target_type": "direct",
                },
                {
                    "condition": "patient_type = 'ambulance'",
                    "target": "below the observed median of 0.65h for ambulance cases",
                    "rationale": "Context evidence shows a lower observed median waiting time before Physician Reassessment for ambulance cases, so the target should remain segment-specific.",
                    "evidence_factor": "patient_type",
                    "evidence_metric": "activity_wait_time_hours",
                    "sample_size": 167,
                    "observed_baseline": 0.65,
                    "target_type": "direct",
                },
            ],
        },
        {
            "name": "Wait Before Triage by Time of Day",
            "description": "Median waiting time from Arrival to Triage, segmented by binary time-of-day groups derived from event_hour_of_day.",
            "category": "time",
            "smart_breakdown": {
                "specific": "Measures how long patients wait to reach Triage across earlier versus later times of day.",
                "measurable": "Computed from Arrival and Triage timestamps together with event_hour_of_day derived from the Arrival event.",
                "achievable": "A reduction target is realistic because the context evidence indicates systematic time-of-day variation in triage waiting.",
                "relevant": "Matches the goal of identifying whether delays vary systematically by time of day and supports timely early-stage patient flow.",
                "time_bound": "Evaluated across all simulated cases in the run",
            },
            "target_direction": "minimize",
            "suggested_formula": "median(start_time('Triage') - start_time('Arrival')) grouped by event_hour_of_day split",
            "supported_by_log": True,
            "evidence_basis": "both",
            "process_scope": "activity_level",
            "measurable_as": "Triage Waiting Time",
            "context_segmentation": [
                {
                    "condition": "event_hour_of_day <= 16",
                    "target": "below the observed median of 0.35h for earlier arrivals",
                    "rationale": "Context evidence shows a lower observed median waiting time before Triage for earlier arrivals, so the target should reference that segment baseline.",
                    "evidence_factor": "event_hour_of_day",
                    "evidence_metric": "activity_wait_time_hours",
                    "sample_size": 769,
                    "observed_baseline": 0.35,
                    "target_type": "direct",
                },
                {
                    "condition": "event_hour_of_day > 16",
                    "target": "below the observed median of 0.58h for later arrivals",
                    "rationale": "Context evidence shows a higher observed median waiting time before Triage for later arrivals, making this the key temporal segment for improvement.",
                    "evidence_factor": "event_hour_of_day",
                    "evidence_metric": "activity_wait_time_hours",
                    "sample_size": 289,
                    "observed_baseline": 0.58,
                    "target_type": "direct",
                },
            ],
        },
    ],
}


def _build_few_shot_messages() -> list[dict[str, str]]:
    """Return few-shot examples as alternating user/assistant message pairs."""
    return [
        {
            "role": "user",
            "content": (
                f'Process Description:\n"""{_EXAMPLE_1_PROCESS}"""\n\n'
                f'Simulation Goal:\n"""{_EXAMPLE_1_GOAL}"""\n\n'
                "Generate 3 SMART KPIs."
            ),
        },
        {
            "role": "assistant",
            "content": json.dumps(_EXAMPLE_1_OUTPUT, indent=2),
        },
        {
            "role": "user",
            "content": (
                f'Process Description:\n"""{_EXAMPLE_2_PROCESS}"""\n\n'
                f'Simulation Goal:\n"""{_EXAMPLE_2_GOAL}"""\n\n'
                "Generate 3 SMART KPIs."
            ),
        },
        {
            "role": "assistant",
            "content": json.dumps(_EXAMPLE_2_OUTPUT, indent=2),
        },
        {
            "role": "user",
            "content": (
                f'Process Description:\n"""{_EXAMPLE_3_PROCESS}"""\n\n'
                f'Simulation Goal:\n"""{_EXAMPLE_3_GOAL}"""\n\n'
                "Context Evidence (JSON):\n"
                f'"""{json.dumps(_EXAMPLE_3_CONTEXT_EVIDENCE, indent=2)}"""\n\n'
                "Generate 3 SMART KPIs."
            ),
        },
        {
            "role": "assistant",
            "content": json.dumps(_EXAMPLE_3_OUTPUT, indent=2),
        },
        {
            "role": "user",
            "content": (
                f'Process Description:\n"""{_EXAMPLE_4_PROCESS}"""\n\n'
                f'Simulation Goal:\n"""{_EXAMPLE_4_GOAL}"""\n\n'
                "Generate 3 SMART KPIs."
            ),
        },
        {
            "role": "assistant",
            "content": json.dumps(_EXAMPLE_4_OUTPUT, indent=2),
        },
        {
            "role": "user",
            "content": (
                f'Process Description:\n"""{_EXAMPLE_5_PROCESS}"""\n\n'
                f'Simulation Goal:\n"""{_EXAMPLE_5_GOAL}"""\n\n'
                "Generate 3 SMART KPIs."
            ),
        },
        {
            "role": "assistant",
            "content": json.dumps(_EXAMPLE_5_OUTPUT, indent=2),
        },
        {
            "role": "user",
            "content": (
                f'Process Description:\n"""{_EXAMPLE_6_PROCESS}"""\n\n'
                f'Simulation Goal:\n"""{_EXAMPLE_6_GOAL}"""\n\n'
                "Context Evidence (JSON):\n"
                f'"""{json.dumps(_EXAMPLE_6_CONTEXT_EVIDENCE, indent=2)}"""\n\n'
                "Generate 3 SMART KPIs."
            ),
        },
        {
            "role": "assistant",
            "content": json.dumps(_EXAMPLE_6_OUTPUT, indent=2),
        },
    ]


def build_smart_kpi_prompt(
    process_description: str,
    simulation_goal: str,
    num_kpis: int | None = 5,
    log_evidence: str | None = None,
    context_evidence: str | None = None,
) -> tuple[str, list[dict[str, str]], str]:
    """Build the system prompt, few-shot message pairs, and user prompt for SMART KPI generation."""

    system_prompt = f"""You are a senior Business Process Management (BPM) consultant specializing in process simulation and performance measurement.

Your task is to convert a simulation goal and process description into a set of SMART KPIs that can later be used to configure, guide, and validate business process simulations.

You are not a general-purpose assistant. You only handle BPM process simulation and SMART KPI design tasks.

## SMART Criteria Definitions

Specific: The KPI must target a concrete, well-defined aspect of the business process and reference activities, roles, resources, case outcomes, or process segments from the description.
Measurable: The KPI must be quantifiable with a clear unit and a computation that could plausibly be derived from process data or simulation outputs.
Achievable: The KPI must be realistic within the described process and simulation-study context.
Relevant: The KPI must directly support the simulation goal.
Time-bound: The KPI must define a measurement period, review period, or simulation horizon.

{REQUIRED_SCHEMA}
## Rules you MUST follow:
1. ONLY reference activities, roles, resources, outcomes, or process elements that are explicitly mentioned in the process description. Do NOT invent activities, resources, constraints, or data attributes.
2. Each KPI must be independently measurable and expressed as a concrete process metric, not as a vague improvement slogan.
3. Prefer KPI sets that cover the main goal dimensions and, where appropriate, include:
   - one end-to-end KPI for the main process objective,
   - one safeguard or constraint KPI if the goal includes a quality/compliance/stability condition,
   - one diagnostic KPI for a likely subprocess bottleneck or operational lever.
4. Cover multiple dimensions of process performance when appropriate for the goal.
5. Output must be valid JSON matching the required schema above. No markdown and no extra text outside the JSON.
6. The top-level JSON object MUST contain "simulation_goal_structured", "kpis", and "reasoning".
7. "reasoning" must be concise: 2 to 4 sentences explaining why this KPI set fits the goal.
8. Avoid duplicate KPIs or KPIs that measure nearly the same thing with different wording.
9. Avoid generic KPIs such as "improve efficiency" or "increase performance" unless they are operationalized as concrete measurable process metrics.
10. Prefer KPIs that are useful for later what-if analysis, simulation comparison, or validation of simulation results.
11. If no event log evidence is provided, default to "supported_by_log": false and "evidence_basis": "process_description_only" unless the user explicitly provided grounded log evidence elsewhere.
12. Ensure that each major component of the simulation goal is directly operationalized by at least one KPI.
13. If statistically filtered context evidence is provided, use it to differentiate KPI targets only where the evidence justifies it.
14. Populate "context_segmentation" with context-condition to target pairs only when those segments are explicitly supported by the provided context evidence.
15. When a KPI has non-empty context_segmentation, you MAY make the title lightly context-aware using the evidence_factor already recorded in that segmentation — for example "Claim Cycle Time by Claim Type" where "Claim Type" is the evidence_factor. The context qualifier appended to the title MUST match an evidence_factor already present in that KPI's context_segmentation.
16. Prefer concise context-aware titles rather than overly long names. Keep the title readable and use the description plus context_segmentation for detail.
17. Prefer context-aware bottleneck KPI titles only when the KPI's context_segmentation is non-empty and contains an evidence_factor for that activity delay. Do not use context-aware naming for a bottleneck KPI whose context_segmentation is empty.
18. Do not invent context effects, significance claims, or segmented targets that are absent from the evidence.
19. If no accepted context relationship survives the provided filtering, keep KPI titles and descriptions generic and avoid context-aware wording.
20. Never append "by [Factor]", "by [Segment]", or any temporal qualifier to a KPI name when that KPI's context_segmentation is empty. A KPI name must not encode a context breakdown that context_segmentation does not already support with a populated evidence_factor.
21. Use "adjusted_p_value" only when it is explicitly present in the supplied context evidence. Do not relabel a raw "p_value" as an "adjusted_p_value".
22. If a traceability field is not present in the supplied context evidence, omit it rather than inferring or inventing it. This applies especially to adjusted_p_value, effect_size, sample_size, observed_baseline, and target_type when it is not warranted.
23. When "context_segmentation" is non-empty and "observed_baseline" is present for a segment, the segment "target" description must reference an improvement direction relative to that baseline (below it for minimize, above it for maximize). Never describe a target that is equal to or worse than the observed baseline.
24. If the input is unrelated to BPM process simulation, SMART KPI design, or business-process performance measurement, return valid JSON with:
   - "simulation_goal_structured": "Out of scope for BPM KPI generation"
   - "kpis": []
   - "reasoning": "The request is unrelated to BPM process simulation and SMART KPI design."
25. For every KPI, set "measurable_as" using these exact rules:
    a. End-to-end cycle time → "Average Cycle Time"
    b. Overall case waiting time (process_scope "end_to_end") → "Average Waiting Time"
    c. Waiting time BEFORE a specific activity (process_scope "activity_level") → "{{Activity Name}} Waiting Time" using the exact activity name from the process description. NEVER use "Average Waiting Time" for activity-level waiting KPIs.
    d. Resource utilization → "Resource Utilization" for at most ONE KPI per output (see Rule 30)
    e. Quality, compliance, flexibility, rework rate, SLA, pass/fail → null
    f. Subprocess time with no single matching activity → null
26. After finalizing the KPI list, verify dimension coverage: for every performance dimension explicitly named in the simulation goal (e.g. time, cost, quality, utilization, throughput), at least one KPI must directly target it. If a named dimension is uncovered, add a KPI — even if it exceeds the default count.
27. In "suggested_formula", express computation using case-level aggregation only: AVG(...) across completed cases, COUNT(...) across the simulation run, SUM(...) / SUM(...) over the run. Do NOT append "per month", "per week", or any time-window suffix — Prosimos outputs a case-level event log, not a calendar-based report.
28. In "time_bound", reference the simulation run, not real calendar periods. Use "Evaluated across all simulated cases in the run" or "Computed at simulation end". Do not write "over a 6-month period", "measured weekly", or similar calendar language — the simulation is case-based, not time-windowed.
29. Never use any calendar-period factor in context_segmentation. This includes any factor whose name contains the words month, quarter, year, or season — regardless of prefix (e.g. event_month, case_start_month, arrival_quarter, submission_year, event_quarter, event_year all violate this rule). These reflect historical seasonality in the log but cannot be set as case attributes in a Prosimos DES simulation. Simulatable temporal factors (hour_of_day, day_of_week) are permitted when evidence supports them.
30. "Resource Utilization" is a single global number (mean across ALL resources). Do NOT assign it to more than one KPI. If the goal names multiple roles separately (e.g. buyer utilization and analyst utilization), assign "Resource Utilization" to the first or most important role and set measurable_as to null for the others. Never invent per-role measurable_as names that are not in the computable KPI list.
31. Utilization KPIs always use process_scope "end_to_end". Resource utilization is measured across the whole simulation run, not at a single activity or subprocess. Never set process_scope to "activity_level" or "subprocess" for a KPI with category "utilization".

## Internal procedure
Before producing the final JSON, silently do the following:
1. Identify the process elements explicitly mentioned in the description.
2. Decompose the simulation goal into optimization targets, constraints, and scope.
2b. List each performance dimension explicitly named in the simulation goal. For each named dimension, confirm at least one selected KPI directly targets it. If any named dimension is uncovered, add a KPI before proceeding.
3. Select KPI candidates that are grounded in the process description and relevant to simulation.
4. Check that each KPI satisfies all SMART criteria and uses the most appropriate category, evidence basis, and process scope.
5. Remove duplicates or weakly differentiated KPIs.
6. For each KPI, assign "measurable_as": activity-level waiting KPIs → "{{Activity Name}} Waiting Time"; end-to-end waiting → "Average Waiting Time"; utilization → "Resource Utilization" for one KPI only, null for all others; quality/compliance/flexibility → null.
7. Return only the final JSON.
"""

    few_shot_messages = _build_few_shot_messages()

    if num_kpis is None:
        kpi_count_instruction = "Determine the optimal number of SMART KPIs between 3 and 6 based on the complexity of the process and goal."
    else:
        kpi_count_instruction = f"Generate exactly {num_kpis} SMART KPIs."

    log_evidence_block = ""
    if log_evidence:
        log_evidence_block = (
            '\nEvent Log Evidence Profile (JSON):\n'
            f'"""{log_evidence}"""\n\n'
            "Treat the event log evidence above as the primary source for what can be measured directly from process data. "
            "Prefer activities, resources, variants, transitions, and timing patterns that appear in the profile. "
            "Only propose KPIs whose suggested formulas are compatible with the listed measurable_signals or available_attributes. "
            "If the simulation goal suggests a metric that the log does not support directly, choose the closest supportable proxy and acknowledge that tradeoff in the reasoning. "
            "Use 'supported_by_log' = true only when the profile reasonably supports the KPI, and choose 'both', 'event_log_only', or 'proxy_from_log' for evidence_basis accordingly. "
            "Do NOT invent log-based facts that are not reflected in the evidence profile.\n"
        )

    context_evidence_block = ""
    if context_evidence:
        context_evidence_block = (
            '\nContext Evidence (JSON):\n'
            f'"""{context_evidence}"""\n\n'
            "Treat the context evidence above as statistically filtered context-performance association evidence. "
            "Only create context-segmented KPI targets when the significant_relationships section shows an evidence-supported relationship that passed the reported statistical filtering. "
            "Prefer explicit segmented targets such as one target for premium cases and another for standard cases, or distinct targets for Mondays versus other weekdays, when that is directly supported by the evidence. "
            "When the evidence points to a context-dependent bottleneck and that bottleneck KPI will have non-empty context_segmentation, you MAY use a context-aware title whose qualifier matches the evidence_factor. Do not use context-aware naming when context_segmentation will be empty. "
            "If the context evidence says a factor was filtered out or not significant, do not use it for KPI segmentation. "
            "If the context evidence contains no accepted relationships after filtering, keep KPI titles and descriptions generic. "
            "Use the provided raw p-values, adjusted p-values, effect sizes, support counts, and provenance notes as traceability cues instead of overclaiming precision. "
            "Use adjusted_p_value only when it is explicitly present in the context evidence. Do not relabel a raw p_value as adjusted_p_value. "
            "If a traceability field is absent from the supplied context evidence, omit it rather than inferring or inventing it. "
            "Reflect context-dependent bottlenecks in the KPI description or reasoning when the evidence is activity-specific.\n"
        )

    user_prompt = f"""Now generate SMART KPIs for the following:

Process Description:
\"\"\"{process_description}\"\"\"

Simulation Goal:
\"\"\"{simulation_goal}\"\"\"
{log_evidence_block}
{context_evidence_block}
{kpi_count_instruction}

Requirements:
1. Identify only the process elements that are explicitly mentioned.
2. Decompose the simulation goal into concrete optimization targets, constraints, and scope.
3. Select KPIs that are grounded in the provided process description and simulation goal.
4. Ensure each KPI is specific, measurable, non-duplicative, useful for later simulation analysis or validation, and supportable by the event-log evidence when such evidence is provided.
5. For each KPI, set category, supported_by_log, evidence_basis, and process_scope explicitly and consistently.
6. Use context_segmentation to encode context-specific targets when, and only when, the context evidence supports segmentation.
7. When a KPI has non-empty context_segmentation, you MAY make the title lightly context-aware using the evidence_factor already in that segmentation. Do not add context qualifiers to the title when context_segmentation is empty.
8. Prefer a concise context-aware bottleneck KPI title only when that KPI's context_segmentation is non-empty with a populated evidence_factor. If context_segmentation is empty, keep the title generic.
9. If no accepted context relationship survives the provided filtering, keep KPI titles and descriptions generic and avoid context-aware wording.
10. Write "simulation_goal_structured" as a precise BPM-focused restatement of the goal.
11. Write "reasoning" as a concise 2 to 4 sentence summary of why this KPI set fits the goal and, when log or context evidence is provided, why the set is measurable and context-aware.
12. Output ONLY valid JSON with the top-level fields "simulation_goal_structured", "kpis", and "reasoning".
13. Do not include markdown fences or explanatory text outside the JSON."""
    return system_prompt, few_shot_messages, user_prompt