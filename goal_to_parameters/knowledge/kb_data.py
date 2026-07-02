"""Structured knowledge base data for the second LLM step.

Sources:
  - ParametersToGoalsTable.csv from the baseline repository
    (muruvetg/from-simulation-goals-to-parameters) — literature-derived
    mappings between simulation goals and SimuBridge parameter changes.
  - SimulationParameters.csv — parameter taxonomy with categories.
  - SIMOD output structure — cross-references to discovered model fields.
  - Context-aware differentiation rules — thesis extension.
  - Additional literature extensions — 15 further simulation/BPR papers
    covering healthcare, finance, manufacturing, government, logistics,
    and service operations.

The baseline loaded these CSVs as flat text but never injected them into
the prompt (bug in prompts.ts).  This module restructures the same
knowledge into typed, queryable Python objects with proper literature
attribution and SIMOD field cross-references.
"""

from __future__ import annotations

from knowledge.models import (
    ChangeDirection,
    ContextAwareRule,
    ContextFactorScope,
    GoalCategory,
    GoalParameterMapping,
    LiteratureReference,
    ParameterCategory,
    ParameterChange,
    ParameterKnowledgeBase,
    SimodFieldMapping,
    SimulationParameter,
)


# ===================================================================
# 1. LITERATURE REFERENCES
#    Sequentially numbered (1-22). Papers 1-7 are the baseline set;
#    papers 8-22 are the extended set introduced by the thesis.
# ===================================================================

LITERATURE: list[LiteratureReference] = [
    # --- 1. Braaksma et al. (2017) ---
    LiteratureReference(
        paper_id=1,
        authors="Braaksma et al.",
        year=2017,
        title="Reusable simulation model for evaluating walk-in and appointment systems for CT-scan facilities",
        domain="healthcare",
        key_finding=(
            "Varying scanner availability, scheduling strategies, and arrival rates "
            "reduced patient access time from 4.7 days to under 1 day."
        ),
        parameters_tested=[
            "resource_count", "resource_calendar", "inter_arrival_time",
        ],
        quantitative_result="Access time reduced from 4.7 days to < 1 day",
        source_location="Papers/CaseStudy/A_Reusable_simulation_model_to_evaluate_the_effects_of_walkin_for_diagnostic_examination.pdf — Table 2 (4.70 days baseline → 0.64 days in Experiment 4). Verified against PDF.",
    ),
    # --- 2. Al-Hawari et al. (2022) ---
    LiteratureReference(
        paper_id=2,
        authors="Al-Hawari, T.; Khanfar, A.; Mumani, A.; Bataineh, O.",
        year=2022,
        title="A Simulation-Based Framework for Evaluation of Healthcare Systems with Interacting Factors and Correlated Performance Measures",
        domain="healthcare",
        key_finding=(
            "DES + DOE + modified TOPSIS framework applied to Educational Dentistry "
            "Clinics in Jordan (orthodontic and primary diagnosis). Best alternative "
            "(Alt 36) reduced orthodontic waiting time by 29.44%, orthodontic LOS by "
            "37.74%, and primary diagnosis LOS by 7.12%, while increasing orthodontic "
            "patients served by 69.39%. Four tested factors: appointment scheduling "
            "rules, number of receptionists, orthodontist work schedules, patient flow."
        ),
        parameters_tested=[
            "resource_count", "resource_calendar", "gateway_probabilities",
            "inter_arrival_time",
        ],
        quantitative_result=(
            "Alt 36 (highest modified-TOPSIS rank): orthodontic waiting time -29.44%, "
            "orthodontic LOS -37.74%, primary diagnosis LOS -7.12%, orthodontic patients "
            "served +69.39%, orthodontist utilisation +11.22%, receptionist utilisation "
            "+65.83%. Note: abstract assigns 37.74% to LOS; p.15/3721 assigns 37.74% to "
            "waiting time — minor inconsistency within paper."
        ),
        source_location=(
            "Papers/CaseStudy/A Simulation‑Based Framework for Evaluation of "
            "Healthcare Systems.pdf — Arabian Journal for Science and Engineering (2022) "
            "47:3707-3724. Key figures verified: abstract p.1/3707 (29.44%/37.74%/7.12% "
            "reductions, 69.39% patients served increase); ranked alternative p.14/3720; "
            "four tested factors p.7/3713."
        ),
    ),
    # --- 3. Pereira et al. (2021) ---
    LiteratureReference(
        paper_id=3,
        authors="Pereira et al.",
        year=2021,
        title="Case study on IT incident management process improvement through simulation",
        domain="it_service_management",
        key_finding=(
            "Eliminating phone entry, automating first-level support, and "
            "centralising email achieved a 10.7% reduction in processing time."
        ),
        parameters_tested=[
            "activity_duration", "gateway_probabilities", "inter_arrival_time",
        ],
        quantitative_result="10.7% reduction in L2 processing time, 100% elimination of L1 processing",
        source_location="Papers/CaseStudy/Business process management heuristics in IT service management a case study for incident management.pdf — Abstract confirms '10.7% average processing time reduction in 2nd support level' and 'eliminates effort in 1st support level'. Verified against PDF.",
    ),
    # --- 4. Czvetko et al. (2021) ---
    LiteratureReference(
        paper_id=4,
        authors="Czvetko et al.",
        year=2021,
        title="Data-driven BPM methodology for CNC production line redesign",
        domain="manufacturing",
        key_finding=(
            "Reassigning resource-activity mappings so operators manage multiple "
            "CNC machines achieved a 40% increase in production capacity."
        ),
        parameters_tested=[
            "resource_activity_assignment", "activity_duration",
        ],
        quantitative_result="40% production capacity increase",
        source_location="Papers/CaseStudy/Data-driven business process management-based development of.pdf — Verified: CNC production line case study, resource-activity reassignment methodology confirmed. Verified against PDF.",
    ),
    # --- 5. Zeinali et al. (2015) ---
    LiteratureReference(
        paper_id=5,
        authors="Zeinali et al.",
        year=2015,
        title="Simulation-based metamodeling approach for resource planning in emergency departments",
        domain="healthcare",
        key_finding=(
            "Reducing nurses from 3 to 2 and adding one resident under a "
            "budget constraint achieved a 48% reduction in average waiting time."
        ),
        parameters_tested=[
            "resource_count", "gateway_probabilities",
        ],
        quantitative_result="48% reduction in average patient waiting time (44 to 23 min)",
        source_location="Papers/CaseStudy/Resource planning in the emergency departments.pdf — 48% waiting time reduction confirmed in paper. Verified against PDF.",
    ),
    # --- 6. Rezeq et al. (2024) ---
    LiteratureReference(
        paper_id=6,
        authors="Rezeq et al.",
        year=2024,
        title="Hybrid simulation-optimization for relief-aid distribution at security checkpoints",
        domain="logistics",
        key_finding=(
            "Combining queue management, electronic processing, extended hours, "
            "and extra staffing achieved a 90% cycle-time reduction and 28% cost reduction."
        ),
        parameters_tested=[
            "resource_count", "resource_calendar", "activity_duration",
            "inter_arrival_time",
        ],
        quantitative_result="90% cycle-time reduction, 28% cost reduction",
        source_location="Papers/CaseStudy/Hybrid simulation-optimization.pdf — 90% cycle-time and 28% cost reduction confirmed in paper results. Verified against PDF.",
    ),
    # --- 7. Maass et al. (2021) ---
    LiteratureReference(
        paper_id=7,
        authors="Maass et al.",
        year=2021,
        title="Discrete event simulation for Mayo Clinic CT department",
        domain="healthcare",
        key_finding=(
            "Adding one dedicated transporter achieved a 10-minute reduction "
            "in ED CT access times, reaching 80% compliance with the 30-minute goal."
        ),
        parameters_tested=[
            "resource_count", "resource_activity_assignment", "activity_duration",
        ],
        quantitative_result="10-min reduction, 80% compliance with 30-min access goal",
        source_location="Papers/CaseStudy/Evaluation Clinical Practice - 2021 - Maass - A discrete event simulation to evaluate impact of radiology process changes.pdf — Abstract confirms 9.8–10.3 min reduction and 80% compliance target. Verified against PDF.",
    ),
    # --- 8. Lee et al. (2019) ---
    LiteratureReference(
        paper_id=8,
        authors="Lee, L., Ou, Y., Cheng, Y., Sun, Y., Wu, H., Guo, W.",
        year=2019,
        title="Using a Hybrid Simulation Model to Maximize Patient Throughput of Magnetic Resonance Imaging in a Medical Center",
        domain="healthcare",
        key_finding=(
            "Adding one radiographer during 11:00-19:00 increased monthly throughput "
            "by 248 patients, shortened patient waiting time by 2.51 days, raised "
            "scanner utilization by 6.15%, and generated US$38,424-69,169 additional "
            "gross income per month."
        ),
        parameters_tested=["resource_count", "resource_calendar", "inter_arrival_time"],
        quantitative_result=(
            "248 additional patients/month, 2.51 days waiting time reduction, "
            "6.15% average scanner utilization increase, US$38,424-US$69,169 "
            "additional monthly gross income"
        ),
        source_location="Abstract and Results section — exact quote: 'providing 248 additional patient examinations with one additional radiographer employed during 11:00 to 19:00 per month would shorten the waiting time of a patient to undergo an MRI examination by 2.51 days, increase the utilization rate of each MRI scanner by an average of 6.15%, and bring an additional gross income of US$38,424 to US$69,169 per 31-day month.'",
    ),
    # --- 9. Kristiana et al. (2026) ---
    LiteratureReference(
        paper_id=9,
        authors="Kristiana, S.P.D.; Triyanti, V.; Budiyanta, N.E.; Silitonga, R.M.",
        year=2026,
        title="Production System Analysis and Scenario Development Using FlexSim: A Case-Based Study",
        domain="manufacturing",
        key_finding=(
            "Reassigning an underutilized operator to a bottleneck workstation "
            "reduced average queue waiting time by 29.5% and total production "
            "time by 31.7%."
        ),
        parameters_tested=["resource_activity_assignment", "resource_count"],
        quantitative_result=(
            "29.5% reduction in average queue waiting time; 31.7% reduction in "
            "total production time; 8.5% increase in average operator utilization"
        ),
        source_location="Results section — exact quotes: 'the overall average queue time across all queues decreased by 29.5%' and 'contributed to a 31.7% reduction in total production time. Under the current conditions, the production of 2440 units required 366 days, whereas in the proposed model, the same number of units could be completed within 250 days.'",
    ),
    # --- 10. Aeenparast et al. (2013) ---
    LiteratureReference(
        paper_id=10,
        authors="Aeenparast, A.; Tabibi, S.J.; Shahanaghi, K.; Aryanejhad, M.B.",
        year=2013,
        title="Reducing Outpatient Waiting Time: A Simulation Modeling Approach",
        domain="healthcare",
        key_finding=(
            "Combining physician work time schedule changes with patient admission "
            "time changes reduced outpatient waiting time by 71.4%, outperforming "
            "scenarios that only increased physician numbers."
        ),
        parameters_tested=["resource_calendar", "resource_count"],
        quantitative_result="71.4% reduction in weighted mean waiting time (from 55.36 to 15.83 minutes)",
        source_location="Results/Scenario comparison table — exact quote: 'combining physician's work time changing (scenario 6) and scenario 7) would reduce patient's waiting time about 71.40 and is the best scenario among others for reducing outpatient waiting time.'",
    ),
    # --- 11. Rashed et al. (2023) ---
    LiteratureReference(
        paper_id=11,
        authors="Rashed, C.A.A.; Nahar, S.K.; Pritom, A.P.",
        year=2023,
        title="Service Time Reduction Through the Development of a Simulation Model in a Selected Bank",
        domain="finance",
        key_finding=(
            "Reassigning an underutilized server from the cash debit section to "
            "the overloaded cash credit university section during peak hours "
            "reduced waiting time by 73% at the bottleneck counter and improved "
            "overall service performance by 30% in waiting time and 40% in "
            "service time."
        ),
        parameters_tested=["resource_activity_assignment", "resource_count"],
        quantitative_result=(
            "Waiting time reduced from 12.64 to 3.38 minutes at bottleneck; "
            "queue length from 8.54 to 2.04; cash debit server utilization "
            "increased from 0.36 to 0.59 (off-peak) and 0.60 to 0.73 (peak)"
        ),
        source_location="Results table — exact quote: 'reduction in the waiting time from 12.64 to 3.38 and the queue length from 8.54 to 2.04 during peak time for the cash credit university section' (73% confirmed from raw values). Note: the 40% service time figure was not confirmed in pages 1-8; verify against later pages.",
    ),
    # --- 12. Shim & Kumar (2010) ---
    LiteratureReference(
        paper_id=12,
        authors="Shim, S.J.; Kumar, A.",
        year=2010,
        title="Simulation for emergency care process reengineering in hospitals",
        domain="healthcare",
        key_finding=(
            "Adding a new payment station and a short-stay ward to the emergency "
            "care process reduced total patient wait times by 41% and shortened "
            "overall time in the system by about 10 minutes, with particular "
            "benefit for higher-acuity patients."
        ),
        parameters_tested=["resource_count", "gateway_probabilities"],
        quantitative_result=(
            "41% reduction in total wait times at work stations (6.86 to 5.01 min); "
            "PAC 2 wait time reduced by 7.80 min; total system time reduced from "
            "133.93 to 123.33 min"
        ),
        source_location="Table III, p. 803 (work-station wait times before/after) and Table IV, p. 803 (PAC-level system times). Exact quote: 'the changes shorten patient wait times by 2.81 minutes, i.e. about 41 percent of the wait times experienced before the changes.' System time: 133.93 → 123.33 min (−10.60 min, Table IV).",
    ),
    # --- 13. Srinivas et al. (2021) ---
    LiteratureReference(
        paper_id=13,
        authors="Srinivas, S.; Nazareth, R.P.; Ullah, M.S.",
        year=2021,
        title="Modeling and analysis of business process reengineering strategies for improving emergency department efficiency",
        domain="healthcare",
        key_finding=(
            "Changing the triage process to evenly distribute medium-acuity "
            "patients between doctors and physician assistants reduced waiting "
            "time by 20% at minimal cost; combining this process change with "
            "optimized workforce allocation per shift achieved 84% reduction in "
            "waiting time and balanced resource utilization."
        ),
        parameters_tested=["resource_activity_assignment", "resource_count", "resource_calendar"],
        quantitative_result=(
            "Scenario 2 (process change): 20% waiting time reduction; "
            "Scenario 5 (process change + workforce optimization): 84% reduction "
            "(89.9 to 14.3 min); doctor utilization reduced from 86.4% to "
            "balanced 70-80% range"
        ),
        source_location="Abstract, p.1 — exact quotes: 'change in the triage process...reduces physician workload, and improves average waiting time by 20%' and 'optimizing the workforce level...delivers the best performance (84% reduction in waiting time and balanced resource utilization)'. Raw values (89.9 → 14.3 min) confirmed in Figure 3, p.12.",
    ),
    LiteratureReference(
        paper_id=14,
        authors="Marchesi, J.F.; Hamacher, S.; Peres, I.T.",
        year=2025,
        title="Stochastic model for physician staffing and scheduling in emergency departments with multiple treatment stages",
        domain="healthcare",
        key_finding=(
            "A two-stage stochastic optimization model for physician scheduling "
            "aligned with uncertain patient arrival patterns reduced overall "
            "average waiting time by 69% and length of stay by 37% compared to "
            "manual scheduling, across multiple ED treatment stages."
        ),
        parameters_tested=["resource_calendar", "resource_count"],
        quantitative_result=(
            "Overall average waiting time reduced from 54.6 to 16.8 min "
            "(69% reduction); LOS reduced from 102.1 to 64.3 min (37% reduction); "
            "first assessment queue frequency dropped from 31.14% to 12.16%"
        ),
        source_location="Abstract, p.1 — exact quote: 'overall average waiting time reduction from 54.6 (54.0-55.1) to 16.8 (16.7-17.0) minutes and an average length of Stay reduction from 102.1 (101.7-102.4) to 64.3 (64.2-64.5) minutes.' 69% and 37% reductions computed from raw values.",
    ),
    LiteratureReference(
        paper_id=15,
        authors="Gharahighehi, A., Kheirkhah, A.S., Bagheri, A., & Rashidi, E.",
        year=2016,
        title="Improving performances of the emergency department using discrete event simulation, DEA and the MADM methods",
        domain="healthcare",
        key_finding=(
            "Tested 10 scenarios combining resource capacity increases (beds, "
            "nurses, GP, pathologist, imaging staff), shift extensions, and "
            "queue-priority reassignment in an Iranian ED; priority-based queueing "
            "by patient severity was the best ranked scenario, reducing acute "
            "patients' waiting time without added investment."
        ),
        parameters_tested=["resource_count", "resource_calendar", "resource_activity_assignment"],
        quantitative_result=(
            "~5% reduction in acute patients' overall waiting time via "
            "severity-based queue prioritization; baseline average waiting times "
            "were 22/141/1028/1829 minutes for ESI1–ESI4 patients"
        ),
        source_location="Abstract, p.1 — exact quote: 'implementing the first scenario in the ranking would reduce acute patients' overall waiting time by approximately 5%, and it doesn't require any additional investments.' Baseline ESI1-4 waiting times (22/141/1028/1829 min) confirmed in Table 4, p.10.",
    ),
    LiteratureReference(
        paper_id=16,
        authors="Renna, P.; Colonnese, C.",
        year=2025,
        title="A Simulation-Driven Business Process Reengineering Framework for Teaching Assignment Optimization in Higher Education—A Case Study of the University of Basilicata",
        domain="government",
        key_finding=(
            "Applied discrete event simulation (Simul8) combined with Bonita BPM "
            "to redesign a university teaching-assignment workflow; automation of "
            "approval routing, digital signatures, and document handling cut "
            "cycle time and manual interventions substantially while improving "
            "administrative staff utilization — reducing end-to-end processing "
            "time by 35% and improving staff utilization by 22% while remaining "
            "compliant with national accreditation standards."
        ),
        parameters_tested=[
            "activity_duration", "resource_calendar", "gateway_probabilities",
            "resource_count", "resource_activity_assignment",
        ],
        quantitative_result=(
            "35% reduction in end-to-end processing time (46,200 → 30,040 min); "
            "22% improvement in staff utilization (7.2% to 9.1%); manual "
            "interventions down 65%; document handling time down 50%; throughput "
            "+28% (6.9 → 7.7 assignments/cycle)"
        ),
        source_location="Abstract, p.1 — 35% processing time reduction and 22% staff utilization improvement confirmed. Raw values (46,200 → 30,040 min; 7.2% → 9.1%) and secondary metrics (65% manual interventions, 50% document handling, +28% throughput) not verified in pages 1-10; check Results/Simulation section.",
    ),
    LiteratureReference(
        paper_id=17,
        authors="Madadi, N., Roudsari, A.H., Wong, K.Y., & Galankashi, M.R.",
        year=2013,
        title="Modeling and Simulation of a Bank Queuing System",
        domain="finance",
        key_finding=(
            "Built a WITNESS discrete-event model of a Malaysian bank branch "
            "with 5 service types and 3 customer classes; compared 4 redesign "
            "alternatives and found that adding a third counter, removing the "
            "dedicated service-information table, and standardizing counter "
            "shifts produced the best joint outcome on waiting time, utilization, "
            "and cost."
        ),
        parameters_tested=[
            "resource_count", "resource_calendar", "resource_activity_assignment",
            "activity_duration",
        ],
        quantitative_result=(
            "Best alternative (Alt IV): average waiting time 10.88 min (down from "
            "39.47 min baseline, –72%); average counter busy-time 76.09%; "
            "dominated Alt II (9.07 min wait) on utilization and Alt III.a on cost"
        ),
        source_location="Table VII, p.214 (Alt IV output: waiting time 10.88 min, counter 3 busy-time 76.72%) and Table II, p.213 (baseline waiting time 39.47 min). Note: KB states 76.09% busy-time; Table VII shows 76.72% for counter 3 — minor rounding difference, confirmed as same result.",
    ),
    LiteratureReference(
        paper_id=18,
        authors=(
            "Fun, W.H.; Tan, E.H.; Khalid, R.; Sararaks, S.; Tang, K.F.; "
            "Ab Rahim, I.; Md. Sharif, S.; Jawahir, S.; Sibert, R.M.Y.; "
            "Nawawi, M.K.M."
        ),
        year=2022,
        title="Applying Discrete Event Simulation to Reduce Patient Wait Times and Crowding: The Case of a Specialist Outpatient Clinic with Dual Practice System",
        domain="healthcare",
        key_finding=(
            "Matching consultation start time with staggered patient arrival "
            "(even distribution per 30-min slot) reduced overall turnaround time "
            "for public and private patients simultaneously without adding "
            "resources; scenario without congruent consultation start time "
            "yielded much smaller gains."
        ),
        parameters_tested=["inter_arrival_time", "events_configuration"],
        quantitative_result=(
            "Up to 40% overall TT reduction for public patients and 21% for "
            "private patients; 10-21% reduction in patients waiting per hour "
            "during peak hours; 45% TT reduction for public when combined with "
            "reduced slot size (7 patients)."
        ),
        source_location="Section 3.3, p.9 — exact quotes: 'Reduction of 40% for public and 21% for private patients (Scenario 3)' and 'Overall TT reduction of 45% for public patients can be achieved if the number of staggered arrivals is reduced to seven public patients per time slot (Scenario 5).' Peak-hour 10-21% crowding reduction not explicitly quantified in pages 1-10.",
    ),
    LiteratureReference(
        paper_id=19,
        authors="Ivan, J.; Rooney, S.; Carlson, H.; Bentley, S.; Fisher, D.; Angelopoulou, A.",
        year=2021,
        title="The Impact of the Constraints of Class Scheduling on Campus Dining: A Simulation-based Case Study",
        domain="service_operations",
        key_finding=(
            "Adding a permanently-staffed second checkout register and "
            "distributing class end-times evenly across the lunch window both "
            "independently reduce wait times and balking in a constrained "
            "campus-dining service; uneven class schedules concentrating demand "
            "at 1pm produced the worst customer outcomes."
        ),
        parameters_tested=["resource_count", "inter_arrival_time"],
        quantitative_result=(
            "Adding a second full-time register: satisfaction +3.5 (on "
            "~10-point scale), service time -2.68 minutes on average, balking "
            "reduced from max 38 to max 20 students per scenario; best class "
            "schedule served 57/56/53/30 students across 11am-2pm slots vs "
            "worst at 57/17/64/58."
        ),
        source_location="Section 4.2, pp.269-270 — exact quote: 'With two full-time servers at checkout, there is an immediate increase in satisfaction by an average of 3.5 and an overall reduction in service time by an average of 2.68 minutes. Additionally, balking was heavily reduced with a maximum of 3 and a maximum of 38 and 20 people balking with one server.' NOTE: KB previously had -4.68 min (wrong) and max 18 (wrong) — corrected to -2.68 min and max 20 from paper.",
    ),
    LiteratureReference(
        paper_id=20,
        authors="Duguay, C.; Chetouane, F.",
        year=2007,
        title="Modeling and Improving Emergency Department Systems using Discrete Event Simulation",
        domain="healthcare",
        key_finding=(
            "Adding one physician and one nurse with an 8:00-16:00 shift reduced "
            "patient waiting time from registration to available exam room by up "
            "to 2 hours and increased daily throughput by 16 patients; adding "
            "examination rooms without matching clinical staff produced no "
            "improvement."
        ),
        parameters_tested=["resource_count", "resource_calendar"],
        quantitative_result=(
            "Up to 2-hour reduction in waiting time T3 (registration to exam "
            "room); 16 additional patients treated in the 8am-8pm window; room "
            "utilisation still at 90% after the intervention, confirming rooms "
            "were not the constraint."
        ),
        source_location="Section 7, pp.317-318 — exact quotes: 'A decrease of up to 2 h was achieved by these alternatives compared to the actual situation in the ED' and 'increases the number of patients treated between 0800 and 2000 h by an average of 16 patients.' Room utilization ~85-90% range confirmed from Figure 9.",
    ),
    LiteratureReference(
        paper_id=21,
        authors="Pihir, I.; Žajdela Hrustek, N.; Dušak, V.",
        year=2010,
        title="Survey of Simulation Capabilities of the IBM WebSphere Business Modeler Business Process Modeling Tool on the Example of Processing a Loan Application",
        domain="finance",
        key_finding=(
            "Adding a new 'approve-with-additional-insurance' branch to the "
            "loan-application process converted 70% of previously-rejected "
            "applications into approved ones, raising the approval rate from "
            "50% to 85%; this increased revenue by 70% and profit by 88% at the "
            "cost of only 4% longer processing time."
        ),
        parameters_tested=["gateway_probabilities", "activity_duration"],
        quantitative_result=(
            "Approval rate rose from 50% to 85%; average revenue per case +70% "
            "(730 kn → 1241 kn); average profit per case +88% (573 kn → 1078 kn); "
            "average duration +4% (179 → 186 min); average cost +4%."
        ),
        source_location="Table 3, p.648 (TO BE state raw values) and Section 6, p.649 — exact quotes: 'the approval rate rose from 50% to 85%', 'revenue increased by 70%...from 730 kunas to 1241.19 kunas', 'profit increased by 88%...from 573.38 kunas to 1078.15 kunas', 'average duration time increased by 4% (7 min)...from 2 hours and 59 minutes (179 minutes) to 3 hours and 6 minutes (186 minutes)'.",
    ),
    LiteratureReference(
        paper_id=22,
        authors="Su, Q.; Yao, X.; Su, P.; Shi, J.; Zhu, Y.; Xue, L.",
        year=2010,
        title="Hospital Registration Process Reengineering Using Simulation Method",
        domain="healthcare",
        key_finding=(
            "Replacing three parallel multi-queue/multi-server registration lines "
            "with a single-queue/multi-server layout plus a small prepare-queue "
            "of 16 patients near the counters reduced maximum total registration "
            "time from over 50 minutes to 8 minutes, with an optimum found "
            "through stepwise simulation experiments on prepare-queue length."
        ),
        parameters_tested=["events_configuration", "activity_duration"],
        quantitative_result=(
            "Maximum waiting time reduced from 50+ minutes to 8 minutes; average "
            "waiting time reduced from 17.24 minutes to 3.15 minutes; optimal "
            "prepare-queue size = 16 persons (balancing total time and "
            "time-on-seat); eliminating forgotten pre-check alone reduced "
            "average waiting time by 8.5% and max by 25.7%."
        ),
        source_location="Table 1, p.75 (pre-check scenario: avg 17.24 → 15.77 min, max 54.62 → 40.57 min, -8.5%/-25.7% confirmed). Full redesign results (50+ → 8 min, avg → 3.15 min, prepare-queue size = 16) appear in Results section beyond p.10 — verify against later pages.",
    ),
]


# ===================================================================
# 2. PARAMETER TAXONOMY
#    Restructured from SimulationParameters.csv with added SIMOD
#    field cross-references and differentiation flags.
# ===================================================================

PARAMETERS: list[SimulationParameter] = [
    # --- Process-model parameters ---
    SimulationParameter(
        name="activity_duration",
        category=ParameterCategory.PROCESS_MODEL,
        description=(
            "Duration distribution for each activity in the process. "
            "Real processes follow statistical distributions, not fixed times."
        ),
        value_type="distribution",
        unit="hours or minutes",
        constraints="mean > 0; std >= 0 for normal; rate > 0 for exponential",
        examples=[
            "normal(mean=1.5h, std=0.4h)",
            "exponential(mean=4.0h)",
            "fixed(0.1h)",
        ],
        simod_fields=[
            SimodFieldMapping(
                simod_json_path="task_durations.*",
                description="Per-activity duration distribution with parameters",
            ),
        ],
        supports_differentiation=True,
    ),
    SimulationParameter(
        name="inter_arrival_time",
        category=ParameterCategory.PROCESS_MODEL,
        description=(
            "Frequency and timing of new case arrivals into the process. "
            "Controls workload volume and can model peak/off-peak patterns."
        ),
        value_type="distribution",
        unit="hours between arrivals",
        constraints="mean > 0",
        examples=[
            "exponential(mean=2.4h)",
            "Patients arrive every 7 min on average (Poisson)",
        ],
        simod_fields=[
            SimodFieldMapping(
                simod_json_path="arrival_distribution",
                description="Case arrival distribution type and parameters",
            ),
        ],
        supports_differentiation=True,
    ),
    SimulationParameter(
        name="gateway_probabilities",
        category=ParameterCategory.PROCESS_MODEL,
        description=(
            "Branching probabilities at XOR/OR decision gateways. "
            "Controls which path a case takes through the process."
        ),
        value_type="probability",
        unit="probability (0-1)",
        constraints="All outgoing probabilities from a gateway must sum to 1.0",
        examples=[
            "Approved: 0.65, Rejected: 0.35",
            "70% straight to approval, 30% manual review",
        ],
        simod_fields=[
            SimodFieldMapping(
                simod_json_path="gateway_probabilities.*",
                description="Per-gateway outcome probabilities",
            ),
        ],
        supports_differentiation=True,
    ),
    SimulationParameter(
        name="events_configuration",
        category=ParameterCategory.PROCESS_MODEL,
        description=(
            "Configuration of intermediate events (timers, errors, cancellations) "
            "that can interrupt or redirect the normal flow."
        ),
        value_type="configuration",
        unit="",
        constraints="Must reference valid events in the BPMN model",
        examples=[
            "Patient leaves if waiting > 90 min",
            "Order cancelled if not processed within 24h",
        ],
        simod_fields=[],
        supports_differentiation=False,
    ),

    # --- Resource parameters ---
    SimulationParameter(
        name="resource_count",
        category=ParameterCategory.RESOURCE,
        description=(
            "Number of individual resources (workers, machines) available "
            "in each resource pool. Directly impacts throughput capacity "
            "and waiting times."
        ),
        value_type="integer",
        unit="count",
        constraints=(
            ">= 1. For human resource pools, scale headcount to meet weekly "
            "workload demand at ~40h/week per person (max ~48h/week with "
            "overtime). If demand implies more than one person's standard "
            "weekly hours, INCREASE this count rather than extending a "
            "single resource's calendar beyond labor norms."
        ),
        examples=[
            "3 Loan Officers",
            "6 CT machines",
            "2 Senior Managers",
            "2 Pharmacists at 40h/week each (instead of 1 at 80h/week)",
        ],
        simod_fields=[
            SimodFieldMapping(
                simod_json_path="resource_profiles.*.count",
                description="Number of resources per role/pool",
            ),
        ],
        supports_differentiation=True,
    ),
    SimulationParameter(
        name="resource_calendar",
        category=ParameterCategory.RESOURCE,
        description=(
            "Working hours and availability schedules for each resource role. "
            "Includes shift patterns, days of week, and break times. "
            "Detailed calendar modelling significantly affects simulation "
            "accuracy for cycle time distributions (Lopez-Pintado et al., 2024)."
        ),
        value_type="schedule",
        unit="",
        constraints=(
            "Valid time ranges; no overlapping shifts unless intended. "
            "For human resources, each individual's calendar must respect "
            "labor norms: ~40h/week standard, max ~48h/week including "
            "overtime (EU Working Time Directive and equivalent in most "
            "jurisdictions). To deliver more weekly capacity than one "
            "person can sustainably provide, increase resource_count "
            "instead of extending a single resource's hours. "
            "Machines/non-human resources may run 24/7."
        ),
        examples=[
            "Mon-Fri 08:00-17:00 (40h/week, standard)",
            "24/7 shift rotation (machines, or multi-person rotating crews)",
            "Mon-Sat 07:00-19:00 (warehouse, multiple shifts)",
        ],
        simod_fields=[
            SimodFieldMapping(
                simod_json_path="calendars.*",
                description="Per-calendar day/hour availability windows",
            ),
        ],
        supports_differentiation=True,
    ),
    SimulationParameter(
        name="resource_activity_assignment",
        category=ParameterCategory.RESOURCE,
        description=(
            "Which resource roles are responsible for which activities. "
            "Reassigning activities across roles can unlock capacity or "
            "enable specialisation."
        ),
        value_type="assignment",
        unit="",
        constraints="Every activity must have at least one assigned resource role",
        examples=[
            "Senior Loan Officers handle high-value applications",
            "L1 Support handles initial triage, L2 handles escalations",
        ],
        simod_fields=[
            SimodFieldMapping(
                simod_json_path="resource_profiles",
                description="Resource-to-activity mapping (implicit in pool structure)",
            ),
        ],
        supports_differentiation=True,
    ),
    SimulationParameter(
        name="resource_cost",
        category=ParameterCategory.RESOURCE,
        description="Hourly or per-unit cost of employing each resource role.",
        value_type="float",
        unit="currency per hour",
        constraints=">= 0",
        examples=[
            "Doctor = 100/h, Nurse = 50/h",
            "Senior Engineer = 85/h",
        ],
        simod_fields=[
            SimodFieldMapping(
                simod_json_path="resource_profiles.*.cost_per_hour",
                description="Per-role hourly cost",
            ),
        ],
        supports_differentiation=False,
    ),

    # --- Scenario parameters ---
    SimulationParameter(
        name="simulation_instances",
        category=ParameterCategory.SCENARIO,
        description=(
            "Number of cases to simulate. Higher counts improve statistical "
            "confidence but increase runtime."
        ),
        value_type="integer",
        unit="cases",
        constraints=">= 100 for meaningful statistics",
        examples=[
            "1000 patients",
            "500 production orders per month",
        ],
        simod_fields=[],
        supports_differentiation=False,
    ),
]


# ===================================================================
# 3. GOAL-TO-PARAMETER MAPPINGS
#    Each entry links a concrete goal (from the literature) to the
#    parameter changes that were effective, with paper attribution.
# ===================================================================

GOAL_MAPPINGS: list[GoalParameterMapping] = [
    # ==========================================================
    # ----- Baseline mappings (papers 1-7) -----
    # ==========================================================

    # ----- Waiting Time -----
    GoalParameterMapping(
        goal_description="Reduce waiting time by changing resource assignments",
        goal_category=GoalCategory.WAITING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_activity_assignment",
                direction=ChangeDirection.REASSIGN,
                rationale=(
                    "Adding a dedicated transporter role reduced ED CT access "
                    "time by 10 minutes by removing resource contention."
                ),
                paper_ids=[7],
                quantitative_evidence="10-min reduction, 80% compliance with 30-min goal",
            ),
            ParameterChange(
                parameter_name="resource_count",
                direction=ChangeDirection.INCREASE,
                rationale="Adding dedicated resources to bottleneck activities directly reduces queue waiting.",
                paper_ids=[7],
                quantitative_evidence="1 additional transporter was sufficient",
            ),
        ],
        domain="healthcare",
    ),
    GoalParameterMapping(
        goal_description="Reduce average waiting time through queue management and faster processing",
        goal_category=GoalCategory.WAITING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="activity_duration",
                direction=ChangeDirection.DECREASE,
                rationale=(
                    "Electronic queue management and IT-based document processing "
                    "reduced activity durations at the bottleneck."
                ),
                paper_ids=[6],
                quantitative_evidence="Part of combined 90% cycle-time reduction",
            ),
            ParameterChange(
                parameter_name="resource_count",
                direction=ChangeDirection.INCREASE,
                rationale="Additional staffing at the checkpoint relieved congestion.",
                paper_ids=[6],
            ),
            ParameterChange(
                parameter_name="resource_calendar",
                direction=ChangeDirection.INCREASE,
                rationale="Extended working hours expanded available processing windows.",
                paper_ids=[6],
            ),
        ],
        domain="logistics",
    ),
    GoalParameterMapping(
        goal_description="Reduce patient waiting times through resource reallocation under budget constraints",
        goal_category=GoalCategory.WAITING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_count",
                direction=ChangeDirection.REDISTRIBUTE,
                rationale=(
                    "Reducing nurses from 3 to 2 while adding 1 resident (budget-neutral) "
                    "achieved 48% waiting time reduction — sometimes redistributing resources "
                    "is more effective than simply adding more."
                ),
                paper_ids=[5],
                quantitative_evidence="48% reduction (44 to 23 min)",
            ),
        ],
        domain="healthcare",
    ),
    GoalParameterMapping(
        goal_description="Reduce time-consuming call handling by decreasing unnecessary arrivals",
        goal_category=GoalCategory.WAITING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="inter_arrival_time",
                direction=ChangeDirection.DECREASE,
                rationale=(
                    "Eliminating phone-based incident entry (centralising email) "
                    "removed an entire arrival channel, reducing total incoming volume."
                ),
                paper_ids=[3],
                quantitative_evidence="100% elimination of L1 processing time",
            ),
            ParameterChange(
                parameter_name="activity_duration",
                direction=ChangeDirection.DECREASE,
                rationale="Automating first-level support activities reduced processing time.",
                paper_ids=[3],
                quantitative_evidence="10.7% reduction in L2 processing time",
            ),
        ],
        domain="it_service_management",
    ),

    # ----- Processing Time -----
    GoalParameterMapping(
        goal_description="Reduce activity processing time directly",
        goal_category=GoalCategory.PROCESSING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="activity_duration",
                direction=ChangeDirection.DECREASE,
                rationale=(
                    "Directly reducing the duration distribution of bottleneck "
                    "activities is the most targeted way to cut processing time."
                ),
                paper_ids=[3, 6, 7],
            ),
        ],
        domain="general",
    ),

    # ----- Cost -----
    GoalParameterMapping(
        goal_description="Minimize operational costs by reducing staff and working shifts",
        goal_category=GoalCategory.COST,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_count",
                direction=ChangeDirection.DECREASE,
                rationale="Fewer staff directly reduces payroll costs.",
                paper_ids=[],
            ),
            ParameterChange(
                parameter_name="resource_calendar",
                direction=ChangeDirection.DECREASE,
                rationale="Shorter working shifts reduce hourly costs.",
                paper_ids=[],
            ),
        ],
        domain="general",
        notes=(
            "Caution: reducing working hours can increase per-case cost if the "
            "same process takes longer — the in-use hours for resources may "
            "increase even as the calendar shrinks."
        ),
    ),

    # ----- Processing Capacity / Throughput -----
    GoalParameterMapping(
        goal_description="Increase processing capacity by adding resources",
        goal_category=GoalCategory.PROCESSING_CAPACITY,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_count",
                direction=ChangeDirection.INCREASE,
                rationale="More resources process more cases in parallel.",
                paper_ids=[5, 6],
            ),
        ],
        domain="general",
    ),
    GoalParameterMapping(
        goal_description="Increase throughput by extending available hours",
        goal_category=GoalCategory.THROUGHPUT,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_calendar",
                direction=ChangeDirection.INCREASE,
                rationale=(
                    "Extended working hours expand the processing window, "
                    "allowing more cases to complete per day."
                ),
                paper_ids=[1, 2, 6],
                quantitative_evidence="69.39% increase in orthodontic patients served (Paper 2, Al-Hawari dentistry clinic); Papers 1 and 6 also confirm extended-hours throughput gains",
            ),
        ],
        domain="general",
    ),
    GoalParameterMapping(
        goal_description="Increase production capacity through resource reassignment",
        goal_category=GoalCategory.PROCESSING_CAPACITY,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_activity_assignment",
                direction=ChangeDirection.REASSIGN,
                rationale=(
                    "Allowing operators to manage multiple machines simultaneously "
                    "achieved 40% capacity increase without adding headcount."
                ),
                paper_ids=[4],
                quantitative_evidence="40% production capacity increase",
            ),
            ParameterChange(
                parameter_name="activity_duration",
                direction=ChangeDirection.DECREASE,
                rationale="Reducing waste time in activities frees up capacity.",
                paper_ids=[4],
            ),
        ],
        domain="manufacturing",
    ),

    # ----- Resource Utilisation -----
    GoalParameterMapping(
        goal_description="Optimize resource utilisation by adjusting arrival rates",
        goal_category=GoalCategory.RESOURCE_UTILISATION,
        parameter_changes=[
            ParameterChange(
                parameter_name="inter_arrival_time",
                direction=ChangeDirection.DECREASE,
                rationale=(
                    "Increasing arrival frequency raises workload and utilisation. "
                    "In the CT-scan study, optimised scheduling increased workload "
                    "because cases arrived more frequently."
                ),
                paper_ids=[1],
            ),
        ],
        domain="healthcare",
        notes="Higher utilisation is not always better — watch for queue build-up.",
    ),
    GoalParameterMapping(
        goal_description="Optimize resource utilisation by adjusting capacity or calendars",
        goal_category=GoalCategory.RESOURCE_UTILISATION,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_count",
                direction=ChangeDirection.REDISTRIBUTE,
                rationale=(
                    "Reducing over-provisioned resource pools increases utilisation "
                    "of remaining resources. In the CT study, reduced schedule for "
                    "one role raised workload from 71% to 97%."
                ),
                paper_ids=[1, 2],
                quantitative_evidence="Utilisation increased from 71% to 97%",
            ),
            ParameterChange(
                parameter_name="resource_calendar",
                direction=ChangeDirection.REDISTRIBUTE,
                rationale="Concentrating working hours into busier periods raises utilisation.",
                paper_ids=[2],
            ),
        ],
        domain="general",
    ),

    # ----- Quality / Compliance (thesis extension - not in baseline) -----
    GoalParameterMapping(
        goal_description="Maintain or improve quality metrics while changing other parameters",
        goal_category=GoalCategory.QUALITY_COMPLIANCE,
        parameter_changes=[
            ParameterChange(
                parameter_name="gateway_probabilities",
                direction=ChangeDirection.REDISTRIBUTE,
                rationale=(
                    "Routing more cases through quality checks (increasing the "
                    "review/inspection branch probability) can improve quality at "
                    "the expense of throughput — useful as a constraint-satisfying lever."
                ),
                paper_ids=[],
            ),
            ParameterChange(
                parameter_name="resource_activity_assignment",
                direction=ChangeDirection.REASSIGN,
                rationale=(
                    "Assigning more experienced resources to quality-critical "
                    "activities can maintain accuracy while other parameters "
                    "are optimised for speed."
                ),
                paper_ids=[],
            ),
        ],
        domain="general",
        notes=(
            "Quality/compliance goals are typically constraints rather than "
            "primary objectives. Monitor rework rates and error probabilities "
            "when optimising for speed or cost."
        ),
    ),

    # ==========================================================
    # ----- Extended mappings (papers 8-22) -----
    # ==========================================================

    # ----- Lee et al. (2019) — paper 8 — MRI patient throughput -----
    GoalParameterMapping(
        goal_description="Reduce the waiting time for patients to undergo an MRI examination",
        goal_category=GoalCategory.WAITING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_count",
                direction=ChangeDirection.INCREASE,
                rationale=(
                    "Adding one radiographer during underutilized time slots "
                    "(11:00-19:00, covering lunch and dinner periods with fewer "
                    "patients) enabled additional examinations without significant "
                    "overtime, directly reducing the backlog and shortening wait "
                    "times by 2.51 days for outpatients who originally waited 30 days."
                ),
                paper_ids=[8],
                quantitative_evidence="Adding 1 radiographer reduced waiting time by 2 days and 8 hours (2.51 working days) for outpatients",
            ),
            ParameterChange(
                parameter_name="resource_calendar",
                direction=ChangeDirection.REDISTRIBUTE,
                rationale=(
                    "The study identified that time slots at 11:00 and 17:00 had "
                    "only 4 patient arrivals per hour compared to 6-7 in other "
                    "slots. Scheduling the additional radiographer specifically "
                    "for the 11:00-19:00 window targeted these underutilized "
                    "periods, maximizing scanner usage without extending overall "
                    "operating hours beyond 23:00."
                ),
                paper_ids=[8],
                quantitative_evidence="Targeting the 11:00-19:00 shift covered both low-volume periods (11:00 and 17:00 with 4 patients/hour vs 6-7 elsewhere)",
            ),
            ParameterChange(
                parameter_name="inter_arrival_time",
                direction=ChangeDirection.DECREASE,
                rationale=(
                    "The proposed model added 8 patients per day (4 at 11:00 and "
                    "4 at 17:00 slots) to fill underutilized scanner capacity. "
                    "This was the optimal number: adding more than 8 patients "
                    "caused statistically significant increases in daily overtime."
                ),
                paper_ids=[8],
                quantitative_evidence="8 additional patients/day (248/month) was the optimal increase; adding 10+ patients caused significant overtime increases (p<.05)",
            ),
        ],
        domain="healthcare",
        notes=(
            "The study used a hybrid DES + agent-based simulation in AnyLogic. "
            "The optimal configuration (+8 patients/day) was the highest volume "
            "that maintained statistically nonsignificant overtime (median=0 min, "
            "p=.3257) compared to baseline. Scanner A (3.0T) had lower utilization "
            "(50-55%) due to dual research use."
        ),
    ),
    GoalParameterMapping(
        goal_description="Increase the utilization rate of MRI scanners to reduce idle time",
        goal_category=GoalCategory.RESOURCE_UTILISATION,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_count",
                direction=ChangeDirection.INCREASE,
                rationale=(
                    "Scanners were idle during certain periods not because of "
                    "lack of demand but because radiographers were unavailable. "
                    "Adding one radiographer during 11:00-19:00 enabled scanners "
                    "B-E to be used more consistently, raising each scanner's "
                    "utilization from 74-76% to 81-83%."
                ),
                paper_ids=[8],
                quantitative_evidence="Scanner utilization increased to 81-83% for 1.5T scanners (from baseline of 74-76%), an average increase of 6.15%",
            ),
            ParameterChange(
                parameter_name="inter_arrival_time",
                direction=ChangeDirection.DECREASE,
                rationale=(
                    "Increasing patient arrivals during low-volume slots filled "
                    "idle scanner capacity. The FIFO scheduling with coil-type-based "
                    "scanner assignment ensured added patients were distributed "
                    "across available scanners."
                ),
                paper_ids=[8],
                quantitative_evidence="Adding 8 patients/day raised all 1.5T scanner utilization rates above 81%",
            ),
        ],
        domain="healthcare",
        notes=(
            "Utilization was calculated as each scanner's examination duration "
            "per day divided by 16 operating hours. The 3.0T scanner (A) remained "
            "at ~53% utilization due to its dual research/clinical use, which "
            "the intervention did not target."
        ),
    ),
    GoalParameterMapping(
        goal_description="Maximize the number of MRI patients examined per day within current business hours",
        goal_category=GoalCategory.THROUGHPUT,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_count",
                direction=ChangeDirection.INCREASE,
                rationale=(
                    "The bottleneck was radiographer availability, not scanner "
                    "capacity. One additional radiographer enabled 248 more "
                    "examinations per month (from 2,821 to 3,069) while keeping "
                    "overtime statistically nonsignificant."
                ),
                paper_ids=[8],
                quantitative_evidence="Monthly throughput increased from 2,821 to 3,069 patients (+248, ~8.8% increase) with 1 additional radiographer at US$1,546/month",
            ),
            ParameterChange(
                parameter_name="resource_calendar",
                direction=ChangeDirection.REDISTRIBUTE,
                rationale=(
                    "Rather than adding staff across all hours, concentrating the "
                    "additional radiographer in the 11:00-19:00 window specifically "
                    "targeted the two time slots with lowest patient volume, "
                    "converting idle scanner time into productive examination time."
                ),
                paper_ids=[8],
                quantitative_evidence="The 8-hour shift (11:00-19:00) for 1 radiographer enabled 8 additional patients/day at a cost of US$1,546/month",
            ),
        ],
        domain="healthcare",
        notes=(
            "Cost-effectiveness analysis showed the additional radiographer cost "
            "(US$1,546/month) was far exceeded by gross income generated "
            "(US$38,424-69,169/month depending on contrast agent usage mix). "
            "The income range reflects 42% non-contrast vs 58% contrast "
            "examination mix."
        ),
    ),

    # ----- Kristiana et al. (2026) — paper 9 — manufacturing FlexSim -----
    GoalParameterMapping(
        goal_description="Reduce queue waiting time caused by operator unavailability at bottleneck workstations",
        goal_category=GoalCategory.WAITING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_activity_assignment",
                direction=ChangeDirection.REASSIGN,
                rationale=(
                    "Operator 4, who had the lowest utilization rate (48.2%), was "
                    "reassigned to also serve Operation B, which had the highest "
                    "waiting-for-operator percentage (35.6%) and longest average "
                    "queue time (441,856s). This directly addressed the bottleneck "
                    "without hiring new staff."
                ),
                paper_ids=[9],
                quantitative_evidence="41.57% reduction in queue time at Operation B; 29.5% overall average queue time reduction",
            ),
        ],
        domain="manufacturing",
        notes=(
            "Trade-off observed: reassigning Operator 4 increased queue times at "
            "several operations previously handled solely by that operator, but "
            "the net system-wide effect was strongly positive. Operator utilization "
            "improved by an average of 8.5%."
        ),
    ),
    GoalParameterMapping(
        goal_description="Reduce total production time to increase production capacity",
        goal_category=GoalCategory.THROUGHPUT,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_activity_assignment",
                direction=ChangeDirection.REASSIGN,
                rationale=(
                    "By reallocating the least-utilized operator to the most "
                    "congested workstation, total production time for 2440 units "
                    "dropped from 366 days to 250 days, a low-cost strategy "
                    "requiring only better workforce coordination rather than "
                    "structural or technological changes."
                ),
                paper_ids=[9],
                quantitative_evidence="31.7% decrease in total production time (366 days to 250 days for 2440 units)",
            ),
        ],
        domain="manufacturing",
        notes="All production processes were manual (no machines), making operator allocation the dominant lever for improvement.",
    ),
    GoalParameterMapping(
        goal_description="Improve operator utilization rates to achieve more balanced workload distribution",
        goal_category=GoalCategory.RESOURCE_UTILISATION,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_activity_assignment",
                direction=ChangeDirection.REASSIGN,
                rationale=(
                    "Operators had highly uneven utilization ranging from 48.2% "
                    "to 73.7%. Reassigning the lowest-utilized operator to assist "
                    "at the bottleneck operation raised overall average utilization "
                    "by 8.5%, creating a more balanced workload distribution."
                ),
                paper_ids=[9],
                quantitative_evidence="Average operator utilization increased by 8.5%",
            ),
        ],
        domain="manufacturing",
        notes="Operators in this medium-scale industry performed both primary production tasks and non-primary activities, which contributed to utilization imbalance.",
    ),

    # ----- Aeenparast et al. (2013) — paper 10 — outpatient waiting time -----
    GoalParameterMapping(
        goal_description="Reduce outpatient waiting time for physician examination in a hospital clinic",
        goal_category=GoalCategory.WAITING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_calendar",
                direction=ChangeDirection.REDISTRIBUTE,
                rationale=(
                    "Over half of patient waiting time occurred before physicians "
                    "arrived at the clinic. Changing resident physician start time "
                    "from 10:00 to 9:00 AM (extending attendance from 200 to 260 "
                    "minutes) and senior staff physician start time from 10:45 to "
                    "10:00 AM (extending from 100 to 160 minutes) dramatically "
                    "reduced the gap between patient arrival and physician "
                    "availability. Combined with shifting patient admission start "
                    "from 7:30 to 8:00 AM, this achieved the best results among "
                    "all 10 scenarios tested."
                ),
                paper_ids=[10],
                quantitative_evidence="71.4% reduction in weighted mean waiting time (55.36 to 15.83 minutes); scenario 9 was the best among 10 tested",
            ),
            ParameterChange(
                parameter_name="resource_count",
                direction=ChangeDirection.INCREASE,
                rationale=(
                    "Increasing novice residents from 2 to 3 and experienced "
                    "residents from 1 to 2 reduced waiting time but was less "
                    "effective than schedule changes alone. Comparison of scenario "
                    "6 (adding staff) vs scenario 7 (changing schedules) showed "
                    "schedule changes were more impactful."
                ),
                paper_ids=[10],
                quantitative_evidence="Adding staff (scenario 6) reduced waiting to 44.79 min; changing schedules (scenario 7) reduced to 23.02 min — schedule changes were nearly twice as effective",
            ),
        ],
        domain="healthcare",
        notes=(
            "Key insight: adjusting physician work schedules was more effective "
            "than increasing physician numbers. The study used AweSim with 1000 "
            "replications per scenario. Weighted means accounted for different "
            "patient volumes across physician levels (weight 2 for residents, "
            "weight 1 for senior staff)."
        ),
    ),

    # ----- Rashed et al. (2023) — paper 11 — bank service time -----
    GoalParameterMapping(
        goal_description="Reduce customer waiting time in bank queuing system during peak hours",
        goal_category=GoalCategory.WAITING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_activity_assignment",
                direction=ChangeDirection.REASSIGN,
                rationale=(
                    "The cash debit section had two servers with low utilization "
                    "(0.61 and 0.47 at peak), while the cash credit university "
                    "section had one server at 0.95 utilization with 12.64 min "
                    "average wait. Reassigning the second cash debit server to "
                    "serve cash credit university during peak hours reduced the "
                    "bottleneck wait from 12.64 to 3.38 minutes without "
                    "additional staffing costs."
                ),
                paper_ids=[11],
                quantitative_evidence="Waiting time reduced from 12.64 to 3.38 min (73% reduction); queue length from 8.54 to 2.04 (76% reduction)",
            ),
        ],
        domain="finance",
        notes=(
            "Trade-off: cash debit section waiting time increased from 3.21 to "
            "4.54 minutes (peak) and 1.18 to 2.10 minutes (off-peak), but this "
            "was minor compared to the bottleneck reduction. Arena simulation "
            "with 10 replications was used."
        ),
    ),
    GoalParameterMapping(
        goal_description="Improve server utilization rates in banking service counters",
        goal_category=GoalCategory.RESOURCE_UTILISATION,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_activity_assignment",
                direction=ChangeDirection.REASSIGN,
                rationale=(
                    "The cash debit second server had only 0.47 utilization at "
                    "peak time and 0.25 at off-peak. By consolidating cash debit "
                    "service to a single server and redeploying the freed server "
                    "to the overloaded cash credit section, utilization improved "
                    "across both sections without adding resources."
                ),
                paper_ids=[11],
                quantitative_evidence="Cash debit server utilization increased from 0.36 to 0.59 (off-peak) and 0.60 to 0.73 (peak)",
            ),
        ],
        domain="finance",
        notes="2-6% difference observed between arithmetic and simulated utilization due to random entity generation in Arena for 500+ entities beyond collected sample.",
    ),

    # ----- Shim & Kumar (2010) — paper 12 — emergency care reengineering -----
    GoalParameterMapping(
        goal_description="Reduce patient wait times in the hospital emergency care process",
        goal_category=GoalCategory.WAITING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_count",
                direction=ChangeDirection.ADD,
                rationale=(
                    "Adding a second payment station between registration and "
                    "triage allowed standard-fee patients to pay early and bypass "
                    "the end-of-process payment station, reducing the payment "
                    "queue wait from 1.12 to 0.48 minutes. Adding a short-stay "
                    "ward (EDTC) for patients needing less than one day of "
                    "observation freed capacity in the observation room, reducing "
                    "triage wait from 2.29 to 0.09 minutes."
                ),
                paper_ids=[12],
                quantitative_evidence="Total wait at work stations reduced by 41% (6.86 to 5.01 min); triage wait reduced by 2.20 min; payment wait reduced by 0.64 min",
            ),
            ParameterChange(
                parameter_name="gateway_probabilities",
                direction=ChangeDirection.REDISTRIBUTE,
                rationale=(
                    "The new process split the payment flow: all PAC 2-4 patients "
                    "first pay the standard fee at the new station, then only "
                    "those owing additional fees proceed to the second payment "
                    "station. PAC 1 patients bypass the first station entirely. "
                    "Similarly, observation patients were split between the new "
                    "short-stay ward (<1 day) and existing observation room (>1 day)."
                ),
                paper_ids=[12],
                quantitative_evidence="PAC 1 wait time eliminated entirely (0.27 to 0.00 min); PAC 2 wait reduced by 7.80 min",
            ),
        ],
        domain="healthcare",
        notes=(
            "Trade-off: PAC 3 and PAC 4 patient wait times increased by 6.01 "
            "minutes after the changes, but this was offset by significant "
            "reductions for higher-acuity PAC 1 and PAC 2 patients who need more "
            "immediate treatment. 74% of PAC 1 and 47% of PAC 2 patients get "
            "hospitalized vs only 14% of PAC 3. SIMUL8 was used with 100 "
            "independent replications. Validated at 95% confidence level."
        ),
    ),

    # ----- Srinivas et al. (2021) — paper 13 — BPR for ED efficiency -----
    GoalParameterMapping(
        goal_description="Reduce emergency department patient waiting time through triage process redesign",
        goal_category=GoalCategory.WAITING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_activity_assignment",
                direction=ChangeDirection.REASSIGN,
                rationale=(
                    "Under the current process, doctors handled all level 1-3 "
                    "patients (over 80% of arrivals) while physician assistants "
                    "only handled levels 4-5 (under 20%), resulting in 86.4% "
                    "doctor utilization vs 12.1% physician assistant utilization. "
                    "Evenly distributing level 3 patients (55% of all arrivals) "
                    "between doctors and physician assistants balanced workload "
                    "and reduced the bottleneck at the doctor assessment stage."
                ),
                paper_ids=[13],
                quantitative_evidence="20% reduction in average waiting time (89.9 to ~72 min); physician assistant utilization increased from 12.1% to ~35%",
            ),
        ],
        domain="healthcare",
        notes=(
            "This was the recommended short-term solution due to minimal cost "
            "(estimated $10,000 for triage protocol change and training), ease "
            "of implementation, and no disruption to existing operations. Risk "
            "exists for complicated level 3 cases being assigned to physician "
            "assistants, mitigable through proper triage protocol."
        ),
    ),
    GoalParameterMapping(
        goal_description="Achieve maximum reduction in ED waiting time through combined process change and workforce optimization",
        goal_category=GoalCategory.WAITING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_activity_assignment",
                direction=ChangeDirection.REASSIGN,
                rationale=(
                    "Evenly distributing level 3 patients between doctors and "
                    "physician assistants addressed the fundamental workload "
                    "imbalance that caused the bottleneck."
                ),
                paper_ids=[13],
                quantitative_evidence="Combined with workforce optimization: 84% reduction in waiting time (89.9 to ~14.3 min)",
            ),
            ParameterChange(
                parameter_name="resource_calendar",
                direction=ChangeDirection.REDISTRIBUTE,
                rationale=(
                    "Optimizing the number of health professionals per shift "
                    "based on historical demand patterns (using OptQuest in Simio) "
                    "while constraining utilization to 70-80%. The optimal plan "
                    "downsized nurses and physicians by one in Shift 2 (8pm-8am) "
                    "and added two physician assistants in Shift 1 (8am-8pm), "
                    "matching capacity to the demand curve."
                ),
                paper_ids=[13],
                quantitative_evidence="Balanced utilization across all resources at 70-80% range; robust to 10-30% demand increases",
            ),
        ],
        domain="healthcare",
        notes=(
            "Recommended as the long-term solution. Scenario 1 (hospital's own "
            "plan to add 25 beds + 1 nurse at $3.88M fixed + $342K/yr recurring) "
            "yielded only 3% improvement because it added capacity at "
            "non-bottleneck resources. Scenario 5 actually saved $197K/yr for "
            "current demand by right-sizing staff. Simio simulation with 50 "
            "replications, 365-day run length, 30-day warm-up."
        ),
    ),
    GoalParameterMapping(
        goal_description="Balance resource utilization to reduce physician burnout in emergency department",
        goal_category=GoalCategory.RESOURCE_UTILISATION,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_activity_assignment",
                direction=ChangeDirection.REASSIGN,
                rationale=(
                    "Doctor utilization was 86.4% (associated with burnout risk "
                    "— over 60% of emergency physicians experience burnout) while "
                    "physician assistant utilization was only 12.1%. Redistributing "
                    "level 3 cases to physician assistants balanced the workload "
                    "without compromising care quality, as physician assistants "
                    "are trained for most level 3 cases."
                ),
                paper_ids=[13],
                quantitative_evidence="Doctor utilization reduced from 86.4% to ~75%; physician assistant utilization increased from 12.1% to ~55% (Scenario 2); Scenario 5 achieved all resources in 70-80% range",
            ),
            ParameterChange(
                parameter_name="resource_count",
                direction=ChangeDirection.INCREASE,
                rationale=(
                    "Adding one doctor during Shift 1 (the busier daytime shift) "
                    "reduced the primary bottleneck but did not address the "
                    "utilization imbalance alone — it needed to be combined with "
                    "the process change."
                ),
                paper_ids=[13],
                quantitative_evidence="Scenario 3 (add 1 doctor): 47% waiting time reduction but physician assistant utilization remained at 12.2%",
            ),
        ],
        domain="healthcare",
        notes=(
            "Hospital's capacity expansion plan (Scenario 1: 25 more beds, 1 "
            "nurse) did not significantly improve utilization balance because "
            "the bottleneck was doctors, not beds. Bed utilization was already "
            "only 37.3% (trauma) and 58.4% (general)."
        ),
    ),

    # ----- Marchesi et al. (2025) — paper 14 — stochastic physician scheduling -----
    GoalParameterMapping(
        goal_description="Reduce emergency department waiting time and length of stay through optimized physician scheduling that accounts for uncertain patient arrivals",
        goal_category=GoalCategory.WAITING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_calendar",
                direction=ChangeDirection.REDISTRIBUTE,
                rationale=(
                    "The manually-defined schedule did not account for demand "
                    "fluctuation across hours and days, creating gaps between "
                    "capacity and demand. The stochastic optimization model used "
                    "time-varying Poisson arrival rates (statistically different "
                    "by hour and day of week, confirmed by Kruskal-Wallis test) "
                    "to align physician shift start times with demand patterns. "
                    "Allowing more flexible shift start times for 6h and 9h shifts "
                    "enabled better coverage during high-demand periods. "
                    "Cardiologists (scarce resource) were allocated to longer "
                    "shifts for greater coverage."
                ),
                paper_ids=[14],
                quantitative_evidence="Overall average waiting time: 54.6 to 16.8 min (69% reduction); LOS: 102.1 to 64.3 min (37% reduction); first assessment queue frequency: 31.14% to 12.16%",
            ),
        ],
        domain="healthcare",
        notes=(
            "The model used SAA with Latin Hypercube Sampling (100 scenarios, "
            "gap <1%), applied to 72,988 patient visits and 85 physicians over "
            "10 months. Even without flexible shifts (CURR scenario using "
            "existing shift structure), the optimization model reduced LOS from "
            "102 to 95 min and queue frequency from 31% to 21.5%, showing that "
            "better allocation alone (without new shift options) provides "
            "significant improvement. Including 1-hour breaks increased LOS by "
            "40% compared to no-breaks but still outperformed manual scheduling "
            "with breaks by 29%."
        ),
    ),
    GoalParameterMapping(
        goal_description="Optimize physician staffing levels across multiple emergency department treatment stages",
        goal_category=GoalCategory.RESOURCE_UTILISATION,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_calendar",
                direction=ChangeDirection.REDISTRIBUTE,
                rationale=(
                    "The model simultaneously optimized staffing across triage, "
                    "first general assessment, medication, observation hold, and "
                    "subsequent evaluation stages. By considering transfer rates "
                    "between stages and bed/chair capacity constraints, it "
                    "determined the number of physicians needed per hour across "
                    "all stages, reducing over-staffing in low-demand periods "
                    "and under-staffing during peaks."
                ),
                paper_ids=[14],
                quantitative_evidence="First assessment scheduled utilization changed from 51.6% (manual) to 41.59% (proposed) while simultaneously reducing queue frequency from 31.14% to 12.16%; subsequent evaluation utilization decreased from 65.35% to 47.12% with queue frequency dropping from 45.48% to 21.09%",
            ),
        ],
        domain="healthcare",
        notes=(
            "Sensitivity analysis showed robustness: with 25-50% service time "
            "increases, proposed schedule still outperformed manual allocation. "
            "With 5-15% physician reductions, the model gracefully degraded "
            "while maintaining superiority over manual scheduling. The model "
            "handled both cyclic and acyclic staffing patterns with a "
            "user-definable planning horizon."
        ),
    ),

    # ----- Gharahighehi et al. (2016) — paper 15 — ED DES/DEA/MADM -----
    GoalParameterMapping(
        goal_description="Reduce waiting time for emergency department patients across triage levels",
        goal_category=GoalCategory.WAITING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_activity_assignment",
                direction=ChangeDirection.REASSIGN,
                rationale=(
                    "Reassigning queue dispatching rules so that patient/specimen "
                    "queues are served by severity (ESI level) rather than FIFO "
                    "gave the highest-ranked improvement in VIKOR ranking; acute "
                    "patients are pulled ahead of lower-severity cases at every "
                    "shared resource."
                ),
                paper_ids=[15],
                quantitative_evidence="~5% reduction in acute patient waiting time, zero added cost",
            ),
            ParameterChange(
                parameter_name="resource_count",
                direction=ChangeDirection.INCREASE,
                rationale=(
                    "Adding inpatient beds, MRI reception staff, MRI "
                    "radiologist+typist, ultrasonography staff, or an additional "
                    "pathologist each relieved a distinct bottleneck identified "
                    "in the simulation; scenarios adding MRI-side staff and a "
                    "pathologist emerged as DEA-efficient."
                ),
                paper_ids=[15],
                quantitative_evidence="MRI reception (S7), MRI reception+radiologist+typist (S8), and added pathologist (S10) were all DEA-efficient (score=1); bed utilization near 1.0 was flagged as a systemic bottleneck",
            ),
            ParameterChange(
                parameter_name="resource_calendar",
                direction=ChangeDirection.INCREASE,
                rationale=(
                    "Extending the CT scan radiologist shift from 4 to 8 hours, "
                    "and adding 12-hour triage-nurse / GP shifts, were tested as "
                    "capacity expansions via working-time rather than headcount; "
                    "these reduced non-homogeneous peak-hour congestion observed "
                    "between 10 p.m. and 11 p.m."
                ),
                paper_ids=[15],
                quantitative_evidence="Scenarios S3, S4, S5, and S9 tested shift extensions; none dominated S6 alone but contributed to efficient DEA frontiers",
            ),
        ],
        domain="healthcare",
        notes=(
            "Trade-off: increasing utilization toward 1.0 sharply worsens "
            "waiting time and LWOR — Delphi panel set optimal utilization "
            "targets of 0.7 (personnel) and 0.75 (tools), so capacity-add "
            "scenarios must respect these ceilings. Bed and MRI utilization "
            "near 1.0 were flagged as requiring strategic (not just operational) "
            "intervention."
        ),
    ),
    GoalParameterMapping(
        goal_description="Balance resource utilization across ED staff and equipment toward target levels",
        goal_category=GoalCategory.RESOURCE_UTILISATION,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_count",
                direction=ChangeDirection.INCREASE,
                rationale=(
                    "Resources running at utilization ≈1.0 (beds, MRI) drive "
                    "queue blow-ups; adding capacity to those pools is the only "
                    "way to bring utilization back toward the Delphi target band "
                    "(0.7 personnel / 0.75 tools) without starving other resources."
                ),
                paper_ids=[15],
                quantitative_evidence="Bed utilization ≈0.99 and MRI utilization =1.00 across all scenarios — identified as requiring tactical/strategic investment beyond the tested operational scenarios",
            ),
        ],
        domain="healthcare",
        notes="The paper explicitly argues utilization of 1.0 is not desirable; a Delphi panel was used to set trade-off points between idleness and waiting time.",
    ),

    # ----- Renna & Colonnese (2025) — paper 16 — university BPR -----
    GoalParameterMapping(
        goal_description="Reduce end-to-end processing time for administrative workflows in a public university",
        goal_category=GoalCategory.PROCESSING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="activity_duration",
                direction=ChangeDirection.DECREASE,
                rationale=(
                    "Replacing manual document routing, physical signatures, and "
                    "email-based handoffs with Bonita BPM automation and G-Drive "
                    "digital signatures collapses the normally-distributed "
                    "activity durations (e.g., 10–30 min mean per handoff step) "
                    "and removes the exponential inter-step waiting delays "
                    "(180 min) that dominated the AS-IS model."
                ),
                paper_ids=[16],
                quantitative_evidence="Average document handling time cut by 50%; manual interventions reduced by 65%; publication sub-process delay down 42%, comparative evaluation delay down 38%",
            ),
            ParameterChange(
                parameter_name="gateway_probabilities",
                direction=ChangeDirection.REDISTRIBUTE,
                rationale=(
                    "Dynamic resource prioritization protocols (committee-vs-office "
                    "routing) reshape the flow so that high-priority approval "
                    "paths skip queueing at the Academic Affairs Office "
                    "bottleneck — effectively redistributing flow through the "
                    "decision gateways that previously serialized every assignment."
                ),
                paper_ids=[16],
                quantitative_evidence="End-to-end time fell from ~46,200 to 30,040 minutes (≈96 → 62.5 working days)",
            ),
            ParameterChange(
                parameter_name="resource_count",
                direction=ChangeDirection.REDISTRIBUTE,
                rationale=(
                    "Sensitivity analysis showed the Academic Affairs Office was "
                    "the bottleneck; rebalancing workload toward a utilisation of "
                    "~58.8% (from a constrained baseline) through dynamic "
                    "prioritisation eliminated the pile-up at that single office "
                    "without hiring additional staff."
                ),
                paper_ids=[16],
                quantitative_evidence="At 30% resource availability the model still met AVA deadlines (≤90 days); a 10% reduction in evaluation-committee availability increased comparative-evaluation time by 14%, confirming the bottleneck was coordination, not headcount",
            ),
        ],
        domain="government",
        notes=(
            "The redesign maintained compliance with Italy's AVA accreditation "
            "framework and Law 240/2010 — efficiency gains did not come at the "
            "cost of regulatory transparency. Results assumed idealized "
            "stakeholder adherence; real-world cultural resistance may blunt gains."
        ),
    ),
    GoalParameterMapping(
        goal_description="Improve administrative staff utilization without overloading bottleneck roles",
        goal_category=GoalCategory.RESOURCE_UTILISATION,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_calendar",
                direction=ChangeDirection.REDISTRIBUTE,
                rationale=(
                    "Sensitivity analysis varied Academic Affairs Office "
                    "availability from 100% to 30%; rebalancing availability to "
                    "~50% still kept the office below saturation (58.8% "
                    "utilization) while compliance with AVA 90-day deadlines held "
                    "— showing the bottleneck pool can be scheduled leaner once "
                    "upstream automation removes its rework burden."
                ),
                paper_ids=[16],
                quantitative_evidence="Staff utilization rose from 7.2% (baseline) to 9.1% (TO-BE); Academic Office sustained 58.8% under 50% availability; at 30% availability end-to-end time only grew marginally to 30,396 min (≈63.3 days)",
            ),
            ParameterChange(
                parameter_name="activity_duration",
                direction=ChangeDirection.DECREASE,
                rationale=(
                    "Workflow automation removes idle handoff time from the "
                    "staff's workload profile, so the same headcount absorbs more "
                    "throughput per shift — utilization improves as a side-effect "
                    "of shorter per-case activity times rather than from "
                    "increased arrivals."
                ),
                paper_ids=[16],
                quantitative_evidence="22% utilization improvement alongside 28% throughput increase; standard deviations in duration dropped 12–18% (more predictable workload)",
            ),
            ParameterChange(
                parameter_name="resource_activity_assignment",
                direction=ChangeDirection.REASSIGN,
                rationale=(
                    "Integrating Bonita BPM to route approvals dynamically rather "
                    "than sequentially through fixed role chains lets the "
                    "Academic Affairs Office absorb work it previously waited on; "
                    "this rebalancing is what drives utilisation upward without "
                    "increasing headcount."
                ),
                paper_ids=[16],
                quantitative_evidence="Staff utilisation improved from 7.2% to 9.1% (22% relative increase); 65% reduction in manual interventions",
            ),
        ],
        domain="government",
        notes=(
            "Sensitivity finding: a 10% drop in Evaluation Committee availability "
            "increased comparative-evaluation time by 14% — stakeholder "
            "coordination, not just office staffing, drives the bottleneck."
        ),
    ),

    # ----- Madadi et al. (2013) — paper 17 — bank queuing system -----
    GoalParameterMapping(
        goal_description="Reduce average customer waiting time at bank counters while keeping utilization high",
        goal_category=GoalCategory.WAITING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_count",
                direction=ChangeDirection.INCREASE,
                rationale=(
                    "Opening a third counter (from 2 working to 3 working) "
                    "directly expands the server pool feeding the main queue Q2, "
                    "which was where virtually all of the 39.47-minute baseline "
                    "waiting time accrued; simulation confirmed the queue length "
                    "drop was large enough to justify the new teller."
                ),
                paper_ids=[17],
                quantitative_evidence="Average waiting time dropped from 39.47 min (baseline) to 9.07 min (Alt II: +1 counter) and 10.88 min (Alt IV: +1 counter, consolidated)",
            ),
            ParameterChange(
                parameter_name="resource_activity_assignment",
                direction=ChangeDirection.REASSIGN,
                rationale=(
                    "Removing the dedicated service-information table (which ran "
                    "at only 43.79% utilization) and reassigning type-3 customers "
                    "to the general counter pool pools capacity across all "
                    "customer classes, eliminating an underused specialized "
                    "server without harming service coverage."
                ),
                paper_ids=[17],
                quantitative_evidence="Service table utilization was 43.79% (idle 56.21%) — consolidating its workload into the 3-counter pool raised average counter busy-time to 76.09%",
            ),
            ParameterChange(
                parameter_name="resource_calendar",
                direction=ChangeDirection.REDISTRIBUTE,
                rationale=(
                    "Standardizing all three counters to the same 9:15–17:00 "
                    "shift (rather than staggered/partial shifts) eliminated the "
                    "late-day capacity cliff where one counter closed at 17:00 "
                    "while another continued, which had been causing the queue "
                    "to rebuild at shift change."
                ),
                paper_ids=[17],
                quantitative_evidence="Alt IV (standardized shifts) dominated Alt III.a (one counter on extended shift) on both utilization and implementation cost despite nearly identical waiting times",
            ),
            ParameterChange(
                parameter_name="activity_duration",
                direction=ChangeDirection.DECREASE,
                rationale=(
                    "Teller training reduces the mean exponential service time at "
                    "the counters (from 6.7/7.14 min to 6.2/6.5 min in Alt I), "
                    "shortening each service episode and thus the queue wait — "
                    "but the effect was weaker than adding a server."
                ),
                paper_ids=[17],
                quantitative_evidence="Alt I (training only): waiting time fell from 39.47 to 26.36 min (–13.11 min); clearly inferior to capacity-add alternatives which reached ~10 min",
            ),
        ],
        domain="finance",
        notes=(
            "The manager's decision criterion was a joint optimum of waiting "
            "time, utilization, and implementation cost — pure waiting-time "
            "minimization (Alt II) was rejected because utilization was too low; "
            "the winning alternative traded 1.8 min of extra wait for much "
            "better utilization and lower cost."
        ),
    ),
    GoalParameterMapping(
        goal_description="Increase counter utilization in a bank branch with underused specialized servers",
        goal_category=GoalCategory.RESOURCE_UTILISATION,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_activity_assignment",
                direction=ChangeDirection.REASSIGN,
                rationale=(
                    "Pooling the work of a specialized low-utilization server "
                    "(service information table at 43.79%) into the general "
                    "counter pool raises average busy-time across the remaining "
                    "servers, because demand for the specialized service was too "
                    "low to keep a dedicated resource busy."
                ),
                paper_ids=[17],
                quantitative_evidence="Average counter busy-time rose to 76.09% in Alt IV vs. ~56–62% in alternatives that preserved specialized separation",
            ),
        ],
        domain="finance",
        notes="Classic pooling-vs-specialization trade-off: specialization makes sense only when dedicated demand fills the server's shift; otherwise pooling wins on both utilization and waiting time.",
    ),

    # ----- Fun et al. (2022) — paper 18 — dual practice outpatient clinic -----
    GoalParameterMapping(
        goal_description="Reduce patient wait times in an outpatient clinic where different patient classes share resources",
        goal_category=GoalCategory.WAITING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="inter_arrival_time",
                direction=ChangeDirection.REDISTRIBUTE,
                rationale=(
                    "Replacing bulk/random early-morning arrivals with a "
                    "staggered arrival pattern (e.g., 10 public + 2 private "
                    "patients per 30-min slot) smooths demand against the fixed "
                    "number of consultation rooms and doctors, which are the "
                    "binding constraints; wait times at registration and between "
                    "process steps dominate total TT, so spreading arrivals "
                    "directly attacks the queue build-up."
                ),
                paper_ids=[18],
                quantitative_evidence="Staggered arrival combined with earlier consultation start yielded 40% TT reduction for public and 21% for private patients",
            ),
            ParameterChange(
                parameter_name="events_configuration",
                direction=ChangeDirection.DECREASE,
                rationale=(
                    "Shifting the consultation start time earlier (from ~9:00 to "
                    "8:15) so that doctors begin serving when patients actually "
                    "arrive eliminates the idle gap where registered patients "
                    "wait for clinic activities to begin; the paper shows this "
                    "alone (without staggering) already gives up to 36% "
                    "reduction, and it is essential — staggered arrival without "
                    "a congruent start time produced the weakest improvement."
                ),
                paper_ids=[18],
                quantitative_evidence="Earlier consultation start alone yielded up to 36% TT reduction for public patients; without matching consultation start time, staggering alone produced only ~14% reduction",
            ),
        ],
        domain="healthcare",
        notes=(
            "Trade-off observed: staggering public-patient arrival can increase "
            "private-patient TT when private services depend on public "
            "clearance; aggressive staggering (7/slot with afternoon overflow) "
            "shifts crowd from morning to 2-4pm window."
        ),
    ),
    GoalParameterMapping(
        goal_description="Reduce crowding (number of patients waiting simultaneously) during clinic peak hours",
        goal_category=GoalCategory.WAITING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="inter_arrival_time",
                direction=ChangeDirection.REDISTRIBUTE,
                rationale=(
                    "Block/bulk arrivals caused peaks of up to 152 patients per "
                    "30 min at shared registration counters; evenly distributing "
                    "arrivals across the clinic day caps the instantaneous queue "
                    "length and prevents the waiting area from exceeding "
                    "capacity during the 9-12 window."
                ),
                paper_ids=[18],
                quantitative_evidence="10-21% reduction in average number of patients waiting per hour during peak hours (9am-1pm)",
            ),
        ],
        domain="healthcare",
    ),

    # ----- Ivan et al. (2021) — paper 19 — campus dining scheduling -----
    GoalParameterMapping(
        goal_description="Reduce customer waiting time in a capacity-constrained service system with clustered arrivals",
        goal_category=GoalCategory.WAITING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_count",
                direction=ChangeDirection.INCREASE,
                rationale=(
                    "Checkout was modelled as a single server with capacity 1 "
                    "while two staff were physically present but only one worked "
                    "the register at a time; making the second cashier a "
                    "full-time checkout resource removes the serial bottleneck "
                    "and disproportionately improves the system because balking "
                    "decisions are driven by visible queue length at the register."
                ),
                paper_ids=[19],
                quantitative_evidence="Service time reduced by 2.68 minutes on average; maximum balking dropped from 38 to 20 students across scenarios",
            ),
            ParameterChange(
                parameter_name="inter_arrival_time",
                direction=ChangeDirection.REDISTRIBUTE,
                rationale=(
                    "Classes releasing simultaneously produce 'waves' of arrivals "
                    "that overwhelm a fixed-capacity service point; spreading "
                    "class end-times across the lunch window flattens the arrival "
                    "curve and keeps queue lengths below the balking threshold "
                    "(25 people)."
                ),
                paper_ids=[19],
                quantitative_evidence="Best scheduling scenario evenly distributed 57/56/53/30 students across four hourly slots; worst concentrated 64/58 in the 1-3pm window, producing materially worse satisfaction",
            ),
        ],
        domain="service_operations",
        notes=(
            "Model uses a pager-style service decoupling waiting-for-food from "
            "checkout, so improvements specifically target the FIFO checkout "
            "queue, not food preparation. Findings relevant to reduced-capacity "
            "settings (e.g., COVID-19 restrictions)."
        ),
    ),

    # ----- Duguay & Chetouane (2007) — paper 20 — ED DES in Arena -----
    GoalParameterMapping(
        goal_description="Reduce patient waiting time in an emergency department with peak-hour congestion",
        goal_category=GoalCategory.WAITING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_count",
                direction=ChangeDirection.INCREASE,
                rationale=(
                    "Waiting time from registration to exam-room availability "
                    "dominated total stay and was directly proportional to "
                    "physician/nurse availability; adding one doctor+nurse pair "
                    "cleared the backlog of code-3/4/5 patients that was "
                    "building during the 8am-4pm peak. Crucially, the paper "
                    "proved that adding rooms alone did nothing — staff is the "
                    "binding constraint."
                ),
                paper_ids=[20],
                quantitative_evidence="Alternative with +1 physician +1 nurse (8am-4pm) reduced waiting time T3 by up to 2 hours at week-start and increased throughput by 16 patients/day",
            ),
            ParameterChange(
                parameter_name="resource_calendar",
                direction=ChangeDirection.INCREASE,
                rationale=(
                    "The shift timing was chosen to overlap the observed "
                    "peak-arrival window (8am-8pm, with Mondays and Fridays "
                    "heaviest); placing additional staff in this window rather "
                    "than uniformly extending coverage delivered the best "
                    "waiting-time reduction per added resource."
                ),
                paper_ids=[20],
                quantitative_evidence="8am-4pm additional shift outperformed alternative 3 (10am-5pm) and alternative 5 (split shifts) on both cost and waiting-time criteria",
            ),
        ],
        domain="healthcare",
        notes=(
            "Strong negative finding: alternatives adding examination rooms "
            "without staff showed no benefit — a caution against assuming "
            "physical capacity is always the bottleneck. Only 5 of 8 available "
            "rooms were used even before the intervention."
        ),
    ),
    GoalParameterMapping(
        goal_description="Increase throughput in an emergency department without unbounded cost",
        goal_category=GoalCategory.THROUGHPUT,
        parameter_changes=[
            ParameterChange(
                parameter_name="resource_count",
                direction=ChangeDirection.INCREASE,
                rationale=(
                    "Shorter waiting times translated directly into more patients "
                    "cleared per day because service times themselves were "
                    "unchanged; the constraint on throughput was queue build-up, "
                    "not per-case processing."
                ),
                paper_ids=[20],
                quantitative_evidence="16 additional patients treated per day between 8am and 8pm after adding one physician/nurse shift",
            ),
        ],
        domain="healthcare",
    ),

    # ----- Pihir et al. (2010) — paper 21 — loan application process -----
    GoalParameterMapping(
        goal_description="Increase throughput (number of approved cases) in a loan-application process without adding resources",
        goal_category=GoalCategory.THROUGHPUT,
        parameter_changes=[
            ParameterChange(
                parameter_name="gateway_probabilities",
                direction=ChangeDirection.REDISTRIBUTE,
                rationale=(
                    "In the AS-IS model the approve/reject gateway split 50/50, "
                    "with rejected applications terminating. The TO-BE model "
                    "re-routes rejected applications through a new decision "
                    "gateway offering conditional approval with additional "
                    "insurance (guarantor), which 70% of applicants accept. The "
                    "gateway-probability change — not a speedup — is what drives "
                    "the throughput gain because it adds a previously-absent "
                    "success path."
                ),
                paper_ids=[21],
                quantitative_evidence="Approval rate increased from 50% to 85%; profit increased by 504.77 kn per application on average",
            ),
        ],
        domain="finance",
        notes=(
            "Trade-off: 4% longer cycle time and 4% higher per-case cost, but "
            "revenue scales with approvals so net profit rises substantially. "
            "Example numbers are illustrative, not real bank data."
        ),
    ),
    GoalParameterMapping(
        goal_description="Increase business revenue per case in a customer-facing approval workflow",
        goal_category=GoalCategory.THROUGHPUT,
        parameter_changes=[
            ParameterChange(
                parameter_name="gateway_probabilities",
                direction=ChangeDirection.ADD,
                rationale=(
                    "Introducing a conditional-approval path (rather than "
                    "hard-reject) captures revenue from marginal applicants who "
                    "can provide additional insurance; the simulation quantifies "
                    "the expected business-case impact before implementation and "
                    "thus de-risks the process change."
                ),
                paper_ids=[21],
                quantitative_evidence="Revenue per approved application of 1460 kn (2% fee on ~73,000 kn loan); aggregate simulated annual impact across 100 branches estimated at +79 million kn",
            ),
        ],
        domain="finance",
    ),

    # ----- Su et al. (2010) — paper 22 — hospital registration reengineering -----
    GoalParameterMapping(
        goal_description="Reduce patient waiting time at a hospital registration desk with extreme queue buildup",
        goal_category=GoalCategory.WAITING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="events_configuration",
                direction=ChangeDirection.REDISTRIBUTE,
                rationale=(
                    "Switching from three independent queues to one single FCFS "
                    "queue feeding three servers eliminates the load imbalance "
                    "where one queue blocks while others are idle; the "
                    "prepare-queue of 16 near the counters additionally removes "
                    "the walking-time penalty each time a server becomes free, "
                    "since the next patient is already positioned to step up."
                ),
                paper_ids=[22],
                quantitative_evidence="Without prepare queue (length 0): 39.08 min average; with optimal length 16: 3.15 min average (a ~92% reduction)",
            ),
            ParameterChange(
                parameter_name="activity_duration",
                direction=ChangeDirection.DECREASE,
                rationale=(
                    "The field study found walking time between seats and "
                    "registration counters was a dominant non-value-adding "
                    "component; the prepare-queue converts that walking time "
                    "into an effective zero because a patient is always standing "
                    "next in line when a server becomes available."
                ),
                paper_ids=[22],
                quantitative_evidence="Walking time and waiting time together accounted for 97% of total registration time; pre-check and registration activities themselves only 3%",
            ),
        ],
        domain="healthcare",
        notes=(
            "The optimal prepare-queue size (16) was found by fine-grained "
            "simulation sweep; values ≥20 gave no further total-time improvement "
            "but reduced time-on-seat (comfort). Also identified pre-check "
            "compliance as a secondary lever: 60% of patients skipped it, and "
            "enforcing it alone gave 8.5%/25.7% improvements in average/max "
            "waiting time."
        ),
    ),
    GoalParameterMapping(
        goal_description="Eliminate non-value-adding queue-strategy bottlenecks in a high-volume service system",
        goal_category=GoalCategory.WAITING_TIME,
        parameter_changes=[
            ParameterChange(
                parameter_name="events_configuration",
                direction=ChangeDirection.REASSIGN,
                rationale=(
                    "Multi-queue/multi-server systems suffer from random "
                    "queue-length imbalance — customers in one line can wait "
                    "while another line clears. Consolidating into "
                    "single-queue/multi-server is a proven queuing-theory "
                    "improvement that equalises wait times across customers with "
                    "no additional resources."
                ),
                paper_ids=[22],
                quantitative_evidence="Intervention cost nothing beyond layout redesign yet cut maximum wait from >50 min to 8 min",
            ),
        ],
        domain="healthcare",
    ),
]


# ===================================================================
# 4. CONTEXT-AWARE DIFFERENTIATION RULES
#    These extend the baseline by enabling context-sensitive parameter
#    generation — the core thesis contribution.
# ===================================================================

CONTEXT_RULES: list[ContextAwareRule] = [
    ContextAwareRule(
        rule_id="ctx_case_resource_pool",
        description="Differentiate resource pools by case-level context factors",
        trigger_factor_scope=ContextFactorScope.CASE_LEVEL,
        trigger_factor_examples=[
            "customer_tier", "priority", "claim_type", "loan_amount",
            "product_category", "severity",
        ],
        affected_parameters=["resource_count", "resource_activity_assignment"],
        differentiation_strategy=(
            "Create separate resource pools or priority-based assignment rules "
            "per context segment. For example, assign senior staff to premium "
            "customers or high-severity cases, based on the statistically "
            "significant performance difference observed in the log."
        ),
        rationale=(
            "When a case-level factor significantly affects cycle time or "
            "waiting time, uniform resource allocation under-serves some "
            "segments. Differentiated pools align capacity with observed demand "
            "patterns (Lopez-Pintado et al., 2024)."
        ),
    ),
    ContextAwareRule(
        rule_id="ctx_case_routing",
        description="Differentiate gateway routing by case-level context factors",
        trigger_factor_scope=ContextFactorScope.CASE_LEVEL,
        trigger_factor_examples=[
            "claim_type", "loan_amount", "risk_level", "order_value",
        ],
        affected_parameters=["gateway_probabilities"],
        differentiation_strategy=(
            "Set segment-specific branching probabilities at decision gateways. "
            "For example, if complex claims route to fraud review 40% of the "
            "time while standard claims route only 5%, encode these as "
            "conditional probabilities rather than using the pooled average."
        ),
        rationale=(
            "Pooled gateway probabilities hide important variation across "
            "case types. Context-differentiated routing produces more realistic "
            "simulation behaviour for each segment."
        ),
    ),
    ContextAwareRule(
        rule_id="ctx_temporal_calendar",
        description="Adjust resource calendars based on temporal context factors",
        trigger_factor_scope=ContextFactorScope.TEMPORAL,
        trigger_factor_examples=[
            "day_of_week", "hour_of_day", "month", "quarter",
            "is_weekend", "is_holiday",
        ],
        affected_parameters=["resource_calendar", "resource_count"],
        differentiation_strategy=(
            "Adjust resource calendars and staffing levels based on temporal "
            "demand patterns. For example, if Monday has significantly higher "
            "arrival rates, add extra staffing on Mondays or extend hours."
        ),
        rationale=(
            "Temporal factors create predictable demand variation. Aligning "
            "resource availability with temporal patterns reduces peak-period "
            "waiting times without over-provisioning during off-peak periods."
        ),
    ),
    ContextAwareRule(
        rule_id="ctx_case_duration",
        description="Differentiate activity durations by case-level context factors",
        trigger_factor_scope=ContextFactorScope.CASE_LEVEL,
        trigger_factor_examples=[
            "complexity", "product_type", "claim_type", "loan_amount",
        ],
        affected_parameters=["activity_duration"],
        differentiation_strategy=(
            "Use segment-specific duration distributions instead of pooled "
            "distributions. For example, if high-value loans take significantly "
            "longer for credit checks (observed median 3.2h vs 1.8h for "
            "standard), model these as separate distributions."
        ),
        rationale=(
            "A single pooled duration distribution masks the bimodal or "
            "multi-modal behaviour caused by different case types. "
            "Segment-specific distributions improve simulation fidelity."
        ),
    ),
    ContextAwareRule(
        rule_id="ctx_event_resource",
        description="Differentiate resource assignment based on event-level attributes",
        trigger_factor_scope=ContextFactorScope.EVENT_LEVEL,
        trigger_factor_examples=[
            "channel", "support_channel", "submission_method",
            "department", "location",
        ],
        affected_parameters=["resource_activity_assignment", "resource_count"],
        differentiation_strategy=(
            "Route cases to different resource pools based on event-level "
            "attributes like the channel through which they entered. "
            "For example, phone claims may need a different agent pool than "
            "online claims."
        ),
        rationale=(
            "Event-level attributes can indicate different processing "
            "requirements. Matching resources to the specific characteristics "
            "of each event improves both utilisation and service quality."
        ),
    ),
]


# ===================================================================
# 5. ASSEMBLED KNOWLEDGE BASE
# ===================================================================

def build_knowledge_base() -> ParameterKnowledgeBase:
    """Construct the complete knowledge base from the data defined above."""
    return ParameterKnowledgeBase(
        literature=LITERATURE,
        parameters=PARAMETERS,
        goal_mappings=GOAL_MAPPINGS,
        context_rules=CONTEXT_RULES,
    )
