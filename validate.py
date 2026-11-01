#!/usr/bin/env python3
"""
Insurance Claims Data — Advanced Validation & Reconciliation Suite (v2.0)
=========================================================================
Reads the CSV output from generate.py and executes 12 comprehensive validation
sections (120+ deterministic checks):

  1. Primary-key uniqueness across all 14 tables (11 core + 2 N:M bridges + 1 event log)
  2. Foreign-key referential integrity across all 23 FK relationships
  3. Cross-table join & N:M bridge consistency
  4. Allowed enum values across all 22 enum columns
  5. Strict date & lifecycle ordering
  6. Exact penny-level bottom-up arithmetic reconciliation:
       - claim_items: round(quantity * unit_cost, 2) == claimed_amount (0.01 tolerance)
       - claims: round(sum(items.claimed_amount), 2) == claim_amount
       - assessments: round(sum(items.approved_amount), 2) == assessed_amount (for approved claims)
       - assessments: eligible_amount == 0.0 when denied; formula match when eligible
       - reserves & settlements: range and limit reconciliation
       - N:M coverage allocations: sum(allocated_claimed_amount) == claim_amount
  7. Lifecycle state machine transitions + Document & Fraud gating
  8. Duplicate detection (incidents & settlements) reconciled with ground truth
  9. Bi-directional ground-truth verification (verifies labelled rows AND verifies zero unlabelled leakage of injected anomaly classes)
 10. Strict feature-outcome & fraud-label separation (claims_input.csv)
 11. Volume & product distribution coverage
 12. Manifest SHA-256 integrity verification

Usage:
    python validate.py                          # default output/ directory
    python validate.py --data-dir my_output     # custom directory
    python validate.py --verbose                # print failing sample rows
    python validate.py --json-report report.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

REQUIRED_DOCUMENTS = {
    "AUTO": {"POLICE_REPORT", "PHOTOS", "REPAIR_ESTIMATE", "DRIVERS_LICENSE"},
    "HOME": {"PHOTOS", "INVENTORY_LIST", "REPAIR_ESTIMATE", "POLICE_REPORT"},
    "HEALTH": {"MEDICAL_RECORDS", "BILLS", "REFERRAL", "ID_PROOF"},
    "LIFE": {"DEATH_CERTIFICATE", "MEDICAL_RECORDS", "ID_PROOF", "BENEFICIARY_PROOF"},
    "TRAVEL": {"TRAVEL_ITINERARY", "RECEIPTS", "MEDICAL_RECORDS", "AIRLINE_CONFIRMATION"},
}


class ValidationResult:
    """Accumulates pass / fail / warning results."""

    def __init__(self) -> None:
        self.checks: List[dict] = []
        self._pass = 0
        self._fail = 0
        self._warn = 0

    def ok(self, rule: str, detail: str = "") -> None:
        self.checks.append({"status": "PASS", "rule": rule, "detail": detail})
        self._pass += 1

    def fail(self, rule: str, detail: str, rows: Optional[list] = None) -> None:
        entry: Dict[str, Any] = {"status": "FAIL", "rule": rule, "detail": detail}
        if rows:
            entry["sample_rows"] = rows[:10]
        self.checks.append(entry)
        self._fail += 1

    def warn(self, rule: str, detail: str, rows: Optional[list] = None) -> None:
        entry: Dict[str, Any] = {"status": "WARN", "rule": rule, "detail": detail}
        if rows:
            entry["sample_rows"] = rows[:10]
        self.checks.append(entry)
        self._warn += 1

    @property
    def passed(self) -> bool:
        return self._fail == 0

    def summary_line(self) -> str:
        return f"  PASS: {self._pass}  |  FAIL: {self._fail}  |  WARN: {self._warn}"


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
    if s is None or str(s).strip() == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


class DataValidator:
    PK_MAP = {
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
        "claim_adjuster_assignments": "assignment_id",
        "claim_coverage_allocations": "allocation_id",
        "claim_state_history": "event_id",
    }

    def __init__(self, data_dir: str, verbose: bool = False):
        self.data_dir = data_dir
        self.verbose = verbose
        self.csv_dir = os.path.join(data_dir, "csv")
        self.r = ValidationResult()

        self.data: Dict[str, List[dict]] = {}
        for t in self.PK_MAP:
            self.data[t] = _load_csv(os.path.join(self.csv_dir, f"{t}.csv"))

        self.ground_truth = _load_csv(os.path.join(data_dir, "ground_truth.csv"))
        self.ground_truth_decisions = _load_csv(
            os.path.join(data_dir, "ground_truth_decisions.csv")
        )

        self._idx: Dict[str, Set[str]] = {}
        for tbl, pk in self.PK_MAP.items():
            self._idx[tbl] = {row[pk] for row in self.data[tbl] if pk in row}

    # ── 1. Primary-key uniqueness & ID pattern compliance ─────────────────────

    def check_pk_uniqueness(self) -> None:
        for tbl, pk in self.PK_MAP.items():
            vals = [row[pk] for row in self.data[tbl] if pk in row]
            dupes = [v for v, c in Counter(vals).items() if c > 1]
            rule = f"PK_UNIQUE_{tbl.upper()}"
            if not vals:
                self.r.fail(rule, f"Table {tbl} is empty or missing")
            elif dupes:
                self.r.fail(rule, f"{len(dupes)} duplicate PKs in {tbl}", dupes)
            else:
                self.r.ok(rule, f"{len(vals)} unique keys")

    # ── 2. Foreign-key integrity (23 FK checks including N:M bridge tables) ───

    def check_fk_integrity(self) -> None:
        fk_checks = [
            ("policies",                   "customer_id",          "customers"),
            ("coverages",                  "policy_id",            "policies"),
            ("claims",                     "policy_id",            "policies"),
            ("claims",                     "customer_id",          "customers"),
            ("claims",                     "incident_id",          "incidents"),
            ("claims",                     "assigned_adjuster_id", "adjusters"),
            ("claims",                     "coverage_id",          "coverages"),
            ("claim_items",                "claim_id",             "claims"),
            ("documents",                  "claim_id",             "claims"),
            ("documents",                  "uploaded_by",          "customers"),
            ("assessments",                "claim_id",             "claims"),
            ("assessments",                "adjuster_id",          "adjusters"),
            ("reserves",                   "claim_id",             "claims"),
            ("reserves",                   "set_by",               "adjusters"),
            ("settlements",                "claim_id",             "claims"),
            ("settlements",                "assessment_id",        "assessments"),
            ("settlements",                "approved_by",          "adjusters"),
            ("settlements",                "payee_customer_id",    "customers"),
            ("claim_adjuster_assignments", "claim_id",             "claims"),
            ("claim_adjuster_assignments", "adjuster_id",          "adjusters"),
            ("claim_coverage_allocations", "claim_id",             "claims"),
            ("claim_coverage_allocations", "coverage_id",          "coverages"),
            ("claim_state_history",        "claim_id",             "claims"),
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

    # ── 3. Cross-table join & N:M bridge consistency ──────────────────────────

    def check_join_consistency(self) -> None:
        policies = {r["policy_id"]: r for r in self.data["policies"]}
        coverage = {r["coverage_id"]: r for r in self.data["coverages"]}
        incidents = {r["incident_id"]: r for r in self.data["incidents"]}
        adjusters = {r["adjuster_id"]: r for r in self.data["adjusters"]}
        claims = {r["claim_id"]: r for r in self.data["claims"]}
        assessments = {r["assessment_id"]: r for r in self.data["assessments"]}

        bad_claims, bad_coverage, bad_adjusters, bad_assessments, bad_settlements = [], [], [], [], []
        bad_caa, bad_cca = [], []

        for c in self.data["claims"]:
            pol = policies.get(c.get("policy_id"), {})
            inc = incidents.get(c.get("incident_id"), {})
            cov = coverage.get(c.get("coverage_id"))
            if (
                c.get("customer_id") != pol.get("customer_id")
                or c.get("claim_type") != pol.get("product_type")
                or c.get("claim_type") != inc.get("product_category")
            ):
                bad_claims.append(c["claim_id"])
            if cov and cov.get("policy_id") != c.get("policy_id"):
                bad_coverage.append(c["claim_id"])
            adj = adjusters.get(c.get("assigned_adjuster_id"), {})
            if c.get("assigned_adjuster_id") and (
                adj.get("status") != "ACTIVE"
                or c.get("claim_type") not in adj.get("specializations", "").split(",")
            ):
                bad_adjusters.append(c["claim_id"])

        for row in self.data["coverages"]:
            pol = policies.get(row.get("policy_id"), {})
            if (
                row.get("effective_date") != pol.get("effective_date")
                or row.get("expiration_date") != pol.get("expiration_date")
            ):
                bad_coverage.append(row.get("coverage_id"))

        for row in self.data["claim_items"]:
            if row.get("item_category") != claims.get(row.get("claim_id"), {}).get("claim_type"):
                bad_claims.append(row.get("claim_item_id"))

        for row in self.data["assessments"]:
            if row.get("adjuster_id") != claims.get(row.get("claim_id"), {}).get("assigned_adjuster_id"):
                bad_assessments.append(row.get("assessment_id"))

        for row in self.data["settlements"]:
            assessment = assessments.get(row.get("assessment_id")) if row.get("assessment_id") else None
            if row.get("assessment_id") and (
                assessment is None or assessment.get("claim_id") != row.get("claim_id")
            ):
                bad_settlements.append(row.get("settlement_id"))

        # Check N:M bridge tables
        primary_caa_by_claim = defaultdict(list)
        for row in self.data["claim_adjuster_assignments"]:
            if row.get("assignment_role") == "PRIMARY_ADJUSTER":
                primary_caa_by_claim[row["claim_id"]].append(row["adjuster_id"])
        for cid, c in claims.items():
            prim = primary_caa_by_claim.get(cid, [])
            if len(prim) != 1 or prim[0] != c.get("assigned_adjuster_id"):
                bad_caa.append(cid)

        for row in self.data["claim_coverage_allocations"]:
            clm = claims.get(row.get("claim_id"), {})
            cov = coverage.get(row.get("coverage_id"), {})
            if cov.get("policy_id") != clm.get("policy_id"):
                bad_cca.append(row.get("allocation_id"))

        checks = [
            ("JOIN_CLAIM_MASTER_CONSISTENCY", bad_claims, "claim customer/product, incident product, and item type agree"),
            ("JOIN_COVERAGE_POLICY_CONSISTENCY", bad_coverage, "claim coverage and policy coverage dates agree"),
            ("JOIN_ACTIVE_SPECIALIST_ADJUSTER", bad_adjusters, "assigned adjuster is active and specialized"),
            ("JOIN_ASSESSMENT_ADJUSTER", bad_assessments, "assessment adjuster matches claim assignment"),
            ("JOIN_SETTLEMENT_ASSESSMENT", bad_settlements, "settlement assessment belongs to same claim"),
            ("JOIN_BRIDGE_ADJUSTER_ASSIGNMENTS", bad_caa, "N:M claim_adjuster_assignments matches primary adjuster"),
            ("JOIN_BRIDGE_COVERAGE_ALLOCATIONS", bad_cca, "N:M claim_coverage_allocations belongs to claim policy"),
        ]
        for rule, bad, detail in checks:
            if bad:
                self.r.fail(rule, f"{len(bad)} mismatched joins: {detail}", bad)
            else:
                self.r.ok(rule, detail)

    # ── 4. Allowed enum values ────────────────────────────────────────────────

    def check_enums(self) -> None:
        enum_checks = {
            ("policies", "status"): {"ACTIVE", "EXPIRED", "CANCELLED", "LAPSED"},
            ("policies", "product_type"): {"AUTO", "HOME", "HEALTH", "LIFE", "TRAVEL"},
            ("policies", "payment_frequency"): {"MONTHLY", "QUARTERLY", "SEMI_ANNUAL", "ANNUAL"},
            ("policies", "underwriting_status"): {"APPROVED", "CONDITIONAL", "DECLINED"},
            ("claims", "status"): {
                "SUBMITTED", "PENDING_DOCUMENTS", "UNDER_REVIEW", "SIU_INVESTIGATION",
                "APPROVED", "DENIED", "SETTLED", "CLOSED",
            },
            ("claims", "claim_type"): {"AUTO", "HOME", "HEALTH", "LIFE", "TRAVEL"},
            ("claims", "priority"): {"LOW", "MEDIUM", "HIGH", "URGENT"},
            ("claim_items", "status"): {"PENDING", "APPROVED", "DENIED"},
            ("incidents", "severity"): {"LOW", "MEDIUM", "HIGH", "CRITICAL"},
            ("documents", "status"): {"REQUESTED", "RECEIVED", "VERIFIED", "REJECTED"},
            ("assessments", "recommendation"): {"APPROVE", "DENY", "REVIEW"},
            ("reserves", "status"): {"OPEN", "CLOSED"},
            ("reserves", "reserve_type"): {"CASE", "EXPENSE"},
            ("settlements", "status"): {"PENDING", "APPROVED", "PAID", "REJECTED"},
            ("settlements", "payment_method"): {"CHECK", "ACH", "WIRE_TRANSFER", "DIRECT_DEPOSIT"},
            ("adjusters", "status"): {"ACTIVE", "ON_LEAVE", "INACTIVE"},
            ("adjusters", "role_tier"): {"SENIOR_ADJUSTER", "STANDARD_ADJUSTER", "SIU_SPECIALIST"},
            ("customers", "status"): {"ACTIVE", "INACTIVE"},
            ("customers", "risk_tier"): {"LOW", "STANDARD", "ELEVATED"},
            ("coverages", "status"): {"ACTIVE", "INACTIVE"},
            ("claim_adjuster_assignments", "assignment_role"): {
                "PRIMARY_ADJUSTER", "SIU_INVESTIGATOR", "SUPERVISOR_REVIEWER", "CO_ADJUSTER",
            },
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
                self.r.ok(rule, f"All values in {sorted(allowed)}")

    # ── 5. Date ordering ──────────────────────────────────────────────────────

    def check_date_ordering(self) -> None:
        bad = []
        for row in self.data["policies"]:
            s = _parse_date(row.get("effective_date"))
            e = _parse_date(row.get("expiration_date"))
            if s and e and s >= e:
                bad.append(row["policy_id"])
        if bad:
            self.r.fail("DATE_POLICY_EFF_LT_EXP", f"{len(bad)} policies with start >= end", bad)
        else:
            self.r.ok("DATE_POLICY_EFF_LT_EXP", "All policies have effective_date < expiration_date")

        pol_dates = {
            row["policy_id"]: (row["effective_date"], row["expiration_date"])
            for row in self.data["policies"]
        }
        bad = [
            row["coverage_id"]
            for row in self.data["coverages"]
            if row["policy_id"] in pol_dates
            and (row["effective_date"], row["expiration_date"]) != pol_dates[row["policy_id"]]
        ]
        if bad:
            self.r.fail("DATE_COVERAGE_MATCHES_POLICY", f"{len(bad)} coverages with mismatched dates", bad)
        else:
            self.r.ok("DATE_COVERAGE_MATCHES_POLICY", "All coverage dates align with parent policy")

        inc_dates = {
            row["incident_id"]: (row.get("incident_date"), row.get("reported_date"))
            for row in self.data["incidents"]
        }
        bad_inc_rep = []
        for iid, (idate, rdate) in inc_dates.items():
            d1, d2 = _parse_date(idate), _parse_date(rdate)
            if d1 and d2 and d2 < d1:
                bad_inc_rep.append(iid)
        if bad_inc_rep:
            self.r.fail("DATE_INCIDENT_REPORTED_GE_INCIDENT", f"{len(bad_inc_rep)} incidents reported before occurrence", bad_inc_rep)
        else:
            self.r.ok("DATE_INCIDENT_REPORTED_GE_INCIDENT", "All incidents have reported_date >= incident_date")

        bad_sub = []
        for row in self.data["claims"]:
            sd = _parse_date(row.get("submission_date"))
            _, rd_str = inc_dates.get(row.get("incident_id"), (None, None))
            rd = _parse_date(rd_str)
            if sd and rd and sd < rd:
                bad_sub.append(row["claim_id"])
        if bad_sub:
            self.r.fail("DATE_CLAIM_AFTER_INCIDENT_REPORT", f"{len(bad_sub)} claims submitted before incident report", bad_sub)
        else:
            self.r.ok("DATE_CLAIM_AFTER_INCIDENT_REPORT", "All claims have submission_date >= incident reported_date")

        bad_sett = []
        for row in self.data["settlements"]:
            a = _parse_date(row.get("approved_date"))
            p = _parse_date(row.get("payment_date"))
            if a and p and a > p:
                bad_sett.append(row["settlement_id"])
        if bad_sett:
            self.r.fail("DATE_SETTLEMENT_APPROVED_BEFORE_PAYMENT", f"{len(bad_sett)} settlements approved after payment", bad_sett)
        else:
            self.r.ok("DATE_SETTLEMENT_APPROVED_BEFORE_PAYMENT", "All settlements have approved_date <= payment_date")

    # ── 6. Exact Bottom-Up Arithmetic Reconciliation ──────────────────────────

    def check_arithmetic(self) -> None:
        # 6a. Exact unit_cost * quantity == claimed_amount (0.01 tolerance)
        bad_unit = []
        items_by_claim: Dict[str, List[dict]] = defaultdict(list)
        for row in self.data["claim_items"]:
            items_by_claim[row["claim_id"]].append(row)
            uc = _to_float(row.get("unit_cost"))
            qty = _to_float(row.get("quantity"))
            ca = _to_float(row.get("claimed_amount"))
            if uc is not None and qty is not None and ca is not None:
                if abs(round(uc * qty, 2) - ca) > 0.01:
                    bad_unit.append(row["claim_item_id"])
        if bad_unit:
            self.r.fail("ARITH_ITEM_COST_CONSISTENCY", f"{len(bad_unit)} items fail unit_cost * qty == claimed_amount", bad_unit)
        else:
            self.r.ok("ARITH_ITEM_COST_CONSISTENCY", "100% of claim_items satisfy round(unit_cost * quantity, 2) == claimed_amount")

        # 6b. Exact sum(claim_items.claimed_amount) == claims.claim_amount
        bad_claim_sum = []
        for c in self.data["claims"]:
            cid = c["claim_id"]
            ca = _to_float(c.get("claim_amount"))
            item_sum = round(sum(_to_float(x.get("claimed_amount")) or 0.0 for x in items_by_claim[cid]), 2)
            if ca is None or abs(item_sum - ca) > 0.01:
                bad_claim_sum.append(cid)
        if bad_claim_sum:
            self.r.fail("ARITH_CLAIM_AMOUNT_EQUALS_ITEM_SUM", f"{len(bad_claim_sum)} claims differ from sum of claim_items", bad_claim_sum)
        else:
            self.r.ok("ARITH_CLAIM_AMOUNT_EQUALS_ITEM_SUM", "100% of claims satisfy claim_amount == sum(claim_items.claimed_amount)")

        # 6c. Exact sum(claim_items.approved_amount) == assessments.assessed_amount (for APPROVED/SETTLED/CLOSED)
        claims_by_id = {c["claim_id"]: c for c in self.data["claims"]}
        bad_assessed_sum = []
        bad_denied_eligible = []
        bad_limit = []
        bad_formula = []
        for row in self.data["assessments"]:
            cid = row["claim_id"]
            clm = claims_by_id.get(cid, {})
            aa = _to_float(row.get("assessed_amount"))
            dd = _to_float(row.get("deductible_applied"))
            cl = _to_float(row.get("coverage_limit"))
            el = _to_float(row.get("eligible_amount"))
            if clm.get("status") in ("APPROVED", "SETTLED", "CLOSED"):
                app_sum = round(sum(_to_float(x.get("approved_amount")) or 0.0 for x in items_by_claim[cid]), 2)
                if aa is None or abs(app_sum - aa) > 0.01:
                    bad_assessed_sum.append(row["assessment_id"])
            if clm.get("status") == "DENIED" or row.get("recommendation") == "DENY":
                if el is None or el > 0.0:
                    bad_denied_eligible.append(row["assessment_id"])
            else:
                if el is not None and cl is not None and cl > 0 and el > cl + 0.01:
                    bad_limit.append(row["assessment_id"])
                if all(v is not None for v in (aa, dd, cl, el)):
                    expected = min(max(0.0, round(aa - dd, 2)), cl) if cl > 0 else max(0.0, round(aa - dd, 2))
                    if abs(expected - el) > 0.01:
                        bad_formula.append(row["assessment_id"])

        if bad_assessed_sum:
            self.r.fail("ARITH_ASSESSED_AMOUNT_EQUALS_APPROVED_ITEMS", f"{len(bad_assessed_sum)} assessments differ from approved item sum", bad_assessed_sum)
        else:
            self.r.ok("ARITH_ASSESSED_AMOUNT_EQUALS_APPROVED_ITEMS", "100% of approved assessments equal sum(claim_items.approved_amount)")

        if bad_denied_eligible:
            self.r.fail("ARITH_DENIED_ELIGIBLE_IS_ZERO", f"{len(bad_denied_eligible)} denied claims have eligible_amount > 0", bad_denied_eligible)
        else:
            self.r.ok("ARITH_DENIED_ELIGIBLE_IS_ZERO", "100% of denied claims have eligible_amount == 0.00")

        if bad_limit:
            self.r.fail("ARITH_ELIGIBLE_LE_LIMIT", f"{len(bad_limit)} assessments exceed coverage limit", bad_limit)
        else:
            self.r.ok("ARITH_ELIGIBLE_LE_LIMIT", "All eligible amounts <= coverage_limit")

        if bad_formula:
            self.r.fail("ARITH_ELIGIBLE_FORMULA", f"{len(bad_formula)} assessments fail min(max(0, assessed - deductible), limit)", bad_formula)
        else:
            self.r.ok("ARITH_ELIGIBLE_FORMULA", "100% of assessments satisfy exact deductible & limit formula")

        # 6d. Reserve range & status consistency
        bad_rsv = []
        for row in self.data["reserves"]:
            cid = row["claim_id"]
            ca = _to_float(claims_by_id.get(cid, {}).get("claim_amount"))
            ir = _to_float(row.get("initial_reserve"))
            if ca is not None and ir is not None and not (ca * 0.80 - 0.01 <= ir <= ca * 1.20 + 0.01):
                bad_rsv.append(row["reserve_id"])
        if bad_rsv:
            self.r.fail("ARITH_RESERVE_RANGE", f"{len(bad_rsv)} reserves outside 80-120% of claim", bad_rsv)
        else:
            self.r.ok("ARITH_RESERVE_RANGE", "All initial reserves within 80-120% of claim_amount")

        # 6e. Settlements vs limit & assessment (excluding labelled SETTLEMENT_EXCEEDS_LIMIT)
        over_limit_ids = {
            r.get("entity_id")
            for r in self.ground_truth
            if r.get("issue_type") == "SETTLEMENT_EXCEEDS_LIMIT"
        }
        coverages_by_id = {r["coverage_id"]: r for r in self.data["coverages"]}
        assessments_by_id = {r["assessment_id"]: r for r in self.data["assessments"]}
        bad_s_limit, bad_s_match = [], []
        for row in self.data["settlements"]:
            if row.get("settlement_id") in over_limit_ids:
                continue
            clm = claims_by_id.get(row.get("claim_id"), {})
            cov = coverages_by_id.get(clm.get("coverage_id"))
            amt = _to_float(row.get("settlement_amount"))
            lim = _to_float(cov.get("coverage_limit")) if cov else None
            if amt is not None and lim is not None and amt > lim + 0.01:
                bad_s_limit.append(row["settlement_id"])
            asn = assessments_by_id.get(row.get("assessment_id"))
            elig = _to_float(asn.get("eligible_amount")) if asn else None
            if amt is not None and elig is not None and abs(amt - elig) > 0.01:
                bad_s_match.append(row["settlement_id"])

        if bad_s_limit:
            self.r.fail("ARITH_SETTLEMENT_LIMIT", f"{len(bad_s_limit)} normal settlements exceed coverage_limit", bad_s_limit)
        else:
            self.r.ok("ARITH_SETTLEMENT_LIMIT", "All non-anomaly settlements <= coverage_limit")

        if bad_s_match:
            self.r.fail("ARITH_SETTLEMENT_ASSESSMENT", f"{len(bad_s_match)} settlements differ from assessment eligible_amount", bad_s_match)
        else:
            self.r.ok("ARITH_SETTLEMENT_ASSESSMENT", "All non-anomaly settlements equal assessment.eligible_amount")

        # 6f. N:M Coverage Allocation Bridge sum reconciliation
        alloc_by_claim: Dict[str, float] = defaultdict(float)
        for row in self.data["claim_coverage_allocations"]:
            alloc_by_claim[row["claim_id"]] = round(
                alloc_by_claim[row["claim_id"]] + (_to_float(row.get("allocated_claimed_amount")) or 0.0), 2
            )
        bad_alloc = [
            c["claim_id"]
            for c in self.data["claims"]
            if abs(alloc_by_claim.get(c["claim_id"], 0.0) - (_to_float(c.get("claim_amount")) or 0.0)) > 0.01
        ]
        if bad_alloc:
            self.r.fail("ARITH_BRIDGE_COVERAGE_ALLOCATION_SUM", f"{len(bad_alloc)} claims fail allocation bridge sum", bad_alloc)
        else:
            self.r.ok("ARITH_BRIDGE_COVERAGE_ALLOCATION_SUM", "100% of claim_coverage_allocations sum to claim_amount")

    # ── 7. Lifecycle State Transitions + Document & Fraud Gating ──────────────

    def check_lifecycle(self) -> None:
        valid_transitions = {
            None: {"SUBMITTED"},
            "SUBMITTED": {"UNDER_REVIEW", "PENDING_DOCUMENTS", "DENIED"},
            "PENDING_DOCUMENTS": {"UNDER_REVIEW", "DENIED"},
            "UNDER_REVIEW": {"APPROVED", "DENIED", "PENDING_DOCUMENTS", "SIU_INVESTIGATION"},
            "SIU_INVESTIGATION": {"APPROVED", "DENIED"},
            "APPROVED": {"SETTLED", "DENIED"},
            "SETTLED": {"CLOSED"},
            "DENIED": set(),
            "CLOSED": set(),
        }
        events_by_claim: Dict[str, List[dict]] = defaultdict(list)
        for ev in self.data["claim_state_history"]:
            events_by_claim[ev["claim_id"]].append(ev)

        bad_trans = []
        for cid, evs in events_by_claim.items():
            evs.sort(key=lambda e: e["event_timestamp"])
            for ev in evs:
                frm = ev["from_status"] if ev["from_status"] else None
                to = ev["to_status"]
                if to not in valid_transitions.get(frm, set()):
                    bad_trans.append(f"{cid}: {frm} -> {to}")
        if bad_trans:
            self.r.fail("LIFECYCLE_VALID_TRANSITIONS", f"{len(bad_trans)} invalid state transitions", bad_trans)
        else:
            self.r.ok("LIFECYCLE_VALID_TRANSITIONS", "All state transitions follow allowed state-machine edges")

        no_reason = [
            r["claim_id"]
            for r in self.data["claims"]
            if r.get("status") == "DENIED" and not r.get("denial_reason")
        ]
        if no_reason:
            self.r.fail("LIFECYCLE_DENIED_HAS_REASON", f"{len(no_reason)} denied claims without reason", no_reason)
        else:
            self.r.ok("LIFECYCLE_DENIED_HAS_REASON", "All denied claims record an explicit denial_reason")

        last_state = {
            cid: max(evs, key=lambda e: e["event_timestamp"])["to_status"]
            for cid, evs in events_by_claim.items()
            if evs
        }
        mismatched = [
            r["claim_id"]
            for r in self.data["claims"]
            if r["claim_id"] in last_state and last_state[r["claim_id"]] != r.get("status")
        ]
        if mismatched:
            self.r.fail("LIFECYCLE_FINAL_STATE_MATCH", f"{len(mismatched)} claims disagree with final event", mismatched)
        else:
            self.r.ok("LIFECYCLE_FINAL_STATE_MATCH", "All claims match their latest claim_state_history event")

        # Verify zero APPROVED/SETTLED/CLOSED claims have REJECTED documents or unreviewed fraud_indicator
        docs_by_claim = defaultdict(list)
        for d in self.data["documents"]:
            docs_by_claim[d["claim_id"]].append(d)
        bad_doc_gate = [
            c["claim_id"]
            for c in self.data["claims"]
            if c.get("status") in ("APPROVED", "SETTLED", "CLOSED")
            and any(d.get("status") == "REJECTED" for d in docs_by_claim[c["claim_id"]])
        ]
        if bad_doc_gate:
            self.r.fail("LIFECYCLE_DOCUMENT_GATING", f"{len(bad_doc_gate)} approved/settled claims have REJECTED documents", bad_doc_gate)
        else:
            self.r.ok("LIFECYCLE_DOCUMENT_GATING", "Zero approved/settled claims have rejected mandatory documents")

        bad_fraud_gate = [
            c["claim_id"]
            for c in self.data["claims"]
            if str(c.get("fraud_indicator")).lower() in ("true", "1")
            and c.get("status") in ("APPROVED", "SETTLED", "CLOSED")
        ]
        if bad_fraud_gate:
            self.r.fail("LIFECYCLE_FRAUD_SIU_GATING", f"{len(bad_fraud_gate)} fraud-flagged claims were approved/paid", bad_fraud_gate)
        else:
            self.r.ok("LIFECYCLE_FRAUD_SIU_GATING", "Zero fraud-flagged claims bypassed SIU/denial gating")

    # ── 8. Duplicate detection (reconciled with Ground Truth!) ────────────────

    def check_duplicates(self) -> None:
        gt_dup_inc = {
            r["entity_id"]
            for r in self.ground_truth
            if r.get("issue_type") == "DUPLICATE_INCIDENT"
        }
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
        unlabelled_dup_inc = []
        for ids in dupes.values():
            if not any(i in gt_dup_inc for i in ids):
                unlabelled_dup_inc.extend(ids)
        if unlabelled_dup_inc:
            self.r.fail("DUP_INCIDENTS_RECONCILED", f"{len(unlabelled_dup_inc)} unlabelled duplicate incidents", unlabelled_dup_inc)
        else:
            self.r.ok("DUP_INCIDENTS_RECONCILED", f"All {len(dupes)} duplicate incident groups are accounted for in ground_truth")

        gt_paid_twice_claims = set()
        sett_by_id = {s["settlement_id"]: s for s in self.data["settlements"]}
        for r in self.ground_truth:
            if r.get("issue_type") == "CLAIM_PAID_TWICE" and r.get("entity_id") in sett_by_id:
                gt_paid_twice_claims.add(sett_by_id[r["entity_id"]]["claim_id"])

        sett_by_claim = defaultdict(list)
        for row in self.data["settlements"]:
            sett_by_claim[row["claim_id"]].append(row["settlement_id"])
        multi = {k: v for k, v in sett_by_claim.items() if len(v) > 1}
        unlabelled_multi = [cid for cid in multi if cid not in gt_paid_twice_claims]
        if unlabelled_multi:
            self.r.fail("DUP_SETTLEMENTS_RECONCILED", f"{len(unlabelled_multi)} unlabelled multi-settlement claims", unlabelled_multi)
        else:
            self.r.ok("DUP_SETTLEMENTS_RECONCILED", f"All {len(multi)} multi-settlement claims are accounted for in ground_truth")

    # ── 9. Bi-directional Ground-Truth Label Verification ─────────────────────

    def check_ground_truth(self) -> None:
        if not self.ground_truth:
            self.r.fail("GROUND_TRUTH_EXISTS", "No ground_truth.csv found")
            return
        self.r.ok("GROUND_TRUTH_EXISTS", f"{len(self.ground_truth)} anomaly labels")

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
        if missing:
            self.r.fail("GROUND_TRUTH_COVERAGE", f"Missing edge-case types: {missing}")
        else:
            self.r.ok("GROUND_TRUTH_COVERAGE", "All 6 required edge-case types present")

        dist = Counter(row["issue_type"] for row in self.ground_truth)
        for it, cnt in sorted(dist.items()):
            self.r.ok(f"GROUND_TRUTH_COUNT_{it}", f"{cnt} cases")

        entity_idx = {
            "CLAIM": self._idx.get("claims", set()),
            "INCIDENT": self._idx.get("incidents", set()),
            "SETTLEMENT": self._idx.get("settlements", set()),
        }
        bad = [
            f"{row.get('entity_type')}:{row.get('entity_id')}"
            for row in self.ground_truth
            if row.get("entity_id") not in entity_idx.get(row.get("entity_type", ""), set())
        ]
        if bad:
            self.r.fail("GROUND_TRUTH_ENTITY_EXISTS", f"{len(bad)} ground-truth refs to missing entities", bad)
        else:
            self.r.ok("GROUND_TRUTH_ENTITY_EXISTS", "All ground-truth entities exist in data")

        self.check_required_document_rows()
        self.check_edge_case_semantics()

    def check_required_document_rows(self) -> None:
        docs_by_claim: Dict[str, Set[str]] = defaultdict(set)
        for row in self.data["documents"]:
            docs_by_claim[row.get("claim_id", "")].add(row.get("document_type", ""))
        known_missing = {
            r.get("entity_id")
            for r in self.ground_truth
            if r.get("issue_type") == "MISSING_MANDATORY_DOCUMENT"
        }
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
            self.r.ok("REQUIRED_DOCUMENT_ROWS", "Required documents match ground-truth missing-document labels 100%")

    def check_edge_case_semantics(self) -> None:
        claims = {r["claim_id"]: r for r in self.data["claims"]}
        policies = {r["policy_id"]: r for r in self.data["policies"]}
        incidents = {r["incident_id"]: r for r in self.data["incidents"]}
        coverages = {r["coverage_id"]: r for r in self.data["coverages"]}
        settlements = {r["settlement_id"]: r for r in self.data["settlements"]}
        by_claim_settlements: Dict[str, list] = defaultdict(list)
        for row in self.data["settlements"]:
            by_claim_settlements[row["claim_id"]].append(row)

        bad: Dict[str, list] = defaultdict(list)
        gt_by_type: Dict[str, Set[str]] = defaultdict(set)
        for label in self.ground_truth:
            typ, eid = label.get("issue_type"), label.get("entity_id")
            gt_by_type[typ].add(eid)
            if typ == "CLAIM_DATE_BEFORE_POLICY_START":
                claim = claims.get(eid, {})
                pol = policies.get(claim.get("policy_id"), {})
                inc = incidents.get(claim.get("incident_id"), {})
                if not (pol.get("effective_date") and inc.get("incident_date") and inc["incident_date"] < pol["effective_date"]):
                    bad[typ].append(eid)
            elif typ == "POLICY_CANCELLED_BEFORE_INCIDENT":
                claim = claims.get(eid, {})
                pol = policies.get(claim.get("policy_id"), {})
                inc = incidents.get(claim.get("incident_id"), {})
                if not (pol.get("status") == "CANCELLED" and pol.get("cancellation_date") and inc.get("incident_date") and inc["incident_date"] > pol["cancellation_date"]):
                    bad[typ].append(eid)
            elif typ == "DUPLICATE_INCIDENT":
                one = incidents.get(eid, {})
                two = incidents.get(label.get("related_entity_id"), {})
                if not one or not two or any(one.get(k) != two.get(k) for k in ("incident_type", "incident_date", "product_category")):
                    bad[typ].append(eid)
            elif typ == "SETTLEMENT_EXCEEDS_LIMIT":
                settlement = settlements.get(eid, {})
                cov = coverages.get(label.get("related_entity_id"), {})
                amount = _to_float(settlement.get("settlement_amount"))
                limit = _to_float(cov.get("coverage_limit"))
                if not settlement or not cov or amount is None or limit is None or amount <= limit:
                    bad[typ].append(eid)
            elif typ == "MISSING_MANDATORY_DOCUMENT":
                claim = claims.get(eid, {})
                present = {d.get("document_type") for d in self.data["documents"] if d.get("claim_id") == eid}
                if not (REQUIRED_DOCUMENTS.get(claim.get("claim_type", ""), set()) - present):
                    bad[typ].append(eid)
            elif typ == "CLAIM_PAID_TWICE":
                settlement = settlements.get(eid, {})
                paid = [s for s in by_claim_settlements.get(settlement.get("claim_id"), []) if s.get("status") == "PAID"]
                if not settlement or settlement.get("status") != "PAID" or len(paid) < 2:
                    bad[typ].append(eid)

        # Bi-directional check: ensure NO unlabelled claims accidentally have CLAIM_DATE_BEFORE_POLICY_START or POLICY_CANCELLED_BEFORE_INCIDENT
        unlabelled_early = []
        unlabelled_cancelled = []
        for cid, c in claims.items():
            pol = policies.get(c["policy_id"], {})
            inc = incidents.get(c["incident_id"], {})
            if inc.get("incident_date") and pol.get("effective_date") and inc["incident_date"] < pol["effective_date"]:
                if cid not in gt_by_type["CLAIM_DATE_BEFORE_POLICY_START"]:
                    unlabelled_early.append(cid)
            if pol.get("status") == "CANCELLED" and pol.get("cancellation_date") and inc.get("incident_date") and inc["incident_date"] > pol["cancellation_date"]:
                if cid not in gt_by_type["POLICY_CANCELLED_BEFORE_INCIDENT"]:
                    unlabelled_cancelled.append(cid)

        for typ in sorted({
            "CLAIM_DATE_BEFORE_POLICY_START", "POLICY_CANCELLED_BEFORE_INCIDENT",
            "DUPLICATE_INCIDENT", "SETTLEMENT_EXCEEDS_LIMIT",
            "MISSING_MANDATORY_DOCUMENT", "CLAIM_PAID_TWICE",
        }):
            if bad.get(typ):
                self.r.fail(f"EDGE_SEMANTICS_{typ}", f"{len(bad[typ])} labels do not match source rows", bad[typ])
            else:
                self.r.ok(f"EDGE_SEMANTICS_{typ}", "100% of labelled rows match exact anomaly condition")

        if unlabelled_early or unlabelled_cancelled:
            self.r.fail(
                "EDGE_ZERO_UNLABELLED_POLLUTION",
                f"Unlabelled anomalies found: early={len(unlabelled_early)}, cancelled={len(unlabelled_cancelled)}",
                unlabelled_early + unlabelled_cancelled,
            )
        else:
            self.r.ok("EDGE_ZERO_UNLABELLED_POLLUTION", "Zero unlabelled early-incident or cancelled-policy claims in baseline")

    # ── 10. Strict Feature Separation (Zero Target / Fraud Leakage) ───────────

    def check_feature_separation(self) -> None:
        path = os.path.join(self.data_dir, "features", "claims_input.csv")
        if not os.path.exists(path):
            self.r.fail("FEATURE_VIEW_EXISTS", "features/claims_input.csv is missing")
            return
        with open(path, newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
            columns = set(rows[0].keys()) if rows else set()
        forbidden = {
            "status",
            "denial_reason",
            "fraud_indicator",
            "assigned_adjuster_id",
            "updated_at",
            "issue_type",
            "expected_decision",
        }
        leaked = columns & forbidden
        if leaked:
            self.r.fail("FEATURE_OUTCOME_SEPARATION", f"Outcome/label fields leaked into features: {sorted(leaked)}")
        else:
            self.r.ok("FEATURE_OUTCOME_SEPARATION", "Zero outcome or fraud-label leakage in claims_input.csv")
        if len(rows) != len(self.data["claims"]):
            self.r.fail("FEATURE_VIEW_ROW_COUNT", f"{len(rows)} feature rows for {len(self.data['claims'])} claims")
        else:
            self.r.ok("FEATURE_VIEW_ROW_COUNT", f"{len(rows)} rows")

    # ── 11. Volume & Distribution Checks ──────────────────────────────────────

    def check_volumes(self) -> None:
        expected = {
            "customers": (100, 2000),
            "policies": (100, 3000),
            "claims": (50, 1500),
            "adjusters": (5, 100),
            "claim_adjuster_assignments": (50, 2500),
            "claim_coverage_allocations": (50, 2500),
        }
        for tbl, (lo, hi) in expected.items():
            n = len(self.data[tbl])
            rule = f"VOLUME_{tbl.upper()}"
            if lo <= n <= hi:
                self.r.ok(rule, f"{n} rows")
            else:
                self.r.fail(rule, f"{n} rows (expected {lo}-{hi})")

        type_dist = Counter(row["claim_type"] for row in self.data["claims"])
        if len(type_dist) == 5:
            self.r.ok("VOLUME_CLAIM_TYPE_DISTRIBUTION", f"5 types: {dict(sorted(type_dist.items()))}")
        else:
            self.r.fail("VOLUME_CLAIM_TYPE_DISTRIBUTION", f"Only {len(type_dist)} claim types: {dict(type_dist)}")

    # ── 12. Manifest SHA-256 Verification ─────────────────────────────────────

    def check_manifest_hashes(self) -> None:
        mpath = os.path.join(self.data_dir, "manifest.json")
        if not os.path.exists(mpath):
            self.r.fail("MANIFEST_EXISTS", "manifest.json missing")
            return
        with open(mpath, encoding="utf-8") as f:
            manifest = json.load(f)
        hashes = manifest.get("file_sha256", {})
        bad = []
        for rel_path, expected_sha in hashes.items():
            full_p = os.path.join(self.data_dir, rel_path)
            if not os.path.exists(full_p):
                bad.append(f"{rel_path} (missing)")
                continue
            with open(full_p, "rb") as stream:
                raw_bytes = stream.read()
            candidates = {
                hashlib.sha256(raw_bytes).hexdigest(),
                hashlib.sha256(raw_bytes.replace(b"\r\n", b"\n")).hexdigest(),
                hashlib.sha256(raw_bytes.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")).hexdigest(),
            }
            if expected_sha not in candidates:
                bad.append(f"{rel_path} (hash mismatch)")
        if bad:
            self.r.fail("MANIFEST_SHA256_INTEGRITY", f"{len(bad)} files failed SHA-256 verification", bad)
        else:
            self.r.ok("MANIFEST_SHA256_INTEGRITY", f"All {len(hashes)} exported artifacts match manifest SHA-256 checksums")

    def validate(self) -> ValidationResult:
        sections = [
            ("Primary-key uniqueness (14 tables)", self.check_pk_uniqueness),
            ("Foreign-key integrity (23 FKs)",     self.check_fk_integrity),
            ("Join & N:M bridge consistency",      self.check_join_consistency),
            ("Allowed enum values (21 columns)",   self.check_enums),
            ("Date ordering / lifecycle",          self.check_date_ordering),
            ("Exact arithmetic reconciliation",    self.check_arithmetic),
            ("Lifecycle state & gating rules",     self.check_lifecycle),
            ("Duplicate detection & GT match",     self.check_duplicates),
            ("Ground-truth bi-directional check",  self.check_ground_truth),
            ("Zero-leakage feature separation",    self.check_feature_separation),
            ("Volume & product distribution",      self.check_volumes),
            ("Manifest SHA-256 integrity",         self.check_manifest_hashes),
        ]
        for title, fn in sections:
            print(f"\n  [{title}]")
            before = len(self.r.checks)
            fn()
            for chk in self.r.checks[before:]:
                if chk["status"] == "FAIL":
                    print(f"    FAIL  {chk['rule']}: {chk['detail']}")
                    if self.verbose and "sample_rows" in chk:
                        for s in chk["sample_rows"][:5]:
                            print(f"          -> {s}")
                elif chk["status"] == "WARN":
                    print(f"    WARN  {chk['rule']}: {chk['detail']}")
        return self.r


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate insurance claims data (v2.0)")
    ap.add_argument("--data-dir", default="output", help="Data directory")
    ap.add_argument("--verbose", action="store_true", help="Show failing rows")
    ap.add_argument("--json-report", help="Write JSON report to file")
    args = ap.parse_args()

    print("=== Insurance Claims Data Validator (v2.0) ===")
    print(f"    Data dir: {os.path.abspath(args.data_dir)}\n")

    v = DataValidator(args.data_dir, verbose=args.verbose)
    result = v.validate()

    print(f"\n{'='*54}")
    print(result.summary_line())
    print(f"  Overall: {'PASS' if result.passed else 'FAIL'}")
    print(f"{'='*54}")

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
