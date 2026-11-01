# Short project walkthrough

Use this guide to learn the project before you present it. The code is a
synthetic challenge demo; it is not a real insurance company system.

## In one sentence

It is a small claims-processing demo plus a repeatable synthetic-data
generator: it checks a claim against a policy, tracks missing documents,
records an eligibility decision and approval, and preserves an event history.

## What happens to a claim

1. A requester submits a claim using a policy, incident, and amount.
2. The system checks policy and coverage dates, policy state, and exclusions.
3. Required evidence is requested. Assessment is blocked until the requested
   evidence is received.
4. An active adjuster with the right specialty is assigned.
5. The system calculates the eligible amount after the deductible and
   coverage limit, then records an assessment and reserve.
6. An approver approves or rejects the recommendation.
7. Payment can be recorded only after approval.
8. Each action creates a timestamped event with the actor, reason, and state
   change.

## What the data generator does

`generate.py` creates synthetic customers, policies, coverages, incidents,
claims, items, documents, adjusters, assessments, reserves, settlements, and
claim-state history. It creates 2,000 policies and 1,000 claims by default
across AUTO, HOME, HEALTH, LIFE, and TRAVEL. It writes CSV, JSON, and SQL
exports, a feature-only claim file, separate ground-truth labels, and a
manifest containing file hashes.

The six intentional challenge cases are: incident before policy start,
policy cancelled before incident, duplicate incident, settlement over limit,
missing mandatory document, and duplicate payment. The labels let the
validator distinguish these deliberately injected cases from unrelated bad
joins or malformed data.

## Useful files

- `app.py` and `workflow.py`: local browser app and claims workflow/API.
- `generate.py`: deterministic synthetic-data generator.
- `validate.py`: integrity and business-rule checks.
- `metrics.py`: summary measures for the generated dataset.
- `schema/schema.sql` and `data_dictionary.md`: relational structure and
  field definitions.
- `tests/`: automated workflow and generator tests.
- `AI_USAGE.md`: AI assistance disclosure and condensed prompt history.
- `DESIGN_NOTES.md`: assumptions, trade-offs, checks, and limitations.

## Demo sequence

1. Start `python app.py` and open `http://127.0.0.1:8000`.
2. Use the requester role to create a claim from the available reference
   policy/incident choices.
3. Switch to operations analyst to request/receive evidence, assign an
   adjuster, and assess the claim.
4. Switch to approver to approve or reject the recommendation.
5. After approval, record payment as administrator and inspect claim history
   and metrics.
6. Run the test and validation commands from `README.md` to show repeatable
   checks.

## How to explain the calculation

The simplified rule is:

```text
eligible amount = max(0, min(claim amount - deductible, coverage limit))
```

An invalid or excluded claim is not eligible. The amount cannot be negative or
exceed the applicable coverage limit. The model intentionally does not
calculate an aggregate remaining policy limit across multiple claims.

## Questions to be ready for

- Why is the data synthetic? It avoids distributing personal or insurer data
  and makes every run reproducible.
- How do you know joins are consistent? The validator checks primary keys,
  foreign keys, and cross-table joins.
- How are expected exceptions represented? They are intentionally injected
  and labeled separately in ground truth.
- How do you prevent label leakage? The feature projection omits outcome
  fields and labels are stored separately.
- What would you improve for production? Identity/security, real document
  handling, aggregate coverage accounting, domain-rule review, integrations,
  and load testing.
- What was AI's role? Read `AI_USAGE.md` and describe it honestly. Do not say
  the work was independently authored if it was AI-assisted.
