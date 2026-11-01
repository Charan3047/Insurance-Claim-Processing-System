# Insurance Claims Processor — Advanced Edition (v2.0)

An end-to-end, zero-dependency insurance claims adjudication workflow, 14-table relational model (with normalized N:M bridge tables), deterministic bottom-up synthetic data generator (`seed=20261022`), 114-check validation suite, and interactive browser workbench.

## Key Advancements over Baseline (v1.0 → v2.0)

1. **100% Bottom-Up Derived Financial Reconciliation (Zero Tolerance)**:
   - Every `ClaimItem` computes `claimed_amount = round(quantity * unit_cost, 2)` exactly (`100%` match vs. `60.7%` in v1.0).
   - Every `Claim` derives `claim_amount = round(sum(claim_items.claimed_amount), 2)` from its child line items (`100%` match vs. `0%` in v1.0).
   - Every approved `Assessment` derives `assessed_amount = round(sum(claim_items.approved_amount), 2)` (`100%` match vs. `0%` in v1.0) and sets `eligible_amount = 0.00` whenever a claim is denied (`100%` match vs. `1.3%` in v1.0).
2. **Normalized Many-to-Many (N:M) Bridge Tables**:
   - Adds `claim_adjuster_assignments` (`CAA-<claim_id>-NNN`) modeling multi-role adjuster assignments (`PRIMARY_ADJUSTER`, `SIU_INVESTIGATOR`, `SUPERVISOR_REVIEWER`, `CO_ADJUSTER`).
   - Adds `claim_coverage_allocations` (`CCA-<claim_id>-NNN`) modeling multi-coverage financial splits whose `allocated_claimed_amount` sums exactly to `claims.claim_amount`.
3. **Clean Baseline + Bi-Directionally Verified Edge Cases**:
   - Generates coherent baseline claims first, then injects all **6 required negative edge cases** (`CLAIM_DATE_BEFORE_POLICY_START`, `POLICY_CANCELLED_BEFORE_INCIDENT`, `DUPLICATE_INCIDENT`, `SETTLEMENT_EXCEEDS_LIMIT`, `MISSING_MANDATORY_DOCUMENT`, `CLAIM_PAID_TWICE`) plus **4 Step-4 portfolio exception scenarios** (`POLICY_LAPSED_BEFORE_INCIDENT`, `INCIDENT_EXCLUDED_BY_COVERAGE`, `CONFIRMED_FRAUD_INDICATOR_SIU` / `SIU_INVESTIGATION`, `CLAIM_AMOUNT_OVER_LIMIT`).
   - Eliminates the 283 unlabelled accidental date/cancellation anomalies and 99 approved-with-rejected-documents anomalies present in v1.0.
4. **Strict Zero-Leakage Feature Separation & Full Portfolio Decision Labels**:
   - Removes `fraud_indicator`, `assigned_adjuster_id`, and `updated_at` from `output/features/claims_input.csv` so no ground-truth label or post-intake outcome leaks into model features.
   - Publishes `output/ground_truth_decisions.csv` covering all 1,000 claims alongside `output/ground_truth.csv` and `output/ground_truth.json`.
5. **Unified 14-Table SQLite Live Workbench (`workflow.py` & `app.py`)**:
   - Seeds all 14 CSV tables into `workflow.sqlite` on startup so live metrics immediately reflect all 1,000 claims, 564 settlements, 1,140 N:M adjuster links, 1,237 N:M coverage links, and 3,555 audit events.
   - Adds queue filtering by status and product type, a Claim 360° inspector with line-item waterfall, and an `/api/claims/{id}/siu` fraud escalation route.

## Quick Start

Requires Python 3.10+ (standard library only, zero external dependencies):

```bash
# 1. Generate the default full dataset (2,000 policies / 1,000 claims, seed 20261022)
python3 generate.py

# 2. Or generate the starter dataset (200 policies / 100 claims)
python3 generate.py --scale starter

# 3. Verify two-run deterministic SHA-256 reproducibility
python3 generate.py --verify

# 4. Run the 12-section (114-check) validation suite
python3 validate.py --json-report output/validation_report.json

# 5. Compute portfolio, reconciliation, and cycle-time metrics
python3 metrics.py --data-dir output --output output/metrics.json

# 6. Run the automated test suite (13 tests)
python3 -m unittest discover -v

# 7. Launch the interactive browser workbench at http://127.0.0.1:8000
python3 app.py
```

## Repository Structure

| Path | Description |
|---|---|
| `generate.py` | Deterministic 14-table synthetic data generator (`seed=20261022`) |
| `validate.py` | 12-section, 114-rule data & business-logic validator |
| `workflow.py` | Unified 14-table SQLite workflow service and role-checked state machine |
| `app.py` | Interactive browser workbench and JSON REST API |
| `metrics.py` | Portfolio, financial reconciliation, and ground-truth evaluation metrics |
| `data_dictionary.md`, `schema/` | Complete 14-table data dictionary (Markdown + JSON) and SQLite DDL (`schema.sql`) |
| `COMPARISON_REPORT.md` | Side-by-side audit comparing Baseline v1.0 vs. Advanced v2.0 |
| `VALIDATION_SUMMARY.md` | Summary of all 114 validation checks and reproducibility hashes |
| `output/` | Generated CSV, JSON, SQL seed files, `features/claims_input.csv`, `ground_truth.*`, `manifest.json` |
| `tests/` | Automated unit & integration tests (`test_app.py`, `test_pipeline.py`, `test_workflow.py`) |

## License & Provenance

Released under the MIT License (`LICENSE`). All records are 100% synthetic and deterministically generated; no external personal or proprietary insurer data is used or redistributed.
