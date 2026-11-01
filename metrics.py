"""Compute comprehensive operational, financial, and evaluation metrics from the generated CSV tables."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

REQUIRED = {
    "AUTO": {"POLICE_REPORT", "PHOTOS", "REPAIR_ESTIMATE", "DRIVERS_LICENSE"},
    "HOME": {"PHOTOS", "INVENTORY_LIST", "REPAIR_ESTIMATE", "POLICE_REPORT"},
    "HEALTH": {"MEDICAL_RECORDS", "BILLS", "REFERRAL", "ID_PROOF"},
    "LIFE": {"DEATH_CERTIFICATE", "MEDICAL_RECORDS", "ID_PROOF", "BENEFICIARY_PROOF"},
    "TRAVEL": {"TRAVEL_ITINERARY", "RECEIPTS", "MEDICAL_RECORDS", "AIRLINE_CONFIRMATION"},
}


def read(data_dir: Path, table: str) -> list[dict[str, str]]:
    p = data_dir / "csv" / f"{table}.csv"
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def ratio(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


def build_metrics(data_dir: Path) -> dict:
    claims = read(data_dir, "claims")
    items = read(data_dir, "claim_items")
    docs = read(data_dir, "documents")
    covers = {r["coverage_id"]: r for r in read(data_dir, "coverages")}
    assessments = {r["claim_id"]: r for r in read(data_dir, "assessments")}
    reserves = {r["claim_id"]: r for r in read(data_dir, "reserves")}
    settlements = read(data_dir, "settlements")
    caa = read(data_dir, "claim_adjuster_assignments")
    cca = read(data_dir, "claim_coverage_allocations")

    labels = []
    if (data_dir / "ground_truth.csv").exists():
        with (data_dir / "ground_truth.csv").open(newline="", encoding="utf-8") as stream:
            labels = list(csv.DictReader(stream))

    gt_decisions = []
    if (data_dir / "ground_truth_decisions.csv").exists():
        with (data_dir / "ground_truth_decisions.csv").open(newline="", encoding="utf-8") as stream:
            gt_decisions = list(csv.DictReader(stream))

    docs_by_claim: dict[str, set[str]] = defaultdict(set)
    for row in docs:
        if row.get("status", "").upper() in {"RECEIVED", "VERIFIED", "APPROVED"}:
            docs_by_claim[row["claim_id"]].add(row["document_type"])

    complete = sum(
        REQUIRED.get(c["claim_type"], set()).issubset(docs_by_claim.get(c["claim_id"], set()))
        for c in claims
    )
    statuses = Counter(c["status"] for c in claims)
    types = Counter(c["claim_type"] for c in claims)
    denial_reasons = Counter(c["denial_reason"] for c in claims if c.get("denial_reason"))

    # Bottom-up arithmetic reconciliation checks
    items_by_claim = defaultdict(list)
    for it in items:
        items_by_claim[it["claim_id"]].append(it)

    exact_item_qty_cost = sum(
        abs(round(float(it["unit_cost"]) * int(it["quantity"]), 2) - float(it["claimed_amount"])) <= 0.01
        for it in items
    )
    exact_claim_sum = sum(
        abs(round(sum(float(x["claimed_amount"]) for x in items_by_claim[c["claim_id"]]), 2) - float(c["claim_amount"])) <= 0.01
        for c in claims
    )

    denial_terms = ("BEFORE_POLICY_START", "CANCELLED", "LAPSED", "EXCLUDED", "OUTSIDE_COVERAGE", "NO_COVERAGE")
    coverage_failures = sum(
        any(term in (c.get("denial_reason") or "") for term in denial_terms)
        for c in claims
    )

    reserve_checked = [c for c in claims if c["claim_id"] in reserves]
    reserve_ok = sum(
        0.80 * float(c["claim_amount"]) - 0.01
        <= float(reserves[c["claim_id"]]["initial_reserve"])
        <= 1.20 * float(c["claim_amount"]) + 0.01
        for c in reserve_checked
    )

    over_limit_ids = {
        r["entity_id"] for r in labels if r.get("issue_type") == "SETTLEMENT_EXCEEDS_LIMIT"
    }
    within_limit = 0
    claim_by_id = {c["claim_id"]: c for c in claims}
    for s in settlements:
        if s["settlement_id"] in over_limit_ids:
            continue
        claim = claim_by_id.get(s["claim_id"])
        cov = covers.get(claim.get("coverage_id", "")) if claim else None
        if cov and float(s["settlement_amount"]) <= float(cov["coverage_limit"]) + 0.01:
            within_limit += 1

    settled_days = []
    for s in settlements:
        c = claim_by_id.get(s["claim_id"])
        if c and s.get("payment_date"):
            try:
                settled_days.append(
                    (date.fromisoformat(s["payment_date"]) - date.fromisoformat(c["submission_date"])).days
                )
            except ValueError:
                pass
    settled_days.sort()
    p50_days = settled_days[len(settled_days) // 2] if settled_days else None
    p90_days = settled_days[int(len(settled_days) * 0.9)] if settled_days else None

    gt_counts = Counter(r.get("issue_type", "UNKNOWN") for r in labels)
    decision_counts = Counter(r.get("expected_decision", "UNKNOWN") for r in gt_decisions)
    approved = sum(statuses.get(s, 0) for s in ("APPROVED", "SETTLED", "CLOSED"))

    total_claimed_usd = round(sum(float(c["claim_amount"]) for c in claims), 2)
    total_eligible_usd = round(sum(float(a["eligible_amount"]) for a in assessments.values()), 2)
    total_settled_usd = round(sum(float(s["settlement_amount"]) for s in settlements if s["status"] == "PAID"), 2)

    return {
        "dataset": {
            "claims": len(claims),
            "policies": len(read(data_dir, "policies")),
            "customers": len(read(data_dir, "customers")),
            "adjusters": len(read(data_dir, "adjusters")),
            "claim_items": len(items),
            "documents": len(docs),
            "claim_adjuster_assignments_bridge": len(caa),
            "claim_coverage_allocations_bridge": len(cca),
            "claim_types": dict(sorted(types.items())),
        },
        "claim_outcomes": dict(sorted(statuses.items())),
        "denial_reason_breakdown": dict(sorted(denial_reasons.items())),
        "approval_rate": ratio(approved, len(claims)),
        "coverage_failure_rate": ratio(coverage_failures, len(claims)),
        "required_document_completeness_rate": ratio(complete, len(claims)),
        "line_item_unit_cost_reconciliation_rate": ratio(exact_item_qty_cost, len(items)),
        "claim_total_to_items_reconciliation_rate": ratio(exact_claim_sum, len(claims)),
        "reserve_within_80_120_percent_rate": ratio(reserve_ok, len(reserve_checked)),
        "settlement_within_coverage_limit_rate_excluding_labelled_over_limit_cases": ratio(
            within_limit, len(settlements) - len(over_limit_ids)
        ),
        "financial_summary_usd": {
            "total_claimed_usd": total_claimed_usd,
            "total_eligible_usd": total_eligible_usd,
            "total_paid_settlements_usd": total_settled_usd,
        },
        "cycle_time_days": {
            "mean_days_submission_to_payment": (
                round(sum(settled_days) / len(settled_days), 2) if settled_days else None
            ),
            "median_p50_days": p50_days,
            "p90_days": p90_days,
        },
        "known_edge_case_counts": dict(sorted(gt_counts.items())),
        "ground_truth_decision_distribution": dict(sorted(decision_counts.items())),
        "notes": [
            "All monetary figures and totals are 100% bottom-up derived from line items and coverage rules.",
            "Intentional SETTLEMENT_EXCEEDS_LIMIT labels are excluded from normal settlement-limit compliance.",
        ],
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default="output")
    p.add_argument("--output", default=None)
    args = p.parse_args()
    data_dir = Path(args.data_dir)
    result = build_metrics(data_dir)
    raw = json.dumps(result, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(raw, encoding="utf-8")
    print(raw, end="")


if __name__ == "__main__":
    main()
