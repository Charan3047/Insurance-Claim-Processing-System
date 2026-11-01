# Design Notes & Architectural Trade-Offs (Advanced Edition v2.0)

## 1. Bottom-Up Financial Derivation vs. Independent Sampling
In the baseline v1.0 generator, `claims.claim_amount`, `claim_items.claimed_amount`, and `assessments.assessed_amount` were sampled independently using random multipliers (`random.uniform(0.5, 1.5)`). As a result, 1,000 out of 1,000 claims failed `claim_amount == SUM(claim_items.claimed_amount)` and 951 out of 951 assessments failed `assessed_amount == SUM(claim_items.approved_amount)`.

In **v2.0**, generation strictly follows Step 6 of the specification:
1. Line items (`claim_items`) are generated first with integer `quantity` and `unit_cost`, deriving `claimed_amount = round(quantity * unit_cost, 2)`.
2. Parent `claims.claim_amount` is derived as `round(sum(item.claimed_amount), 2)`.
3. Approved line items receive deterministic approval factors, and `assessments.assessed_amount` is derived as `round(sum(item.approved_amount), 2)`.
4. Denied claims always set `approved_amount = 0.00`, `assessed_amount = 0.00`, and `eligible_amount = 0.00`.

## 2. Normalized Many-to-Many (N:M) Bridge Tables
To satisfy the entity-relationship requirement (*"Use bridge tables for many-to-many relationships; retain event history for state changes; validate all joins"*), v2.0 adds two normalized bridge tables while keeping primary FKs on `claims` for backward compatibility:
- `claim_adjuster_assignments`: Links claims to multiple adjusters across distinct roles (`PRIMARY_ADJUSTER`, `SIU_INVESTIGATOR` for fraud-flagged claims, `SUPERVISOR_REVIEWER` for high-value claims $\ge \$25,000$).
- `claim_coverage_allocations`: Links claims to one or more policy coverages (`is_primary = True/False`) and reconciles `SUM(allocated_claimed_amount) == claims.claim_amount`.

## 3. Clean Separation Between Baseline Portfolio and Injected Anomalies
In v1.0, pairing random policies with random incidents caused 55.1% of all claims to be denied by accidental date mismatches, creating 231 unlabelled `CLAIM_DATE_BEFORE_POLICY_START` cases and 52 unlabelled `POLICY_CANCELLED_BEFORE_INCIDENT` cases outside `ground_truth.csv`.

In **v2.0**, baseline claims are generated strictly within active policy/coverage windows first. Then, `_inject_all_scenarios_before_downstream_derivation()` injects the exact target counts of the 6 required anomaly classes and 4 Step-4 portfolio exception classes (`POLICY_LAPSED_BEFORE_INCIDENT`, `INCIDENT_EXCLUDED_BY_COVERAGE`, `CONFIRMED_FRAUD_INDICATOR_SIU`, `CLAIM_AMOUNT_OVER_LIMIT`) before assessments, reserves, settlements, and state history are derived. This guarantees 0 unlabelled anomaly pollution and 0 validator warnings (`114 PASS, 0 FAIL, 0 WARN`).

## 4. Zero-Leakage Feature Engineering
`output/features/claims_input.csv` excludes all post-intake outcomes and ground-truth flags (`status`, `denial_reason`, `fraud_indicator`, `assigned_adjuster_id`, `updated_at`) and enriches each claim with pre-decision intake context (`policy_status_at_intake`, `policy_effective_date`, `policy_expiration_date`, `coverage_limit`, `deductible`, `incident_date`, `incident_severity`, `line_item_count`, `submitted_document_count`).
