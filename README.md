# Insurance Claims Processing System

A runnable claims desk and synthetic data pipeline for claim intake, policy and
coverage validation, document requests, eligibility assessment, adjuster work,
approver decisions, settlement payment, and an append-only audit trail.

## Quick start

Requires Python 3.10+ and no third-party packages.

```bash
# Run the browser workflow at http://127.0.0.1:8000
python app.py

# Regenerate the full dataset (2,000 policies / 1,000 claims)
python generate.py

# Generate the smaller starter dataset; all six anomaly classes remain present
python generate.py --scale starter

# Validate the generated CSVs, relationships, business rules, events, labels,
# and the feature/ground-truth separation
python validate.py

# Run the workflow and generator tests
python -m unittest discover -v

# Write business summary measures to output/metrics.json
python metrics.py --data-dir output --output output/metrics.json

# Repeat full generation in memory and compare full-data SHA-256 checksums
python generate.py --verify
```

The browser workflow creates `workflow.sqlite` for its persisted demo state and
event history. Remove that file to reset the application to the generated data.
Use `python app.py --db :memory:` for a temporary session.

## Working claims workflow

The local browser app supports the challenge roles through a role selector. The
role is sent with each action and checked by the workflow service.

| Actor | Demo actions |
|---|---|
| Business requester | Submit a claim, track status, provide requested documents |
| Operations analyst | Request evidence, receive documents, assess eligibility, assign a specialist adjuster |
| Approver | Approve or reject an assessed settlement |
| Process owner | Review queue metrics and claim history |
| System administrator | Perform administrative workflow actions and inspect rule configuration |

Every intake, document, assignment, assessment, approval, rejection, and payment
is persisted as a timestamped `claim_events` row with actor role, actor ID,
before/after state, reason, and action payload. The workflow creates records for
claims, documents, assessments, reserves, and settlements in SQLite while using
the generated policies, coverages, incidents, and adjusters as master data.

### Decision rules

- A claim is denied when its incident falls outside policy or coverage dates,
  the policy is cancelled or lapsed before the incident, no active coverage
  exists, or an exclusion applies.
- Intake requests the mandatory evidence defined for the claim type. Assessment
  is blocked while any requested evidence remains outstanding.
- An adjuster must be active and specialized for the claim type.
- `eligible_amount = max(0, min(claim_amount - deductible, coverage_limit))`.
- The operational workflow sets the preliminary reserve to 100% of the reported
  claim amount; generated reserves vary between 80% and 120%.
- The approver can approve or reject only after assessment. Payment can be
  recorded only for an approved settlement; the amount is capped by the
  assessment and applicable coverage limit.
- Each decision includes a human-readable reason and an event record.

The app is a local challenge demo. Its role selector demonstrates authorization
rules; production identity, authentication, and deployment controls are outside
this synthetic project.

### HTTP API

The browser uses the same JSON API. Send `X-Role` and `X-Actor` headers for
workflow actions.

| Method and path | Purpose |
|---|---|
| `GET /api/claims` | List the claim queue |
| `GET /api/claims/{claim_id}` | Claim, evidence, assessment, settlement, and audit history |
| `GET /api/metrics` | Live operational metrics |
| `GET /api/rules` | Required evidence and decision rules |
| `GET /api/reference` | Valid policy/incident pairs for intake |
| `POST /api/claims` | Intake with `policy_id`, `incident_id`, and `claim_amount` |
| `POST /api/claims/{id}/documents/request` | Record a missing-evidence request |
| `POST /api/claims/{id}/documents/receive` | Record requested evidence as received |
| `POST /api/claims/{id}/assign` | Assign an active specialist adjuster |
| `POST /api/claims/{id}/assess` | Evaluate eligibility and create assessment/reserve |
| `POST /api/claims/{id}/approve` | Approve or reject the recommended settlement |
| `POST /api/claims/{id}/pay` | Record an approved payment (administrator action) |

## Data model and generated volume

The project contains the 11 required entities plus the claim state event log.
Primary keys, foreign keys, relationship cardinalities, optional links, field
rules, lifecycle states, and ID patterns are in [`data_dictionary.md`](data_dictionary.md)
and [`schema/data_dictionary.json`](schema/data_dictionary.json). The
SQLite table definitions are in [`schema/schema.sql`](schema/schema.sql).

The default full generator creates 2,000 policies and 1,000 claims across five
types: AUTO 300, HEALTH 250, HOME 200, LIFE 100, and TRAVEL 150. It creates
dependent records from valid parent IDs and checks joins.

| Entity | Full generated rows |
|---|---:|
| Customer | 1,500 |
| Policy | 2,000 |
| Coverage | 6,041 |
| Incident | 1,175 |
| Claim | 1,000 |
| ClaimItem | 2,986 |
| Document | 3,990 |
| Adjuster | 50 |
| Assessment | 954 |
| Reserve | 426 |
| Settlement | 267 |
| ClaimStateHistory | 3,235 |

The generator creates valid baseline records first, then injects six labelled
edge-case classes. Full scale contains 10 pre-policy incidents, 10 cancelled
policies, 5 duplicate incidents, 5 over-limit settlements, 10 missing mandatory
documents, and 5 duplicate payments. Starter scale includes one example of
each class.

## Outputs, ground truth, and evaluation

`output/` contains CSV, JSON, and SQL seed files for the 12 generated tables,
`ground_truth.csv/json`, a manifest with row counts and per-file SHA-256 values,
and `features/claims_input.csv`. The feature projection excludes post-decision
`status` and `denial_reason`; anomaly labels remain in the separate ground-truth
files.

`python metrics.py` reports claim-type and outcome distributions, approval and
coverage-failure rates, mandatory-document completeness, reserve-range
compliance, settlement-limit reconciliation (excluding labelled intentional
over-limit cases), mean submission-to-payment time, and edge-case counts.
These values describe synthetic records and are not real insurer performance.
The live app also shows document completeness, paid settlements, limit
reconciliation, and audit-event coverage.

## Validation and tests

`validate.py` checks primary keys, 19 foreign-key relationships, cross-table joins, enums, dates,
arithmetic, reserve and settlement reconciliation, event transitions and final
state consistency, duplicate anomalies, all six ground-truth semantics, required
document presence, feature separation, and volume/type coverage.

The standard-library test suite exercises intake, role checks, missing-document
gating, deductible/limit calculations, approval-before-payment, duplicate
intake prevention, event history, and metrics.

## Reproducibility

The default seed is `20261022` and can be changed with `--seed`. `--verify`
generates the complete records twice and hashes every row in every table. The
manifest additionally hashes each emitted CSV, JSON, SQL, and feature file.
Generation order is deterministic for a given Python runtime and seed.

## Challenge submission materials

- [`AI_USAGE.md`](AI_USAGE.md) discloses the substantial AI assistance used and
  records the prompts at a summarized level. Read this before submitting and
  describe your own contribution accurately.
- [`DESIGN_NOTES.md`](DESIGN_NOTES.md) explains assumptions, decisions,
  trade-offs, validation approach, and limitations.
- [`PRESENTATION_GUIDE.md`](PRESENTATION_GUIDE.md) is a short walkthrough to
  help you learn and demonstrate the application.

The source is locally prepared for a GitHub repository, including a
`.github/workflows/ci.yml` workflow that runs tests, validation, and metrics on
pushes and pull requests. To publish, create a **public** empty GitHub
repository, then from this project folder run:

```bash
git init
# First meaningful commit: generated data, schema, validation, metrics, tests
git add generate.py validate.py metrics.py schema output data_dictionary.md tests/__init__.py tests/test_pipeline.py
git commit -m "Build reproducible claims data pipeline"
# Second meaningful commit: interactive claims workflow and its tests
git add app.py workflow.py tests/test_app.py tests/test_workflow.py
git commit -m "Add auditable claims workflow"
# Third meaningful commit: submission docs, license, and continuous checks
git add README.md AI_USAGE.md DESIGN_NOTES.md PRESENTATION_GUIDE.md LICENSE .gitignore .github
git commit -m "Document design and prepare repository checks"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git
git push -u origin main
```

Replace the remote URL with your own repository URL. GitHub publication,
public visibility, challenge-window timestamps, and your personal walkthrough
cannot be verified from this local folder; confirm these on GitHub and with the
recruiter before submission.

## ID conventions

| Entity | Pattern | Example |
|---|---|---|
| Customer | `CUST-NNNNNN` | `CUST-000042` |
| Policy | `POL-<PRODUCT>-2026-NNNNNN` | `POL-AUTO-2026-000001` |
| Coverage | `COV-<policy_id>-NNN` | `COV-POL-AUTO-2026-000001-001` |
| Claim | `CLM-2026-NNNNNN` | `CLM-2026-000100` |
| Incident | `INC-2026-NNNNNN` | `INC-2026-000050` |
| Document | `DOC-<claim_id>-NNN` | `DOC-CLM-2026-000100-001` |
| Adjuster | `ADJ-NNNNNN` | `ADJ-000010` |
| Assessment | `ASN-<claim_id>-NNN` | `ASN-CLM-2026-000100-001` |
| Reserve | `RSV-<claim_id>-NNN` | `RSV-CLM-2026-000100-001` |
| Settlement | `SET-<claim_id>-NNN` | `SET-CLM-2026-000100-001` |

## Provenance and repository use

All records are synthetic; no external dataset is used or redistributed. The
schema is inspired by common property-and-casualty insurance concepts. The
project uses the Python standard library only. See [`LICENSE`](LICENSE) for the
code license. Do not treat generated personal-looking fields as real people or
real policy data.
