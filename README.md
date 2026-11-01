# Insurance Claims Processing System

An end-to-end insurance claims adjudication workflow and deterministic synthetic-data pipeline built with the Python standard library. The system handles claim intake, policy and coverage validation, mandatory document tracking, specialist adjuster assignment, eligibility assessment, financial reserve management, settlement routing, and append-only audit logging.

> **Scope:** This repository is a challenge prototype powered by 100% synthetic data (`seed=20261022`). It is not a production adjudication platform, and its metrics do not represent real insurer operations.

## Key Capabilities

- **Role-Based Claims Workbench & REST API:** Local browser interface (`app.py`) and JSON API (`workflow.py`) supporting five operational roles (`requester`, `operations_analyst`, `approver`, `process_owner`, `system_administrator`).
- **14-Table Relational Model:** Covers 11 core entities (`Customer`, `Adjuster`, `Policy`, `Coverage`, `Incident`, `Claim`, `ClaimItem`, `Document`, `Assessment`, `Reserve`, `Settlement`), 2 normalized many-to-many (`N:M`) bridge tables (`ClaimAdjusterAssignment`, `ClaimCoverageAllocation`), and an immutable event log (`ClaimStateHistory`).
- **Deterministic Synthetic Data (`seed=20261022`):** Generates 2,000 policies and 1,000 claims across five product lines (`AUTO`, `HOME`, `HEALTH`, `LIFE`, `TRAVEL`) with 100% bottom-up financial reconciliation (`unit_cost * quantity == claimed_amount` and `SUM(claim_items) == claim_amount`).
- **Controlled Edge Cases & Zero-Leakage Features:** Injects and labels six required anomaly scenarios in `output/ground_truth.csv` and exports a leakage-free feature projection in `output/features/claims_input.csv`.
- **Automated Quality Gate:** Includes a 114-check data validator (`validate.py`), operational metrics engine (`metrics.py`), 13 automated unit/workflow tests (`tests/`), and GitHub Actions CI.

## Quick Start

Requires **Python 3.10+** with no external dependencies.

```bash
# 1. Start the interactive claims workbench at http://127.0.0.1:8000
python app.py

# 2. Generate the full synthetic dataset (2,000 policies / 1,000 claims)
python generate.py

# 3. Generate the smaller starter dataset (200 policies / 100 claims)
python generate.py --scale starter

# 4. Verify two-run deterministic SHA-256 reproducibility
python generate.py --verify

# 5. Run the 114-check validation suite
python validate.py --data-dir output --json-report output/validation_report.json

# 6. Compute portfolio and reconciliation metrics
python metrics.py --data-dir output --output output/metrics.json

# 7. Run automated unit and workflow tests
python -m unittest discover -v
```

## Workflow & Decision Rules

| Actor Role | Permitted Workflow Actions |
|---|---|
| `requester` | Submit claims, track claim status, upload requested supporting documents |
| `operations_analyst` | Request missing documents, verify received evidence, assign specialist adjusters, escalate suspected fraud to SIU, assess eligibility |
| `approver` | Approve or reject assessed settlement recommendations, escalate high-risk claims |
| `process_owner` | Inspect portfolio metrics, cycle times, and claim state history |
| `system_administrator` | Disburse approved settlement payments and inspect rule configurations |

### Core Business Logic

- **Coverage Validation:** Claims are denied automatically at intake or review if the incident date falls outside policy/coverage effective dates, the policy was cancelled or lapsed prior to the incident, no active coverage exists, or the incident peril is excluded.
- **Document & Fraud Gating:** Eligibility assessment is blocked until all product-specific mandatory documents are received. Claims flagged for suspected fraud are routed to `SIU_INVESTIGATION` or denied rather than paid.
- **Eligibility Formula:** When coverage is verified, `eligible_amount = max(0.0, min(assessed_amount - deductible, coverage_limit))`; denied claims always record `eligible_amount = 0.00`.
- **Auditability:** Every workflow action appends an immutable event with actor role, actor ID, state transition, reason, and timestamp.

## API Endpoints

All mutating endpoints accept `X-Role` and `X-Actor` headers:

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/claims` | List claims queue (supports `?status=` and `?claim_type=` filters) |
| `GET` | `/api/claims/{id}` | Retrieve full Claim 360° record (items, documents, bridges, assessments, settlements, events) |
| `GET` | `/api/metrics` | Retrieve live portfolio and reconciliation metrics |
| `GET` | `/api/rules` | Retrieve required document rules and eligibility formulas |
| `GET` | `/api/reference` | List valid active policy and incident pairs for claim intake |
| `POST` | `/api/claims` | Submit a new claim |
| `POST` | `/api/claims/{id}/documents/request` | Record a missing-document request |
| `POST` | `/api/claims/{id}/documents/receive` | Mark pending mandatory documents as received |
| `POST` | `/api/claims/{id}/assign` | Assign an active specialist adjuster |
| `POST` | `/api/claims/{id}/siu` | Escalate a claim to `SIU_INVESTIGATION` |
| `POST` | `/api/claims/{id}/assess` | Assess eligibility and establish a financial reserve |
| `POST` | `/api/claims/{id}/approve` | Approve or reject the settlement recommendation |
| `POST` | `/api/claims/{id}/pay` | Record settlement disbursement and close the open reserve |

## Dataset & Repository Layout

| Path | Contents |
|---|---|
| `app.py`, `workflow.py` | Local web workbench, REST API handler, workflow engine, and SQLite persistence |
| `generate.py` | Deterministic 14-table synthetic data generator (`seed=20261022`) |
| `validate.py` | 12-section, 114-rule validation and reconciliation suite |
| `metrics.py` | Portfolio, financial reconciliation, and cycle-time metrics calculator |
| `data_dictionary.md`, `schema/` | Complete field-level data dictionary (Markdown + JSON) and SQLite DDL (`schema.sql`) |
| `output/` | Generated `csv/`, `json/`, `sql/`, `features/claims_input.csv`, `ground_truth.*`, `manifest.json`, and reports |
| `tests/` | Automated unit and integration test suite (`unittest`) |
| `DESIGN_NOTES.md`, `PRESENTATION_GUIDE.md`, `AI_USAGE.md` | Architecture notes, walkthrough guide, and AI-assistance disclosure |

## License & Data Provenance

Released under the MIT License (`LICENSE`). All records are 100% synthetic; no external, personal, or proprietary insurer data is used or redistributed.
