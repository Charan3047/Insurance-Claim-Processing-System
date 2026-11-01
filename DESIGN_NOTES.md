# Design notes

## Problem being demonstrated

The project demonstrates a small insurance-claims operation: take in a claim,
check the policy and coverage dates, track required evidence, assess an
eligible amount, assign an adjuster, route a recommendation for approval, and
record an approved payment. It also creates synthetic data for evaluating
these rules at a larger scale.

## Assumptions

- All people, policies, claims, and financial values are synthetic. The data
  is for a challenge demonstration, not a real insurer or actuarial model.
- A claim is associated with one policy and one incident. A policy can have
  multiple coverages, and a claim can have multiple items and documents.
- Coverage is considered only when the incident date is within policy and
  coverage effective dates, the policy is in a valid state, and no configured
  exclusion applies.
- The generated preliminary reserve is a synthetic estimate. In the demo
  workflow it is set to the reported claim amount; the generator varies
  reserves in a documented range to create evaluation cases.
- `eligible_amount = max(0, min(claim_amount - deductible, coverage_limit))`
  is a simplified single-claim calculation. It does not model aggregate
  remaining limits, coordination among coverages, taxes, depreciation, or
  jurisdiction-specific rules.
- The web demo uses a role selector to demonstrate action permissions. It does
  not provide production identity management or secure authentication.

## Key design choices and trade-offs

- **Standard library only:** Python's built-in HTTP server, SQLite, CSV, JSON,
  and `unittest` keep setup small and make the project easy to run in a clean
  Python environment. This favors portability over a production web framework.
- **Deterministic synthetic data:** a fixed seed and stable generation order
  make records reproducible and suitable for checking joins, rules, and
  metrics. Deterministic synthetic data is less varied than real portfolios
  and should not be treated as statistically representative.
- **Explicit edge cases:** six required negative scenarios are intentionally
  labeled in separate ground-truth files. This makes expected behavior
  testable and keeps anomalies distinguishable from accidental data errors.
- **Separate features and labels:** `output/features/claims_input.csv` is the
  model-style input projection; outcome fields and labels are kept out of that
  projection to reduce target leakage in a downstream evaluation exercise.
- **Append-only workflow events:** each action is recorded with actor, time,
  reason, and before/after states. This improves traceability, though the demo
  does not claim the operational or tamper-resistance guarantees of an
  enterprise audit platform.
- **CSV, JSON, and SQL exports:** these formats make the generated dataset
  inspectable and reusable without requiring the browser demo. SQLite is used
  separately for live workflow state.

## How correctness is checked

`tests/` exercises generator and workflow behavior. `validate.py` checks keys,
joins, enums, dates, arithmetic, lifecycle consistency, edge-case labels,
document rules, and feature/label separation. `generate.py --verify` compares
two in-memory generations using the same seed. `metrics.py` reports synthetic
portfolio and workflow measures. GitHub Actions repeats the tests, validation,
and metrics generation on pushes and pull requests.

The expected warnings for duplicate-incident and duplicate-payment examples
are intentional challenge cases; they should be reviewed with their separate
ground-truth labels. They are not a claim that duplicate payments are
acceptable in a live claims system.

## Limitations and next steps

This is a local challenge prototype, not a deployable claims adjudication
system. It has no external identity provider, real document storage, production
security controls, configurable jurisdiction rules, concurrency/load testing,
or integration with insurer systems. The generated data and evaluation
metrics do not establish predictive accuracy or real-world business value.
Any production use would require domain-expert review, security and privacy
engineering, stronger financial-limit accounting, and operational monitoring.
