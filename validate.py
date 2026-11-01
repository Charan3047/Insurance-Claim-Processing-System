#!/usr/bin/env python3
"""
Insurance Claims Data — Validation Suite
=========================================
Reads the CSV output from generate.py and runs comprehensive checks:

  1. Primary-key uniqueness
  2. Foreign-key referential integrity
  3. Allowed enum values
  4. Date ordering / lifecycle consistency
  5. Arithmetic reconciliation (reserves, settlements vs. coverage limits)
  6. Lifecycle state transitions
  7. Duplicate detection
  8. Edge-case / ground-truth label verification

Usage:
    python validate.py                          # default output/ directory
    python validate.py --data-dir my_output     # custom directory
    python validate.py --verbose                # print every failing row
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict, Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

REQUIRED_DOCUMENTS = {
    "AUTO": {"POLICE_REPORT", "PHOTOS", "REPAIR_ESTIMATE", "DRIVERS_LICENSE"},
    "HOME": {"PHOTOS", "INVENTORY_LIST", "REPAIR_ESTIMATE", "POLICE_REPORT"},
    "HEALTH": {"MEDICAL_RECORDS", "BILLS", "REFERRAL", "ID_PROOF"},
    "LIFE": {"DEATH_CERTIFICATE", "MEDICAL_RECORDS", "ID_PROOF", "BENEFICIARY_PROOF"},
    "TRAVEL": {"TRAVEL_ITINERARY", "RECEIPTS", "MEDICAL_RECORDS", "AIRLINE_CONFIRMATION"},
}


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

class ValidationResult:
    """Accumulates pass / fail / warning results."""

    def __init__(self):
        self.checks: List[dict] = []
        self._pass = 0
        self._fail = 0
        self._warn = 0

    def ok(self, rule: str, detail: str = ""):
        self.checks.append({"status": "PASS", "rule": rule, "detail": detail})
        self._pass += 1

    def fail(self, rule: str, detail: str, rows: Optional[list] = None):
        entry = {"status": "FAIL", "rule": rule, "detail": detail}
        if rows:
            entry["sample_rows"] = rows[:10]
        self.checks.append(entry)
        self._fail += 1

    def warn(self, rule: str, detail: str):
        self.checks.append({"status": "WARN", "rule": rule, "detail": detail})
        self._warn += 1

    @property
    def passed(self) -> bool:
        return self._fail == 0

    def summary_line(self) -> str:
        return (
            f"  PASS: {self._pass}  |  FAIL: {self._fail}  |  WARN: {self._warn}"
        )


def _load_csv(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s or s.strip() == "":
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


def _to_float(s: Optional[str]) -> Optional[float]:
    if s is None or s.strip() == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN VALIDATOR
# ═══════════════════════════════════════════════════════════════════════════════

class DataValidator:
    def __init__(self, data_dir: str, verbose: bool = False):
        self.data_dir = data_dir
        self.verbose = verbose
        self.csv_dir = os.path.join(data_dir, "csv")
        self.r = ValidationResult()

        # Load all tables
        tables = [
            "customers", "adjusters", "policies", "coverages", "incidents",
            "claims", "claim_items", "documents", "assessments", "reserves",
            "settlements", "claim_state_history",
        ]
        self.data: Dict[str, List[dict]] = {}
        for t in tables:
            self.data[t] = _load_csv(os.path.join(self.csv_dir, f"{t}.csv"))

        # Ground truth
        gt_path = os.path.join(data_dir, "ground_truth.csv")
        self.ground_truth = _load_csv(gt_path)

        # Build indexes
        self._idx: Dict[str, Set[str]] = {}
        pk_map = {
            "customers": "customer_id",
            "adjusters": "adjuster_id",
            "policies": "policy_id",
            "coverages": "coverage_id",
            "incidents": "incident_id",
            "claims": "claim_id",
            "claim_items": "claim_item_id",
            "documents": "document_id",
            "assessments": "assessment_id",
            "reserves": "reserve_id",
            "settlements": "settlement_id",
            "claim_state_history": "event_id",
        }
        for tbl, pk in pk_map.items():
            self._idx[tbl] = {row[pk] for row in self.data[tbl] if pk in row}

    # ── 1. Primary-key uniqueness ─────────────────────────────────────────────

    def check_pk_uniqueness(self):
        pk_map = {
            "customers": "customer_id",
            "adjusters": "adjuster_id",
            "policies": "policy_id",
            "coverages": "coverage_id",
            "incidents": "incident_id",
            "claims": "claim_id",
            "claim_items": "claim_item_id",
            "documents": "document_id",
            "assessments": "assessment_id",
            "reserves": "reserve_id",
            "settlements": "settlement_id",
            "claim_state_history": "event_id",
        }
        for tbl, pk in pk_map.items():
            vals = [row[pk] for row in self.data[tbl] if pk in row]
            dupes = [v for v, c in Counter(vals).items() if c > 1]
            rule = f"PK_UNIQUE_{tbl.upper()}"
            if dupes:
                self.r.fail(rule, f"{len(dupes)} duplicate PKs in {tbl}", dupes)
            else:
                self.r.ok(rule, f"{len(vals)} unique keys")

    # ── 2. Foreign-key integrity ──────────────────────────────────────────────

    def check_fk_integrity(self):
        fk_checks = [
            ("policies",    "customer_id",          "customers"),
            ("coverages",   "policy_id",            "policies"),
            ("claims",      "policy_id",            "policies"),
            ("claims",      "customer_id",          "customers"),
            ("claims",      "incident_id",          "incidents"),
            ("claims",      "assigned_adjuster_id", "adjusters"),
            ("claims",      "coverage_id",          "coverages"),
            ("claim_items", "claim_id",             "claims"),
            ("documents",   "claim_id",             "claims"),
            ("documents",   "uploaded_by",         "customers"),
            ("assessments", "claim_id",             "claims"),
            ("assessments", "adjuster_id",          "adjusters"),
            ("reserves",    "claim_id",             "claims"),
            ("reserves",    "set_by",               "adjusters"),
            ("settlements", "claim_id",             "claims"),
            ("settlements", "assessment_id",        "assessments"),
            ("settlements", "approved_by",          "adjusters"),
            ("settlements", "payee_customer_id",    "customers"),
            ("claim_state_history", "claim_id",     "claims"),
        ]
        for child_tbl, fk_col, parent_tbl in fk_checks:
            parent_keys = self._idx[parent_tbl]
            orphans = []
            for row in self.data[child_tbl]:
                val = row.get(fk_col)
                if val and val.strip() and val not in parent_keys:
                    orphans.append(val)
            rule = f"FK_{child_tbl.upper()}.{fk_col.upper()}"
            if orphans:
                self.r.fail(rule, f"{len(orphans)} orphan FKs", orphans)
            else:
                self.r.ok(rule, f"All references valid -> {parent_tbl}")

    def check_join_consistency(self):
        policies = {r["policy_id"]: r for r in self.data["policies"]}
        coverage = {r["coverage_id"]: r for r in self.data["coverages"]}
        incidents = {r["incident_id"]: r for r in self.data["incidents"]}
        adjusters = {r["adjuster_id"]: r for r in self.data["adjusters"]}
        claims = {r["claim_id"]: r for r in self.data["claims"]}
        assessments = {r["assessment_id"]: r for r in self.data["assessments"]}
        bad_claims, bad_coverage, bad_adjusters, bad_assessments, bad_settlements = [], [], [], [], []
        for c in self.data["claims"]:
            pol = policies.get(c.get("policy_id"), {})
            inc = incidents.get(c.get("incident_id"), {})
            cov = coverage.get(c.get("coverage_id"))
            if c.get("customer_id") != pol.get("customer_id") or c.get("claim_type") != pol.get("product_type") or c.get("claim_type") != inc.get("product_category"):
                bad_claims.append(c["claim_id"])
            if cov and cov.get("policy_id") != c.get("policy_id"):
                bad_coverage.append(c["claim_id"])
            adj = adjusters.get(c.get("assigned_adjuster_id"), {})
            if c.get("assigned_adjuster_id") and (adj.get("status") != "ACTIVE" or c.get("claim_type") not in adj.get("specializations", "").split(",")):
                bad_adjusters.append(c["claim_id"])
        for row in self.data["coverages"]:
            pol = policies.get(row.get("policy_id"), {})
            if row.get("effective_date") != pol.get("effective_date") or row.get("expiration_date") != pol.get("expiration_date"):
                bad_coverage.append(row.get("coverage_id"))
        for row in self.data["claim_items"]:
            if row.get("item_category") != claims.get(row.get("claim_id"), {}).get("claim_type"):
                bad_claims.append(row.get("claim_item_id"))
        for row in self.data["assessments"]:
            if row.get("adjuster_id") != claims.get(row.get("claim_id"), {}).get("assigned_adjuster_id"):
                bad_assessments.append(row.get("assessment_id"))
        for row in self.data["settlements"]:
            assessment = assessments.get(row.get("assessment_id")) if row.get("assessment_id") else None
            if row.get("assessment_id") and (assessment is None or assessment.get("claim_id") != row.get("claim_id")):
                bad_settlements.append(row.get("settlement_id"))
        checks = [
            ("JOIN_CLAIM_MASTER_CONSISTENCY", bad_claims, "claim customer/product, incident product, and item type must agree"),
            ("JOIN_COVERAGE_POLICY_CONSISTENCY", bad_coverage, "claim coverage and policy coverage dates must agree"),
            ("JOIN_ACTIVE_SPECIALIST_ADJUSTER", bad_adjusters, "assigned adjuster must be active and specialized"),
            ("JOIN_ASSESSMENT_ADJUSTER", bad_assessments, "assessment adjuster must match claim assignment"),
            ("JOIN_SETTLEMENT_ASSESSMENT", bad_settlements, "settlement assessment must belong to the same claim"),
        ]
        for rule, bad, detail in checks:
            if bad: self.r.fail(rule, f"{len(bad)} mismatched joins: {detail}", bad)
            else: self.r.ok(rule, detail)

    # ── 3. Allowed enum values ────────────────────────────────────────────────

    def check_enums(self):
        enum_checks = {
            ("policies", "status"): {"ACTIVE", "EXPIRED", "CANCELLED", "LAPSED"},
            ("policies", "product_type"): {"AUTO", "HOME", "HEALTH", "LIFE", "TRAVEL"},
            ("policies", "payment_frequency"): {"MONTHLY", "QUARTERLY", "SEMI_ANNUAL", "ANNUAL"},
            ("policies", "underwriting_status"): {"APPROVED", "CONDITIONAL", "DECLINED"},
            ("claims", "status"): {
                "SUBMITTED", "UNDER_REVIEW", "PENDING_DOCUMENTS",
                "APPROVED", "DENIED", "SETTLED", "CLOSED",
            },
            ("claims", "claim_type"): {"AUTO", "HOME", "HEALTH", "LIFE", "TRAVEL"},
            ("claims", "priority"): {"LOW", "MEDIUM", "HIGH", "URGENT"},
            ("incidents", "severity"): {"LOW", "MEDIUM", "HIGH", "CRITICAL"},
            ("documents", "status"): {"RECEIVED", "VERIFIED", "REJECTED"},
            ("assessments", "recommendation"): {"APPROVE", "DENY", "REVIEW"},
            ("reserves", "status"): {"OPEN", "CLOSED"},
            ("reserves", "reserve_type"): {"CASE"},
            ("settlements", "status"): {"PENDING", "APPROVED", "PAID", "REJECTED"},
            ("settlements", "payment_method"): {"CHECK", "ACH", "WIRE_TRANSFER", "DIRECT_DEPOSIT"},
            ("adjusters", "status"): {"ACTIVE", "ON_LEAVE", "INACTIVE"},
            ("customers", "status"): {"ACTIVE", "INACTIVE"},
            ("coverages", "status"): {"ACTIVE", "INACTIVE"},
        }
        for (tbl, col), allowed in enum_checks.items():
            bad = []
            for row in self.data[tbl]:
                v = row.get(col, "")
                if v and v not in allowed:
                    bad.append(v)
            rule = f"ENUM_{tbl.upper()}.{col.upper()}"
            if bad:
                uniq = list(set(bad))
                self.r.fail(rule, f"{len(bad)} invalid values: {uniq[:5]}", uniq)
            else:
                self.r.ok(rule)

    # ── 4. Date ordering ─────────────────────────────────────────────────────

    def check_date_ordering(self):
        # Policy: effective_date < expiration_date
        bad = []
        for row in self.data["policies"]:
            s = _parse_date(row.get("effective_date"))
            e = _parse_date(row.get("expiration_date"))
            if s and e and s >= e:
                bad.append(row["policy_id"])
        rule = "DATE_POLICY_EFF_LT_EXP"
        if bad:
            self.r.fail(rule, f"{len(bad)} policies with start >= end", bad)
        else:
            self.r.ok(rule)

        # Coverage dates match policy dates
        pol_dates = {
            row["policy_id"]: (row["effective_date"], row["expiration_date"])
            for row in self.data["policies"]
        }
        bad = []
        for row in self.data["coverages"]:
            pid = row["policy_id"]
            if pid in pol_dates:
                pe, px = pol_dates[pid]
                if row["effective_date"] != pe or row["expiration_date"] != px:
                    bad.append(row["coverage_id"])
        rule = "DATE_COVERAGE_MATCHES_POLICY"
        if bad:
            self.r.warn(rule, f"{len(bad)} coverages with mismatched dates")
        else:
            self.r.ok(rule)

        # Claim submission_date >= incident reported_date (approximately)
        inc_dates = {row["incident_id"]: row.get("reported_date") for row in self.data["incidents"]}
        bad = []
        for row in self.data["claims"]:
            sd = _parse_date(row.get("submission_date"))
            rd = _parse_date(inc_dates.get(row.get("incident_id")))
            if sd and rd and sd < rd - __import__("datetime").timedelta(days=7):
                bad.append(row["claim_id"])
        rule = "DATE_CLAIM_AFTER_INCIDENT_REPORT"
        if bad:
            self.r.warn(rule, f"{len(bad)} claims submitted before incident report (>7d)")
        else:
            self.r.ok(rule)

        # Settlement approved_date <= payment_date
        bad = []
        for row in self.data["settlements"]:
            a = _parse_date(row.get("approved_date"))
            p = _parse_date(row.get("payment_date"))
            if a and p and a > p:
                bad.append(row["settlement_id"])
        rule = "DATE_SETTLEMENT_APPROVED_BEFORE_PAYMENT"
        if bad:
            self.r.warn(rule, f"{len(bad)} settlements approved after payment")
        else:
            self.r.ok(rule)

    # ── 5. Arithmetic reconciliation ──────────────────────────────────────────

    def check_arithmetic(self):
        # Claim item unit_cost * quantity ≈ claimed_amount (rough check)
        bad = []
        for row in self.data["claim_items"]:
            uc = _to_float(row.get("unit_cost"))
            qty = _to_float(row.get("quantity"))
            ca = _to_float(row.get("claimed_amount"))
            if uc is not None and qty is not None and ca is not None:
                expected = round(uc * qty, 2)
                # Allow 50% tolerance due to generation logic
                if ca > 0 and abs(expected - ca) / ca > 0.5:
                    bad.append(row["claim_item_id"])
        rule = "ARITH_ITEM_COST_CONSISTENCY"
        if bad:
            self.r.warn(rule, f"{len(bad)} items where unit_cost*qty differs >50% from claimed")
        else:
            self.r.ok(rule)

        # Assessment: eligible_amount <= coverage_limit
        bad = []
        for row in self.data["assessments"]:
            el = _to_float(row.get("eligible_amount"))
            cl = _to_float(row.get("coverage_limit"))
            if el is not None and cl is not None and cl > 0 and el > cl * 1.01:
                bad.append(row["assessment_id"])
        rule = "ARITH_ELIGIBLE_LE_LIMIT"
        if bad:
            self.r.fail(rule, f"{len(bad)} assessments with eligible > limit", bad)
        else:
            self.r.ok(rule)

        # Assessment: eligible_amount = min(assessed - deductible, limit) approximately
        bad = []
        for row in self.data["assessments"]:
            aa = _to_float(row.get("assessed_amount"))
            dd = _to_float(row.get("deductible_applied"))
            cl = _to_float(row.get("coverage_limit"))
            el = _to_float(row.get("eligible_amount"))
            if all(v is not None for v in [aa, dd, cl, el]):
                expected = max(0, aa - dd)
                if cl > 0:
                    expected = min(expected, cl)
                expected = round(expected, 2)
                if abs(expected - el) > 1.0:
                    bad.append(row["assessment_id"])
        rule = "ARITH_ELIGIBLE_FORMULA"
        if bad:
            self.r.fail(rule, f"{len(bad)} assessments with wrong eligible calc", bad)
        else:
            self.r.ok(rule)

        claim_amount = {r["claim_id"]: _to_float(r.get("claim_amount")) for r in self.data["claims"]}
        reserves_by_claim = {r["claim_id"]: r for r in self.data["reserves"]}
        bad = []
        for cid, row in reserves_by_claim.items():
            source = claim_amount.get(cid)
            reserve = _to_float(row.get("initial_reserve"))
            if source is not None and reserve is not None and not (source * 0.8 - 0.01 <= reserve <= source * 1.2 + 0.01):
                bad.append(row["reserve_id"])
        if bad:
            self.r.fail("ARITH_RESERVE_RANGE", f"{len(bad)} reserves outside 80–120% of claim", bad)
        else:
            self.r.ok("ARITH_RESERVE_RANGE")

        over_limit_ids = {r.get("entity_id") for r in self.ground_truth if r.get("issue_type") == "SETTLEMENT_EXCEEDS_LIMIT"}
        claims_by_id = {r["claim_id"]: r for r in self.data["claims"]}
        coverages_by_id = {r["coverage_id"]: r for r in self.data["coverages"]}
        assessments_by_id = {r["assessment_id"]: r for r in self.data["assessments"]}
        bad_limit, bad_match = [], []
        for row in self.data["settlements"]:
            if row.get("settlement_id") in over_limit_ids:
                continue
            claim = claims_by_id.get(row.get("claim_id"), {})
            coverage = coverages_by_id.get(claim.get("coverage_id"))
            amount = _to_float(row.get("settlement_amount"))
            limit = _to_float(coverage.get("coverage_limit")) if coverage else None
            if amount is not None and limit is not None and amount > limit + 0.01:
                bad_limit.append(row.get("settlement_id"))
            assessment = assessments_by_id.get(row.get("assessment_id"))
            eligible = _to_float(assessment.get("eligible_amount")) if assessment else None
            if amount is not None and eligible is not None and abs(amount - eligible) > 0.01:
                bad_match.append(row.get("settlement_id"))
        if bad_limit:
            self.r.fail("ARITH_SETTLEMENT_LIMIT", f"{len(bad_limit)} normal settlements exceed limit", bad_limit)
        else:
            self.r.ok("ARITH_SETTLEMENT_LIMIT")
        if bad_match:
            self.r.fail("ARITH_SETTLEMENT_ASSESSMENT", f"{len(bad_match)} settlements differ from assessment", bad_match)
        else:
            self.r.ok("ARITH_SETTLEMENT_ASSESSMENT")

    # ── 6. Lifecycle / state transitions ──────────────────────────────────────

    def check_lifecycle(self):
        valid_transitions = {
            None: {"SUBMITTED"},
            "SUBMITTED": {"UNDER_REVIEW", "DENIED"},
            "UNDER_REVIEW": {"APPROVED", "DENIED", "PENDING_DOCUMENTS"},
            "PENDING_DOCUMENTS": {"UNDER_REVIEW", "APPROVED", "DENIED"},
            "APPROVED": {"SETTLED", "DENIED"},
            "SETTLED": {"CLOSED"},
            "DENIED": set(),
            "CLOSED": set(),
        }
        # Group events by claim
        events_by_claim: Dict[str, List[dict]] = defaultdict(list)
        for ev in self.data["claim_state_history"]:
            events_by_claim[ev["claim_id"]].append(ev)

        bad = []
        for cid, evs in events_by_claim.items():
            evs.sort(key=lambda e: e["event_timestamp"])
            for ev in evs:
                frm = ev["from_status"] if ev["from_status"] else None
                to = ev["to_status"]
                allowed = valid_transitions.get(frm, set())
                if to not in allowed:
                    bad.append(f"{cid}: {frm} -> {to}")

        rule = "LIFECYCLE_VALID_TRANSITIONS"
        if bad:
            self.r.warn(
                rule,
                f"{len(bad)} invalid state transitions (may include edge cases)",
                bad,
            )
        else:
            self.r.ok(rule)

        # Denied claims should have a denial_reason
        no_reason = [
            r["claim_id"]
            for r in self.data["claims"]
            if r.get("status") == "DENIED" and not r.get("denial_reason")
        ]
        rule = "LIFECYCLE_DENIED_HAS_REASON"
        if no_reason:
            self.r.fail(rule, f"{len(no_reason)} denied claims without reason", no_reason)
        else:
            self.r.ok(rule)

        last_state = {}
        for cid, evs in events_by_claim.items():
            if evs:
                last_state[cid] = max(evs, key=lambda e: e["event_timestamp"])["to_status"]
        mismatched = [r["claim_id"] for r in self.data["claims"]
                      if r["claim_id"] in last_state and last_state[r["claim_id"]] != r.get("status")]
        if mismatched:
            self.r.fail("LIFECYCLE_FINAL_STATE_MATCH", f"{len(mismatched)} claims disagree with final event", mismatched)
        else:
            self.r.ok("LIFECYCLE_FINAL_STATE_MATCH")

    # ── 7. Duplicate detection ────────────────────────────────────────────────

    def check_duplicates(self):
        # Check for duplicate incidents (same type, date, location)
        seen = defaultdict(list)
        for row in self.data["incidents"]:
            key = (
                row.get("incident_type"),
                row.get("incident_date"),
                row.get("location_city"),
                row.get("location_state"),
            )
            seen[key].append(row["incident_id"])
        dupes = {k: v for k, v in seen.items() if len(v) > 1}
        rule = "DUP_INCIDENTS"
        if dupes:
            total = sum(len(v) - 1 for v in dupes.values())
            self.r.warn(rule, f"{total} potential duplicate incidents across {len(dupes)} groups")
        else:
            self.r.ok(rule)

        # Check for multiple settlements per claim
        sett_by_claim = defaultdict(list)
        for row in self.data["settlements"]:
            sett_by_claim[row["claim_id"]].append(row["settlement_id"])
        multi = {k: v for k, v in sett_by_claim.items() if len(v) > 1}
        rule = "DUP_SETTLEMENTS_PER_CLAIM"
        if multi:
            self.r.warn(
                rule,
                f"{len(multi)} claims with multiple settlements (may be edge cases)",
            )
        else:
            self.r.ok(rule)

    # ── 8. Ground-truth label verification ────────────────────────────────────

    def check_ground_truth(self):
        if not self.ground_truth:
            self.r.warn("GROUND_TRUTH_EXISTS", "No ground_truth.csv found")
            return

        self.r.ok("GROUND_TRUTH_EXISTS", f"{len(self.ground_truth)} anomaly labels")

        # Required edge-case types
        required_types = {
            "CLAIM_DATE_BEFORE_POLICY_START",
            "POLICY_CANCELLED_BEFORE_INCIDENT",
            "DUPLICATE_INCIDENT",
            "SETTLEMENT_EXCEEDS_LIMIT",
            "MISSING_MANDATORY_DOCUMENT",
            "CLAIM_PAID_TWICE",
        }
        found = {row["issue_type"] for row in self.ground_truth}
        missing = required_types - found
        rule = "GROUND_TRUTH_COVERAGE"
        if missing:
            self.r.fail(rule, f"Missing edge-case types: {missing}")
        else:
            self.r.ok(rule, f"All 6 required edge-case types present")

        # Distribution
        dist = Counter(row["issue_type"] for row in self.ground_truth)
        for it, cnt in sorted(dist.items()):
            self.r.ok(f"GROUND_TRUTH_COUNT_{it}", f"{cnt} cases")

        # Verify that ground-truth entity IDs exist in data
        entity_idx = {
            "CLAIM": self._idx.get("claims", set()),
            "INCIDENT": self._idx.get("incidents", set()),
            "SETTLEMENT": self._idx.get("settlements", set()),
        }
        bad = []
        for row in self.ground_truth:
            etype = row.get("entity_type")
            eid = row.get("entity_id")
            idx = entity_idx.get(etype, set())
            if eid and eid not in idx:
                bad.append(f"{etype}:{eid}")
        rule = "GROUND_TRUTH_ENTITY_EXISTS"
        if bad:
            self.r.fail(rule, f"{len(bad)} ground-truth refs to missing entities", bad)
        else:
            self.r.ok(rule, "All ground-truth entities exist in data")
        self.check_required_document_rows()
        self.check_edge_case_semantics()

    def check_required_document_rows(self):
        docs_by_claim: Dict[str, Set[str]] = defaultdict(set)
        for row in self.data["documents"]:
            docs_by_claim[row.get("claim_id", "")].add(row.get("document_type", ""))
        known_missing = {r.get("entity_id") for r in self.ground_truth
                         if r.get("issue_type") == "MISSING_MANDATORY_DOCUMENT"}
        bad = []
        for claim in self.data["claims"]:
            missing = REQUIRED_DOCUMENTS.get(claim.get("claim_type", ""), set()) - docs_by_claim.get(claim["claim_id"], set())
            if missing and claim["claim_id"] not in known_missing:
                bad.append(f"{claim['claim_id']}: {sorted(missing)}")
            if claim["claim_id"] in known_missing and not missing:
                bad.append(f"{claim['claim_id']}: label says missing but all required documents exist")
        if bad:
            self.r.fail("REQUIRED_DOCUMENT_ROWS", f"{len(bad)} required-document inconsistencies", bad)
        else:
            self.r.ok("REQUIRED_DOCUMENT_ROWS")

    def check_edge_case_semantics(self):
        claims = {r["claim_id"]: r for r in self.data["claims"]}
        policies = {r["policy_id"]: r for r in self.data["policies"]}
        incidents = {r["incident_id"]: r for r in self.data["incidents"]}
        coverages = {r["coverage_id"]: r for r in self.data["coverages"]}
        settlements = {r["settlement_id"]: r for r in self.data["settlements"]}
        by_claim_settlements: Dict[str, list] = defaultdict(list)
        for row in self.data["settlements"]:
            by_claim_settlements[row["claim_id"]].append(row)
        bad: Dict[str, list] = defaultdict(list)
        for label in self.ground_truth:
            typ, eid = label.get("issue_type"), label.get("entity_id")
            if typ == "CLAIM_DATE_BEFORE_POLICY_START":
                claim = claims.get(eid, {}); pol = policies.get(claim.get("policy_id"), {}); inc = incidents.get(claim.get("incident_id"), {})
                if not (pol.get("effective_date") and inc.get("incident_date") and inc["incident_date"] < pol["effective_date"]): bad[typ].append(eid)
            elif typ == "POLICY_CANCELLED_BEFORE_INCIDENT":
                claim = claims.get(eid, {}); pol = policies.get(claim.get("policy_id"), {}); inc = incidents.get(claim.get("incident_id"), {})
                if not (pol.get("status") == "CANCELLED" and pol.get("cancellation_date") and inc.get("incident_date") and inc["incident_date"] > pol["cancellation_date"]): bad[typ].append(eid)
            elif typ == "DUPLICATE_INCIDENT":
                one = incidents.get(eid, {}); two = incidents.get(label.get("related_entity_id"), {})
                if not one or not two or any(one.get(k) != two.get(k) for k in ("incident_type", "incident_date", "product_category")): bad[typ].append(eid)
            elif typ == "SETTLEMENT_EXCEEDS_LIMIT":
                settlement = settlements.get(eid, {}); cov = coverages.get(label.get("related_entity_id"), {})
                amount = _to_float(settlement.get("settlement_amount")); limit = _to_float(cov.get("coverage_limit"))
                if not settlement or not cov or amount is None or limit is None or amount <= limit: bad[typ].append(eid)
            elif typ == "MISSING_MANDATORY_DOCUMENT":
                claim = claims.get(eid, {}); present = {d.get("document_type") for d in self.data["documents"] if d.get("claim_id") == eid}
                if not (REQUIRED_DOCUMENTS.get(claim.get("claim_type", ""), set()) - present): bad[typ].append(eid)
            elif typ == "CLAIM_PAID_TWICE":
                settlement = settlements.get(eid, {}); paid = [s for s in by_claim_settlements.get(settlement.get("claim_id"), []) if s.get("status") == "PAID"]
                if not settlement or settlement.get("status") != "PAID" or len(paid) < 2: bad[typ].append(eid)
        required = {"CLAIM_DATE_BEFORE_POLICY_START", "POLICY_CANCELLED_BEFORE_INCIDENT", "DUPLICATE_INCIDENT",
                    "SETTLEMENT_EXCEEDS_LIMIT", "MISSING_MANDATORY_DOCUMENT", "CLAIM_PAID_TWICE"}
        found = {r.get("issue_type") for r in self.ground_truth}
        for typ in sorted(required):
            if bad.get(typ): self.r.fail(f"EDGE_SEMANTICS_{typ}", f"{len(bad[typ])} labels do not match source rows", bad[typ])
            elif typ in found: self.r.ok(f"EDGE_SEMANTICS_{typ}")
            else: self.r.fail(f"EDGE_SEMANTICS_{typ}", "No labelled examples found")

    def check_feature_separation(self):
        path = os.path.join(self.data_dir, "features", "claims_input.csv")
        if not os.path.exists(path):
            self.r.fail("FEATURE_VIEW_EXISTS", "features/claims_input.csv is missing")
            return
        with open(path, newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
            columns = set(rows[0].keys()) if rows else set()
        forbidden = {"status", "denial_reason", "issue_type", "expected_decision"}
        leaked = columns & forbidden
        if leaked:
            self.r.fail("FEATURE_OUTCOME_SEPARATION", f"Outcome fields leaked into features: {sorted(leaked)}")
        else:
            self.r.ok("FEATURE_OUTCOME_SEPARATION")
        if len(rows) != len(self.data["claims"]):
            self.r.fail("FEATURE_VIEW_ROW_COUNT", f"{len(rows)} feature rows for {len(self.data['claims'])} claims")
        else:
            self.r.ok("FEATURE_VIEW_ROW_COUNT", f"{len(rows)} rows")

    # ── 9. Volume checks ─────────────────────────────────────────────────────

    def check_volumes(self):
        expected = {
            "customers": (100, 2000),
            "policies": (100, 3000),
            "claims": (50, 1500),
            "adjusters": (5, 100),
        }
        for tbl, (lo, hi) in expected.items():
            n = len(self.data[tbl])
            rule = f"VOLUME_{tbl.upper()}"
            if lo <= n <= hi:
                self.r.ok(rule, f"{n} rows")
            else:
                self.r.fail(rule, f"{n} rows (expected {lo}-{hi})")

        # Claim type distribution
        type_dist = Counter(row["claim_type"] for row in self.data["claims"])
        rule = "VOLUME_CLAIM_TYPE_DISTRIBUTION"
        if len(type_dist) >= 5:
            self.r.ok(rule, f"5 types: {dict(type_dist)}")
        else:
            self.r.fail(rule, f"Only {len(type_dist)} claim types: {dict(type_dist)}")

    # ── Run all ───────────────────────────────────────────────────────────────

    def validate(self) -> ValidationResult:
        sections = [
            ("Primary-key uniqueness",      self.check_pk_uniqueness),
            ("Foreign-key integrity",        self.check_fk_integrity),
            ("Join consistency",              self.check_join_consistency),
            ("Allowed enum values",          self.check_enums),
            ("Date ordering / lifecycle",    self.check_date_ordering),
            ("Arithmetic reconciliation",    self.check_arithmetic),
            ("Lifecycle state transitions",  self.check_lifecycle),
            ("Duplicate detection",          self.check_duplicates),
            ("Ground-truth labels",          self.check_ground_truth),
            ("Feature separation",           self.check_feature_separation),
            ("Volume checks",               self.check_volumes),
        ]
        for title, fn in sections:
            print(f"\n  [{title}]")
            before = len(self.r.checks)
            fn()
            # Print recent results
            for chk in self.r.checks[before:]:
                if chk["status"] == "FAIL":
                    print(f"    FAIL  {chk['rule']}: {chk['detail']}")
                    if self.verbose and "sample_rows" in chk:
                        for s in chk["sample_rows"][:5]:
                            print(f"          -> {s}")
                elif chk["status"] == "WARN":
                    print(f"    WARN  {chk['rule']}: {chk['detail']}")

        return self.r


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Validate insurance claims data")
    ap.add_argument("--data-dir", default="output", help="Data directory")
    ap.add_argument("--verbose", action="store_true", help="Show failing rows")
    ap.add_argument("--json-report", help="Write JSON report to file")
    args = ap.parse_args()

    print(f"=== Insurance Claims Data Validator ===")
    print(f"    Data dir: {os.path.abspath(args.data_dir)}\n")

    v = DataValidator(args.data_dir, verbose=args.verbose)
    result = v.validate()

    print(f"\n{'='*50}")
    print(result.summary_line())
    print(f"  Overall: {'PASS' if result.passed else 'FAIL'}")
    print(f"{'='*50}")

    if args.json_report:
        with open(args.json_report, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "overall": "PASS" if result.passed else "FAIL",
                    "pass": result._pass,
                    "fail": result._fail,
                    "warn": result._warn,
                    "checks": result.checks,
                },
                f,
                indent=2,
            )
        print(f"\n  JSON report written to {args.json_report}")

    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
