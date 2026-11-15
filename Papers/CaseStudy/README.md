# Case-study literature

The knowledge base of Stage 2 (`goal_to_parameters/knowledge/kb_data.py`) draws its literature evidence from the
simulation case studies listed below. The structured entries (authors, year, domain, parameters tested,
quantitative result and the location of that result in the paper) are part of `kb_data.py`; the PDFs themselves
are third-party publications and are not distributed with this code. Obtain them from their publishers.

## References

`ID` is the `paper_id` used in `kb_data.py` and in the `literature_support` fields of the prompts. For IDs 1 and
3-7 the title column holds the knowledge base's descriptive label; the PDF file name carries the publication's
own title.

| ID | Authors | Year | Title | PDF file name expected by the indexer |
|---|---|---|---|---|
| 1 | Braaksma et al. | 2017 | Reusable simulation model for evaluating walk-in and appointment systems for CT-scan facilities | A_Reusable_simulation_model_to_evaluate_the_effects_of_walkin_for_diagnostic_examination.pdf |
| 2 | Al-Hawari, T.; Khanfar, A.; Mumani, A.; Bataineh, O. | 2022 | A Simulation-Based Framework for Evaluation of Healthcare Systems with Interacting Factors and Correlated Performance Measures | A Simulation‑Based Framework for Evaluation of Healthcare Systems.pdf |
| 3 | Pereira et al. | 2021 | Case study on IT incident management process improvement through simulation | Business process management heuristics in IT service management a case study for incident management.pdf |
| 4 | Czvetko et al. | 2021 | Data-driven BPM methodology for CNC production line redesign | Data-driven business process management-based development of.pdf |
| 5 | Zeinali et al. | 2015 | Simulation-based metamodeling approach for resource planning in emergency departments | Resource planning in the emergency departments.pdf |
| 6 | Rezeq et al. | 2024 | Hybrid simulation-optimization for relief-aid distribution at security checkpoints | Hybrid simulation-optimization.pdf |
| 7 | Maass et al. | 2021 | Discrete event simulation for Mayo Clinic CT department | Evaluation Clinical Practice - 2021 - Maass - A discrete event simulation to evaluate impact of radiology process changes.pdf |
| 8 | Lee, L., Ou, Y., Cheng, Y., Sun, Y., Wu, H., Guo, W. | 2019 | Using a Hybrid Simulation Model to Maximize Patient Throughput of Magnetic Resonance Imaging in a Medical Center | Using a Hybrid Simulation Model to Maximize Patient Throughput.pdf |
| 9 | Kristiana, S.P.D.; Triyanti, V.; Budiyanta, N.E.; Silitonga, R.M. | 2026 | Production System Analysis and Scenario Development Using FlexSim: A Case-Based Study | Production System Analysis and Scenario Development Using.pdf |
| 10 | Aeenparast, A.; Tabibi, S.J.; Shahanaghi, K.; Aryanejhad, M.B. | 2013 | Reducing Outpatient Waiting Time: A Simulation Modeling Approach | Reducing Outpatient Waiting Time A Simulation Modeling Approach.pdf |
| 11 | Rashed, C.A.A.; Nahar, S.K.; Pritom, A.P. | 2023 | Service Time Reduction Through the Development of a Simulation Model in a Selected Bank | Service Time Reduction Through the Development of a Simulation Model in a Selected Bank.pdf |
| 12 | Shim, S.J.; Kumar, A. | 2010 | Simulation for emergency care process reengineering in hospitals | Simulation for emergency care process reengineering in hospitals.pdf |
| 13 | Srinivas, S.; Nazareth, R.P.; Ullah, M.S. | 2021 | Modeling and analysis of business process reengineering strategies for improving emergency department efficiency | Modeling and analysis of business process reengineering strategies for improving emergency department efficiency.pdf |
| 14 | Marchesi, J.F.; Hamacher, S.; Peres, I.T. | 2025 | Stochastic model for physician staffing and scheduling in emergency departments with multiple treatment stages | Stochastic model for physician staffing and scheduling in emergency.pdf |
| 15 | Gharahighehi, A., Kheirkhah, A.S., Bagheri, A., & Rashidi, E. | 2016 | Improving performances of the emergency department using discrete event simulation, DEA and the MADM methods | Improving performances of the emergency.pdf |
| 16 | Renna, P.; Colonnese, C. | 2025 | A Simulation-Driven Business Process Reengineering Framework for Teaching Assignment Optimization in Higher Education—A Case Study of the University of Basilicata | A Simulation-Driven Business Process Reengineering.pdf; duplicate: AsimulationDriverProcessReeingeringFramework.pdf |
| 17 | Madadi, N., Roudsari, A.H., Wong, K.Y., & Galankashi, M.R. | 2013 | Modeling and Simulation of a Bank Queuing System | Modeling and Simulation of a Bank Queuing.pdf |
| 18 | Fun, W.H.; Tan, E.H.; Khalid, R.; Sararaks, S.; Tang, K.F.; Ab Rahim, I.; Md. Sharif, S.; Jawahir, S.; Sibert, R.M.Y.; Nawawi, M.K.M. | 2022 | Applying Discrete Event Simulation to Reduce Patient Wait Times and Crowding: The Case of a Specialist Outpatient Clinic with Dual Practice System | Applying Discrete Event Simulation to Reduce Patient Wait Times and Crowding The Case of a Specialist Outpatient Clinic with Dual Practice System.pdf |
| 19 | Ivan, J.; Rooney, S.; Carlson, H.; Bentley, S.; Fisher, D.; Angelopoulou, A. | 2021 | The Impact of the Constraints of Class Scheduling on Campus Dining: A Simulation-based Case Study | The Impact of the Constraints of Class Scheduling on.pdf |
| 20 | Duguay, C.; Chetouane, F. | 2007 | Modeling and Improving Emergency Department Systems using Discrete Event Simulation | Modeling and Improving Emergency.pdf |
| 21 | Pihir, I.; Žajdela Hrustek, N.; Dušak, V. | 2010 | Survey of Simulation Capabilities of the IBM WebSphere Business Modeler Business Process Modeling Tool on the Example of Processing a Loan Application | Survey of Simulation Capabilities of the IBM WebSphere Business Modeler.pdf |
| 22 | Su, Q.; Yao, X.; Su, P.; Shi, J.; Zhu, Y.; Xue, L. | 2010 | Hospital Registration Process Reengineering Using Simulation Method | Hospital Registration Process Reengineering.pdf |

One further PDF was part of the original folder without a knowledge-base entry: *Improving the efficiency of
public administrations through business process reengineering and simulation*, Business Process Management
Journal (file name: `Improving the efficiency of public administrations through business process reengineering
and simulation.pdf`).

The knowledge base itself is optional and off by default (`use_knowledge_base` in `goal_to_parameters/config.yaml`;
see the main README, "Optional: knowledge-base retrieval").

## Optional full-text index

`goal_to_parameters/knowledge/pdf_indexer.py` can index the PDFs of this folder into paragraph-level chunks that
the hybrid retriever (`knowledge/hybrid_retrieval.py`) adds to the knowledge-base entries. The index needs
`pypdf` and the PDFs under the file names above. Without them `load_pdf_chunks` returns an empty list and
retrieval uses the structured knowledge base only.

**The reported experiments did not use the PDF index.** `pypdf` was not installed in the environment that ran
them, so `load_pdf_chunks` returned no chunks, and none of the 690 Stage 2 run records (LoanApp/P2P batches v1 to
v3.2 and the real-life ablation) contains a `source_excerpts` section in any prompt, while their prompts do contain
the structured knowledge-base literature. The retriever ran in its fallback configuration: lexical BM25 (GLASS's
own pure-Python implementation, `knowledge/bm25.py`), dense scoring with the built-in TF-IDF embedder
(`sentence-transformers` and an Ollama embedding server were not available) and no cross-encoder re-ranking
(`sentence-transformers` not installed). The PDF index is therefore optional and not needed to reproduce the
results.
