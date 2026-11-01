"""Compute reviewable business metrics from the generated CSV tables."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
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
    with (data_dir / "csv" / f"{table}.csv").open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def ratio(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


def build_metrics(data_dir: Path) -> dict:
    claims = read(data_dir, "claims")
    docs = read(data_dir, "documents")
    covers = {r["coverage_id"]: r for r in read(data_dir, "coverages")}
    assessments = {r["claim_id"]: r for r in read(data_dir, "assessments")}
    settlements = read(data_dir, "settlements")
    if (data_dir / "ground_truth.csv").exists():
        with (data_dir / "ground_truth.csv").open(newline="", encoding="utf-8") as stream:
            labels = list(csv.DictReader(stream))
    else:
        labels = []
    docs_by_claim: dict[str, set[str]] = {}
    for row in docs:
        if row.get("status", "").upper() in {"RECEIVED", "VERIFIED", "APPROVED"}:
            docs_by_claim.setdefault(row["claim_id"], set()).add(row["document_type"])
    complete = sum(REQUIRED.get(c["claim_type"], set()).issubset(docs_by_claim.get(c["claim_id"], set())) for c in claims)
    statuses = Counter(c["status"] for c in claims)
    types = Counter(c["claim_type"] for c in claims)
    denial_terms = ("OUTSIDE_COVERAGE", "CANCELLED", "LAPSED", "NO_COVERAGE", "EXCLUDED")
    coverage_failures = sum(any(term in (c.get("denial_reason") or "") for term in denial_terms) for c in claims)
    reserves = {r["claim_id"]: r for r in read(data_dir, "reserves")}
    reserve_checked = [c for c in claims if c["claim_id"] in reserves]
    reserve_ok = sum(0.8 * float(c["claim_amount"]) <= float(reserves[c["claim_id"]]["initial_reserve"]) <= 1.2 * float(c["claim_amount"]) for c in reserve_checked)
    over_limit_ids = {r["entity_id"] for r in labels if r.get("issue_type") == "SETTLEMENT_EXCEEDS_LIMIT"}
    within_limit = 0
    claim_by_id = {c["claim_id"]: c for c in claims}
    for s in settlements:
        if s["settlement_id"] in over_limit_ids:
            continue
        claim = next((c for c in claims if c["claim_id"] == s["claim_id"]), None)
        cov = covers.get(claim.get("coverage_id", "")) if claim else None
        if cov and float(s["settlement_amount"]) <= float(cov["coverage_limit"]):
            within_limit += 1
    settled_dates = []
    for s in settlements:
        c = claim_by_id.get(s["claim_id"])
        if c and s.get("payment_date"):
            try: settled_dates.append((date.fromisoformat(s["payment_date"]) - date.fromisoformat(c["submission_date"])).days)
            except ValueError: pass
    gt_counts = Counter(r.get("issue_type", "UNKNOWN") for r in labels)
    approved = sum(statuses.get(s, 0) for s in ("APPROVED", "SETTLED", "CLOSED"))
    return {
        "dataset": {"claims": len(claims), "policies": len(read(data_dir, "policies")), "claim_types": dict(sorted(types.items()))},
        "claim_outcomes": dict(sorted(statuses.items())),
        "approval_rate": ratio(approved, len(claims)),
        "coverage_failure_rate": ratio(coverage_failures, len(claims)),
        "required_document_completeness_rate": ratio(complete, len(claims)),
        "reserve_within_80_120_percent_rate": ratio(reserve_ok, len(reserve_checked)),
        "settlement_within_coverage_limit_rate_excluding_labelled_over_limit_cases": ratio(within_limit, len(settlements) - len(over_limit_ids)),
        "mean_days_submission_to_payment": round(sum(settled_dates) / len(settled_dates), 2) if settled_dates else None,
        "known_edge_case_counts": dict(sorted(gt_counts.items())),
        "notes": ["Metrics are descriptive of this synthetic dataset, not estimates of real insurer performance.",
                  "Intentional SETTLEMENT_EXCEEDS_LIMIT labels are excluded from normal settlement-limit compliance."]
    }


def main() -> None:
    p = argparse.ArgumentParser(); p.add_argument("--data-dir", default="output"); p.add_argument("--output", default=None); args = p.parse_args()
    data_dir = Path(args.data_dir); result = build_metrics(data_dir)
    raw = json.dumps(result, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(raw, encoding="utf-8")
    print(raw, end="")


if __name__ == "__main__": main()
