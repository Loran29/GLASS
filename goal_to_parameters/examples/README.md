# Example Inputs

These files are small starter scenarios for quickly testing GLASS without having to invent process descriptions and goals yourself.

## Files

- `order_fulfillment.json`
- `loan_application.json`
- `hospital_discharge.json`
- `context_aware_insurance_claim.json`
- `refinement_feedback_example.md`

## How to use them

1. Open one of the `.json` files.
2. Copy the `process_description` value into the app's Process Description field.
3. Copy the `simulation_goal` value into the app's Simulation Goal field.
4. Generate KPIs.
5. If you want to test the refinement loop, use `refinement_feedback_example.md` as sample reviewer feedback after rejecting one or more KPIs.

## Context-Aware Demo

Use `context_aware_insurance_claim.json` together with `context_aware_insurance_claim.csv` if you want to test the context-aware pipeline on an example that is different from the context-aware few-shot prompt example.

This example includes contextual columns such as:

- `claim_type`
- `priority`
- `claim_amount`
- `channel`
- `customer_tier`
- `region`
- `fraud_risk`
- `workbasket`
- `end_time`

It also includes optional branches such as missing-document resubmission and fraud review, so the process variants are more realistic while still being structured enough for the app to discover statistically meaningful context-performance relationships and generate segmented KPI targets.
