# Insurance Claims Processing Demo

A small, runnable claims workflow and deterministic synthetic-data pipeline. The project demonstrates claim intake, coverage checks, evidence collection, eligibility assessment, adjuster assignment, approval, payment, and an auditable event history.

> **Scope:** This is a local challenge prototype using synthetic data. It is not a production insurance or adjudication system, and its generated metrics do not describe real insurer performance.

## At a glance

- Browser-based claims workflow with role-aware actions and a JSON API.
- Python standard library only; no third-party packages required.
- Reproducible full dataset: 2,000 policies and 1,000 claims across five product types.
- CSV, JSON, and SQL data exports, a separate feature projection, and separate ground-truth labels.
- Validation, metrics, automated tests, and GitHub Actions checks.
- AI assistance and design assumptions are disclosed in the project documentation.

## Run locally

Requires Python 3.10 or newer. From the project directory:

```bash
# Start the browser workflow, then open http://127.0.0.1:8000
python app.py
```

The first run creates `workflow.sqlite` for the demo's mutable workflow state and event history. The generated reference data is read from `output/`.

To run the data and quality checks:

```bash
# Generate the default full dataset (2,000 policies / 1,000 claims)
python generate.py

# Or generate the smaller dataset, which still includes all six edge-case classes
python generate.py --scale starter

# Check the current dataset and write a machine-readable report
python validate.py --json-report output/validation_report.json

# Run tests
python -m unittest discover -v

# Recompute descriptive measures
python metrics.py --data-dir output --output output/metrics.json

# Generate twice in memory and compare deterministic checksums
python generate.py --verify
```

The default generator seed is `20261022`; pass `--seed <integer>` to use another seed. Generation uses the Python standard library and needs no dependency installation.

## Claims workflow

The browser interface demonstrates the following lifecycle:

1. A business requester submits and tracks a claim.
2. An operations analyst requests and receives required evidence, assigns an eligible adjuster, and assesses the claim.
3. An approver approves or rejects the recommendation.
4. An administrator records payment for an approved settlement.
5. A process owner reviews claim history and queue metrics.

| Role | Example actions |
|---|---|
| Business requester | Submit a claim, check its status, provide requested documents |
| Operations analyst | Request evidence, receive documents, assign an adjuster, assess eligibility |
| Approver | Approve or reject an assessed recommendation |
| Process owner | Review queue metrics and claim history |
| System administrator | Perform administrative actions and inspect rule configuration |

Actions are checked by the workflow service and recorded as timestamped events with the actor, reason, action payload, and before/after state. The browser uses the same JSON API described in [`workflow.py`](workflow.py).

### Decision rules

- Coverage is invalid when the incident is outside policy or coverage dates, the policy is cancelled or lapsed before the incident, no active coverage applies, or an exclusion applies.
- Required evidence depends on claim type. Assessment is blocked while requested evidence is outstanding.
- Assigned adjusters must be active and specialized for the claim type.
- The simplified recommendation is `max(0, min(claim_amount - deductible, coverage_limit))` when the claim is eligible; otherwise the eligible amount is zero.
- The demo workflow sets a preliminary reserve to the reported claim amount. Generated reserves vary from 80% to 120% of the claim amount for evaluation.
- Approval or rejection is allowed after assessment. Payment is recorded only for an approved settlement and is capped by the assessment and applicable coverage limit.
- Decisions include a reason and an event-history entry.

These rules simplify real insurance processes. Aggregate remaining limits, multi-policy coordination, jurisdiction-specific rules, and production identity controls are outside the project scope.

### JSON API

Workflow actions use `X-Role` and `X-Actor` headers. Main routes include:

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/claims` | List the claims queue |
| `GET` | `/api/claims/{claim_id}` | Read claim, evidence, assessment, settlement, and event history |
| `GET` | `/api/metrics` | Read live operational measures |
| `GET` | `/api/rules` | Read decision rules and required evidence |
| `GET` | `/api/reference` | Read policy and incident choices for intake |
| `POST` | `/api/claims` | Submit a claim |
| `POST` | `/api/claims/{id}/documents/request` | Record an evidence request |
| `POST` | `/api/claims/{id}/documents/receive` | Record received evidence |
| `POST` | `/api/claims/{id}/assign` | Assign an active specialist adjuster |
| `POST` | `/api/claims/{id}/assess` | Assess eligibility and create an assessment/reserve |
| `POST` | `/api/claims/{id}/approve` | Approve or reject the recommendation |
| `POST` | `/api/claims/{id}/pay` | Record payment after approval |

## Data model and generated data

The relational model includes the 11 challenge entities—Customer, Policy, Coverage, Claim, ClaimItem, Document, Incident, Adjuster, Assessment, Reserve, and Settlement—plus claim-state history for traceability. Keys, relationships, cardinalities, optional references, field definitions, ID patterns, effective dates, and lifecycle states are documented in [`data_dictionary.md`](data_dictionary.md) and [`schema/data_dictionary.json`](schema/data_dictionary.json). SQLite DDL is in [`schema/schema.sql`](schema/schema.sql).

The default full dataset contains 2,000 policies and 1,000 claims across five types:

| Claim type | Claims |
|---|---:|
| AUTO | 300 |
| HEALTH | 250 |
| HOME | 200 |
| LIFE | 100 |
| TRAVEL | 150 |

The generator creates parent/master records before dependent records and links them using generated IDs. It starts with baseline records, then injects and labels these six required edge-case classes:

1. Incident before policy start
2. Policy cancelled before incident
3. Duplicate incident
4. Settlement exceeds coverage limit
5. Missing mandatory document
6. Claim paid twice

At full scale the cases have 10, 10, 5, 5, 10, and 5 examples respectively. Starter scale contains at least one example of each class. Intentional anomalies are identified in ground truth so they remain distinguishable from accidental integrity errors.

## Outputs and evaluation

`output/` includes:

- `csv/`, `json/`, and `sql/`: exports for the generated entities and history.
- `features/claims_input.csv`: an input-feature projection that omits post-decision outcome fields.
- `ground_truth.csv` and `ground_truth.json`: known edge-case labels, kept separate from model-style input features.
- `manifest.json`: seed, scale, row counts, and SHA-256 hashes for generated files.
- `validation_report.json` and `metrics.json`: validation results and descriptive measures for the included dataset snapshot.

`metrics.py` reports claim-type and outcome distributions, approval and coverage-failure rates, required-document completeness, reserve-range compliance, settlement-limit reconciliation, submission-to-payment time, and edge-case counts. These are descriptive measures of synthetic records, not estimates of real business performance.

## Validation and tests

The validator checks primary-key uniqueness, 19 foreign-key relationships, cross-table joins, allowed values, date ordering, financial reconciliation, lifecycle transitions, duplicate cases, ground-truth semantics, required documents, feature/label separation, and dataset volume/type coverage.

The test suite covers generator behavior and workflow actions such as intake, role checks, missing-document gating, deductible/limit calculations, approval-before-payment, duplicate intake handling, event history, and metrics. The repository also includes [GitHub Actions CI](.github/workflows/ci.yml) to run tests, validate the packaged dataset, and generate metrics on pushes and pull requests.

The included full-data validation snapshot reports **89 passes, 0 failures, and 2 warnings**. The warnings correspond to intentionally injected duplicate incidents and duplicate payments; those cases are represented in separate ground-truth labels.

## Reproducibility

The generator defaults to seed `20261022`. `python generate.py --verify` regenerates the complete dataset twice in memory and compares checksums. The output manifest records SHA-256 values for generated CSV, JSON, SQL, and feature files. Results are deterministic for a given seed and Python runtime.

## Repository guide

| Path | Contents |
|---|---|
| `app.py`, `workflow.py` | Local browser application, API, workflow rules, and SQLite persistence |
| `generate.py` | Synthetic-data generator and reproducibility check |
| `validate.py` | Dataset and business-rule validation |
| `metrics.py` | Synthetic-data evaluation measures |
| `schema/`, `data_dictionary.md` | Relational schema and field documentation |
| `output/` | Generated data, labels, manifest, report, and metrics |
| `tests/` | Standard-library automated test suite |
| `.github/workflows/ci.yml` | Automated checks for GitHub pushes and pull requests |

## Challenge and AI-use documentation

- [`AI_USAGE.md`](AI_USAGE.md) discloses substantial AI assistance and summarizes the prompt history. It does not claim independent authorship or review that did not occur.
- [`DESIGN_NOTES.md`](DESIGN_NOTES.md) documents assumptions, choices, trade-offs, validation, and limitations.
- [`PRESENTATION_GUIDE.md`](PRESENTATION_GUIDE.md) provides a short walkthrough and questions to prepare for.

Please describe your own contribution and AI assistance accurately when presenting or submitting this project.

## Limitations

This project is a local demonstration. It does not include production authentication, deployment hardening, external document storage, insurer integrations, concurrency/load testing, or jurisdiction-specific claim rules. Synthetic data and metrics are not representative of real customers, policies, or insurer performance. See [`DESIGN_NOTES.md`](DESIGN_NOTES.md) for assumptions and possible next steps.

## License and data provenance

The code is released under the MIT License; see [`LICENSE`](LICENSE). All generated records are synthetic. No external dataset is redistributed. The schema is informed by common insurance concepts, and generated personal-looking fields must not be treated as real customer or policy information.
