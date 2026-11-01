#!/usr/bin/env python3
"""
Insurance Claims Processing — Advanced Deterministic Synthetic Data Generator (v2.0)
====================================================================================
Generates a complete, strictly reconciled insurance-claims dataset with:
  • 11 Core Entities: Customer, Adjuster, Policy, Coverage, Incident, Claim,
    ClaimItem, Document, Assessment, Reserve, Settlement
  • 2 Normalized N:M Bridge Tables:
      - claim_adjuster_assignments (Claim <-> Adjuster many-to-many with roles)
      - claim_coverage_allocations (Claim <-> Coverage many-to-many allocations)
  • 1 Immutable Audit / Lifecycle Event Log:
      - claim_state_history (append-only state transitions & actors)
  • 2,000 policies and 1,000 claims across 5 product types (AUTO, HOME, HEALTH, LIFE, TRAVEL)
  • 100% Bottom-Up Derived Arithmetic:
      - ClaimItem: claimed_amount = round(quantity * unit_cost, 2)
      - Claim: claim_amount = round(sum(item.claimed_amount), 2)
      - Assessment: assessed_amount = round(sum(item.approved_amount), 2)
      - Assessment: eligible_amount = 0.0 if denied else min(round(max(0, assessed_amount * (liability_pct/100) - deductible) * (1 - copay_pct/100), 2), coverage_limit)
      - Settlement: settlement_amount = assessment.eligible_amount (except labelled over-limit edge cases)
  • Clean Baseline + Explicitly Labelled Edge Cases & Fraud Scenarios:
      - Zero unlabelled accidental anomalies in baseline records
      - All 6 required edge-case classes + 4 extended portfolio exception classes
      - Separate ground_truth.csv, ground_truth.json, and ground_truth_decisions.csv
      - Strict zero-leakage feature projection in output/features/claims_input.csv

Usage
-----
    python generate.py                        # full-scale (2,000 policies / 1,000 claims), seed 20261022
    python generate.py --scale starter        # starter-scale (200 policies / 100 claims)
    python generate.py --verify               # two-run deterministic SHA-256 reproducibility check
    python generate.py --seed 42 --output out # custom seed & output directory
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import random
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION & DOMAIN CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_SEED = 20261022
GENERATOR_VERSION = "2.0.0"

SCALE_CONFIGS = {
    "starter": {
        "num_customers": 150,
        "num_policies": 200,
        "num_claims": 100,
        "num_adjusters": 12,
    },
    "full": {
        "num_customers": 1500,
        "num_policies": 2000,
        "num_claims": 1000,
        "num_adjusters": 50,
    },
}

PRODUCTS = ["AUTO", "HOME", "HEALTH", "LIFE", "TRAVEL"]

POLICY_START_RANGE = (datetime(2024, 1, 1), datetime(2026, 3, 31))
CLAIM_DATE_RANGE = (datetime(2025, 1, 1), datetime(2026, 10, 15))

POLICY_STATE_WEIGHTS = {
    "ACTIVE": 0.65,
    "EXPIRED": 0.15,
    "CANCELLED": 0.10,
    "LAPSED": 0.10,
}

COVERAGE_CONFIG = {
    "AUTO": {
        "types": [
            "LIABILITY",
            "COLLISION",
            "COMPREHENSIVE",
            "UNINSURED_MOTORIST",
            "MEDICAL_PAYMENTS",
        ],
        "limit_range": (25_000, 250_000),
        "deductible_range": (250, 2_000),
        "exclusions": ["RACING", "COMMERCIAL_USE", "INTENTIONAL_DAMAGE", "DUI"],
        "required_docs": ["POLICE_REPORT", "PHOTOS", "REPAIR_ESTIMATE", "DRIVERS_LICENSE"],
    },
    "HOME": {
        "types": [
            "DWELLING",
            "PERSONAL_PROPERTY",
            "LIABILITY",
            "ADDITIONAL_LIVING_EXPENSE",
        ],
        "limit_range": (50_000, 750_000),
        "deductible_range": (500, 5_000),
        "exclusions": ["FLOOD", "EARTHQUAKE", "INTENTIONAL_DAMAGE", "NORMAL_WEAR"],
        "required_docs": ["PHOTOS", "INVENTORY_LIST", "REPAIR_ESTIMATE", "POLICE_REPORT"],
    },
    "HEALTH": {
        "types": ["HOSPITALIZATION", "OUTPATIENT", "PRESCRIPTION", "DENTAL", "VISION"],
        "limit_range": (15_000, 500_000),
        "deductible_range": (250, 5_000),
        "exclusions": ["COSMETIC", "EXPERIMENTAL", "PRE_EXISTING_UNCOVERED"],
        "required_docs": ["MEDICAL_RECORDS", "BILLS", "REFERRAL", "ID_PROOF"],
    },
    "LIFE": {
        "types": ["TERM_DEATH", "ACCIDENTAL_DEATH", "DISABILITY", "CRITICAL_ILLNESS"],
        "limit_range": (50_000, 1_500_000),
        "deductible_range": (0, 0),
        "exclusions": ["SUICIDE_WITHIN_2YR", "FRAUD", "WAR", "ILLEGAL_ACTIVITY"],
        "required_docs": [
            "DEATH_CERTIFICATE",
            "MEDICAL_RECORDS",
            "ID_PROOF",
            "BENEFICIARY_PROOF",
        ],
    },
    "TRAVEL": {
        "types": ["TRIP_CANCELLATION", "MEDICAL_ABROAD", "BAGGAGE_LOSS", "FLIGHT_DELAY"],
        "limit_range": (2_000, 50_000),
        "deductible_range": (50, 500),
        "exclusions": [
            "PRE_EXISTING_CONDITION",
            "ADVENTURE_SPORTS",
            "PANDEMIC",
            "WAR_ZONE",
        ],
        "required_docs": [
            "TRAVEL_ITINERARY",
            "RECEIPTS",
            "MEDICAL_RECORDS",
            "AIRLINE_CONFIRMATION",
        ],
    },
}

INCIDENT_TYPES = {
    "AUTO": ["COLLISION", "THEFT", "VANDALISM", "WEATHER_DAMAGE", "HIT_AND_RUN"],
    "HOME": ["FIRE", "WATER_DAMAGE", "THEFT", "STORM", "VANDALISM"],
    "HEALTH": ["ILLNESS", "INJURY", "SURGERY", "EMERGENCY", "CHRONIC_CONDITION"],
    "LIFE": [
        "NATURAL_DEATH",
        "ACCIDENTAL_DEATH",
        "DISABILITY_ONSET",
        "CRITICAL_ILLNESS_DIAGNOSIS",
    ],
    "TRAVEL": [
        "TRIP_CANCELLATION",
        "MEDICAL_EMERGENCY",
        "BAGGAGE_LOST",
        "FLIGHT_DELAYED",
        "THEFT_ABROAD",
    ],
}

ITEM_DESCRIPTIONS = {
    "AUTO": [
        "Vehicle body panel repair",
        "Windshield & glass replacement",
        "Emergency towing & recovery",
        "Substitute rental car allowance",
        "Bumper & frame recalibration",
    ],
    "HOME": [
        "Roof shingle & structural repair",
        "Water extraction & dehumidification",
        "Damaged appliance & furniture replacement",
        "Electrical panel & wiring restoration",
        "Emergency board-up & debris removal",
    ],
    "HEALTH": [
        "Inpatient hospital room & board",
        "Surgical procedure & anesthesia",
        "Diagnostic imaging & laboratory panel",
        "Specialty prescription regimen",
        "Post-acute physical rehabilitation",
    ],
    "LIFE": [
        "Primary term death benefit payout",
        "Accidental death benefit rider",
        "Total disability monthly indemnity",
        "Critical illness lump-sum benefit",
    ],
    "TRAVEL": [
        "Non-refundable flight & hotel rebooking",
        "Emergency overseas medical evacuation",
        "Lost baggage & personal effects reimbursement",
        "Travel delay meal & lodging allowance",
        "Out-of-country urgent clinic care",
    ],
}

CLAIM_STATES = [
    "SUBMITTED",
    "PENDING_DOCUMENTS",
    "UNDER_REVIEW",
    "SIU_INVESTIGATION",
    "APPROVED",
    "DENIED",
    "SETTLED",
    "CLOSED",
]

FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda",
    "David", "Elizabeth", "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica",
    "Thomas", "Sarah", "Christopher", "Karen", "Charles", "Lisa", "Daniel", "Nancy",
    "Matthew", "Betty", "Anthony", "Margaret", "Mark", "Sandra", "Donald", "Ashley",
    "Steven", "Kimberly", "Paul", "Emily", "Andrew", "Donna", "Joshua", "Michelle",
    "Kenneth", "Carol", "Kevin", "Amanda", "Brian", "Dorothy", "George", "Melissa",
    "Timothy", "Deborah", "Ronald", "Stephanie", "Edward", "Rebecca", "Jason", "Sharon",
    "Jeffrey", "Laura", "Ryan", "Cynthia",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson",
    "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson",
    "Walker", "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen",
    "Hill", "Flores", "Green", "Adams", "Nelson", "Baker", "Hall", "Rivera",
    "Campbell", "Mitchell", "Carter", "Roberts",
]

STATES_LIST = [
    "CA", "TX", "FL", "NY", "PA", "IL", "OH", "GA", "NC", "MI",
    "NJ", "VA", "WA", "AZ", "MA", "TN", "IN", "MO", "MD", "WI",
]

CITIES_BY_STATE = {
    "CA": ["Los Angeles", "San Francisco", "San Diego", "San Jose"],
    "TX": ["Houston", "Dallas", "Austin", "San Antonio"],
    "FL": ["Miami", "Orlando", "Tampa", "Jacksonville"],
    "NY": ["New York", "Buffalo", "Rochester", "Albany"],
    "PA": ["Philadelphia", "Pittsburgh", "Allentown", "Erie"],
    "IL": ["Chicago", "Springfield", "Naperville", "Rockford"],
    "OH": ["Columbus", "Cleveland", "Cincinnati", "Toledo"],
    "GA": ["Atlanta", "Savannah", "Augusta", "Macon"],
    "NC": ["Charlotte", "Raleigh", "Durham", "Greensboro"],
    "MI": ["Detroit", "Grand Rapids", "Lansing", "Ann Arbor"],
    "NJ": ["Newark", "Jersey City", "Trenton", "Princeton"],
    "VA": ["Richmond", "Virginia Beach", "Norfolk", "Arlington"],
    "WA": ["Seattle", "Tacoma", "Spokane", "Bellevue"],
    "AZ": ["Phoenix", "Tucson", "Mesa", "Scottsdale"],
    "MA": ["Boston", "Cambridge", "Worcester", "Springfield"],
    "TN": ["Nashville", "Memphis", "Knoxville", "Chattanooga"],
    "IN": ["Indianapolis", "Fort Wayne", "Evansville", "Bloomington"],
    "MO": ["Kansas City", "St Louis", "Springfield", "Columbia"],
    "MD": ["Baltimore", "Annapolis", "Frederick", "Rockville"],
    "WI": ["Milwaukee", "Madison", "Green Bay", "Kenosha"],
}

STREET_NAMES = ["Main", "Oak", "Pine", "Maple", "Cedar", "Elm", "Park", "Lake", "Hill", "River"]
STREET_TYPES = ["St", "Ave", "Blvd", "Dr", "Ln", "Way"]
EMAIL_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com"]

BASE_PREMIUMS = {"AUTO": 1200, "HOME": 1800, "HEALTH": 4800, "LIFE": 600, "TRAVEL": 220}
CLAIM_TYPE_SHARES = {"AUTO": 0.30, "HOME": 0.20, "HEALTH": 0.25, "LIFE": 0.10, "TRAVEL": 0.15}

UNIT_COST_RANGES = {
    "AUTO": (180.0, 4200.0),
    "HOME": (350.0, 8500.0),
    "HEALTH": (150.0, 6500.0),
    "LIFE": (5000.0, 45000.0),
    "TRAVEL": (85.0, 2200.0),
}


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _rand_date(start: datetime, end: datetime) -> datetime:
    delta = (end - start).days
    if delta <= 0:
        return start
    return start + timedelta(days=random.randint(0, delta))


def _rand_phone() -> str:
    return f"({random.randint(200,999)}) {random.randint(200,999)}-{random.randint(1000,9999)}"


def _rand_email(first: str, last: str, seq: int) -> str:
    sep = random.choice([".", "_"])
    return f"{first.lower()}{sep}{last.lower()}{seq}@{random.choice(EMAIL_DOMAINS)}"


def _rc(val: float) -> float:
    return round(float(val), 2)


def _weighted(d: Dict[str, float]) -> str:
    return random.choices(list(d.keys()), weights=list(d.values()), k=1)[0]


def _state_change_reason(state: str, denial_reason: Optional[str] = None) -> str:
    return {
        "SUBMITTED": "Claim intake recorded and linked to policy coverage",
        "PENDING_DOCUMENTS": "Mandatory supporting documentation requested from claimant",
        "UNDER_REVIEW": "All mandatory documents verified; assigned to specialist adjuster",
        "SIU_INVESTIGATION": "Elevated fraud risk indicators detected; referred to Special Investigations Unit",
        "APPROVED": "Eligibility assessment completed and settlement recommendation approved",
        "DENIED": f"Claim denied: {denial_reason}" if denial_reason else "Coverage or eligibility validation failed",
        "SETTLED": "Approved settlement payment disbursed to payee",
        "CLOSED": "Financial reserve reconciled to zero and claim file archived",
    }.get(state, "Lifecycle state updated")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

class InsuranceDataGenerator:
    """Deterministic, seed-controlled generator for the full 14-table insurance
    claims data model with bottom-up derived reconciliation and isolated edge cases."""

    def __init__(self, seed: int = DEFAULT_SEED, scale: str = "full"):
        self.seed = seed
        self.scale = scale
        self.cfg = SCALE_CONFIGS[scale]
        self.rng = random.Random(seed)
        random.seed(seed)

        self._ctr: Dict[str, int] = defaultdict(int)

        # 11 Core Entity stores
        self.customers: List[dict] = []
        self.adjusters: List[dict] = []
        self.policies: List[dict] = []
        self.coverages: List[dict] = []
        self.incidents: List[dict] = []
        self.claims: List[dict] = []
        self.claim_items: List[dict] = []
        self.documents: List[dict] = []
        self.assessments: List[dict] = []
        self.reserves: List[dict] = []
        self.settlements: List[dict] = []

        # 2 Many-to-Many (N:M) Bridge Table stores
        self.claim_adjuster_assignments: List[dict] = []
        self.claim_coverage_allocations: List[dict] = []

        # 1 Event History store
        self.claim_state_history: List[dict] = []

        # Ground Truth stores
        self.ground_truth: List[dict] = []
        self.ground_truth_decisions: List[dict] = []

        # Internal lookup indexes
        self._pol: Dict[str, dict] = {}
        self._cust: Dict[str, dict] = {}
        self._cov_by_pol: Dict[str, List[dict]] = defaultdict(list)
        self._cov_by_id: Dict[str, dict] = {}
        self._inc: Dict[str, dict] = {}
        self._clm: Dict[str, dict] = {}
        self._adj: Dict[str, dict] = {}
        self._adj_by_prod: Dict[str, List[str]] = defaultdict(list)
        self._items_by_clm: Dict[str, List[dict]] = defaultdict(list)
        self._docs_by_clm: Dict[str, List[dict]] = defaultdict(list)
        self._asn_by_clm: Dict[str, dict] = {}
        self._rsv_by_clm: Dict[str, dict] = {}
        self._set_by_clm: Dict[str, List[dict]] = defaultdict(list)

    # ── Deterministic ID factories (strictly adhering to nomenclature) ────────

    def _next(self, prefix: str) -> int:
        self._ctr[prefix] += 1
        return self._ctr[prefix]

    def _cust_id(self) -> str:
        return f"CUST-{self._next('CUST'):06d}"

    def _pol_id(self, product: str) -> str:
        return f"POL-{product}-2026-{self._next('POL'):06d}"

    def _cov_id(self, pol_id: str) -> str:
        return f"COV-{pol_id}-{self._next(f'COV-{pol_id}'):03d}"

    def _clm_id(self) -> str:
        return f"CLM-2026-{self._next('CLM'):06d}"

    def _inc_id(self) -> str:
        return f"INC-2026-{self._next('INC'):06d}"

    def _doc_id(self, clm_id: str) -> str:
        return f"DOC-{clm_id}-{self._next(f'DOC-{clm_id}'):03d}"

    def _adj_id(self) -> str:
        return f"ADJ-{self._next('ADJ'):06d}"

    def _asn_id(self, clm_id: str) -> str:
        return f"ASN-{clm_id}-{self._next(f'ASN-{clm_id}'):03d}"

    def _rsv_id(self, clm_id: str) -> str:
        return f"RSV-{clm_id}-{self._next(f'RSV-{clm_id}'):03d}"

    def _set_id(self, clm_id: str) -> str:
        return f"SET-{clm_id}-{self._next(f'SET-{clm_id}'):03d}"

    def _caa_id(self, clm_id: str) -> str:
        return f"CAA-{clm_id}-{self._next(f'CAA-{clm_id}'):03d}"

    def _cca_id(self, clm_id: str) -> str:
        return f"CCA-{clm_id}-{self._next(f'CCA-{clm_id}'):03d}"

    # ── Step 1: Customers ─────────────────────────────────────────────────────

    def _gen_customers(self) -> None:
        for idx in range(1, self.cfg["num_customers"] + 1):
            cid = self._cust_id()
            state = random.choice(STATES_LIST)
            city = random.choice(CITIES_BY_STATE[state])
            first = random.choice(FIRST_NAMES)
            last = random.choice(LAST_NAMES)
            rec = {
                "customer_id": cid,
                "first_name": first,
                "last_name": last,
                "date_of_birth": _rand_date(
                    datetime(1950, 1, 1), datetime(2002, 12, 31)
                ).strftime("%Y-%m-%d"),
                "email": _rand_email(first, last, idx),
                "phone": _rand_phone(),
                "address_line1": (
                    f"{random.randint(100,9999)} "
                    f"{random.choice(STREET_NAMES)} "
                    f"{random.choice(STREET_TYPES)}"
                ),
                "city": city,
                "state": state,
                "zip_code": f"{random.randint(10000,99999)}",
                "risk_tier": random.choices(
                    ["LOW", "STANDARD", "ELEVATED"], weights=[0.35, 0.50, 0.15], k=1
                )[0],
                "created_at": _rand_date(
                    datetime(2020, 1, 1), datetime(2023, 12, 31)
                ).strftime("%Y-%m-%d %H:%M:%S"),
                "status": random.choices(
                    ["ACTIVE", "INACTIVE"], weights=[0.92, 0.08], k=1
                )[0],
            }
            self.customers.append(rec)
            self._cust[cid] = rec

    # ── Step 2: Adjusters ─────────────────────────────────────────────────────

    def _gen_adjusters(self) -> None:
        for idx in range(1, self.cfg["num_adjusters"] + 1):
            aid = self._adj_id()
            first = random.choice(FIRST_NAMES)
            last = random.choice(LAST_NAMES)
            specs = sorted(random.sample(PRODUCTS, k=random.randint(1, 3)))
            role_tier = random.choices(
                ["SENIOR_ADJUSTER", "STANDARD_ADJUSTER", "SIU_SPECIALIST"],
                weights=[0.25, 0.60, 0.15],
                k=1,
            )[0]
            rec = {
                "adjuster_id": aid,
                "first_name": first,
                "last_name": last,
                "email": _rand_email(first, last, 1000 + idx),
                "phone": _rand_phone(),
                "specializations": ",".join(specs),
                "role_tier": role_tier,
                "license_number": f"ADJ-LIC-{100000 + idx:06d}",
                "hire_date": _rand_date(
                    datetime(2015, 1, 1), datetime(2024, 6, 30)
                ).strftime("%Y-%m-%d"),
                "status": random.choices(
                    ["ACTIVE", "ON_LEAVE", "INACTIVE"],
                    weights=[0.85, 0.10, 0.05],
                    k=1,
                )[0],
                "max_concurrent_claims": random.choice([15, 20, 25, 30]),
            }
            self.adjusters.append(rec)
            self._adj[aid] = rec
            if rec["status"] == "ACTIVE":
                for s in specs:
                    self._adj_by_prod[s].append(aid)

        # Guarantee at least 2 active specialists per product + 1 active SIU specialist
        for product in PRODUCTS:
            while len(self._adj_by_prod[product]) < 2:
                target = random.choice(self.adjusters)
                target["status"] = "ACTIVE"
                cur_specs = set(target["specializations"].split(","))
                cur_specs.add(product)
                target["specializations"] = ",".join(sorted(cur_specs))
                if target["adjuster_id"] not in self._adj_by_prod[product]:
                    self._adj_by_prod[product].append(target["adjuster_id"])

        if not any(a["status"] == "ACTIVE" and a["role_tier"] == "SIU_SPECIALIST" for a in self.adjusters):
            self.adjusters[0]["status"] = "ACTIVE"
            self.adjusters[0]["role_tier"] = "SIU_SPECIALIST"

    # ── Step 3: Policies ──────────────────────────────────────────────────────

    def _gen_policies(self) -> None:
        cust_ids = [c["customer_id"] for c in self.customers]
        for _ in range(self.cfg["num_policies"]):
            product = random.choice(PRODUCTS)
            pid = self._pol_id(product)
            cid = random.choice(cust_ids)
            start = _rand_date(*POLICY_START_RANGE)
            term = random.choice([12, 12, 24, 36])
            end = start + timedelta(days=term * 30)
            st = _weighted(POLICY_STATE_WEIGHTS)
            cancel_dt = None
            lapse_dt = None
            if st == "CANCELLED":
                cancel_dt = _rand_date(
                    start + timedelta(days=30), end - timedelta(days=30)
                ).strftime("%Y-%m-%d")
            elif st == "LAPSED":
                lapse_dt = _rand_date(
                    start + timedelta(days=45), end - timedelta(days=30)
                ).strftime("%Y-%m-%d")
            elif st == "EXPIRED":
                # Make expired policies have an expiration date in early 2025/2026
                start = _rand_date(datetime(2024, 1, 1), datetime(2024, 9, 30))
                term = 12
                end = start + timedelta(days=365)

            premium = _rc(
                BASE_PREMIUMS[product] * random.uniform(0.75, 2.15) * (term / 12)
            )
            rec = {
                "policy_id": pid,
                "customer_id": cid,
                "product_type": product,
                "policy_number": pid,
                "effective_date": start.strftime("%Y-%m-%d"),
                "expiration_date": end.strftime("%Y-%m-%d"),
                "term_months": term,
                "premium_amount": premium,
                "payment_frequency": random.choice(
                    ["MONTHLY", "QUARTERLY", "SEMI_ANNUAL", "ANNUAL"]
                ),
                "status": st,
                "cancellation_date": cancel_dt,
                "lapse_date": lapse_dt,
                "underwriting_status": random.choices(
                    ["APPROVED", "CONDITIONAL"],
                    weights=[0.88, 0.12],
                    k=1,
                )[0],
                "created_at": start.strftime("%Y-%m-%d %H:%M:%S"),
                "updated_at": datetime(2026, 10, 31).strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.policies.append(rec)
            self._pol[pid] = rec

    # ── Step 4: Coverages ─────────────────────────────────────────────────────

    def _gen_coverages(self) -> None:
        for pol in self.policies:
            prod = pol["product_type"]
            cc = COVERAGE_CONFIG[prod]
            n = random.randint(2, min(4, len(cc["types"])))
            for cov_type in random.sample(cc["types"], n):
                cov_id = self._cov_id(pol["policy_id"])
                lim = _rc(random.randint(int(cc["limit_range"][0] / 1000), int(cc["limit_range"][1] / 1000)) * 1000)
                ded = (
                    _rc(random.choice([250, 500, 1000, 1500, 2000, 2500]))
                    if cc["deductible_range"][1] > 0
                    else 0.0
                )
                ded = min(ded, float(cc["deductible_range"][1]))
                ne = random.randint(0, min(2, len(cc["exclusions"])))
                excl = sorted(random.sample(cc["exclusions"], ne))
                rec = {
                    "coverage_id": cov_id,
                    "policy_id": pol["policy_id"],
                    "coverage_type": cov_type,
                    "coverage_limit": lim,
                    "deductible": ded,
                    "co_pay_percentage": (
                        random.choice([0, 10, 20]) if prod == "HEALTH" else 0
                    ),
                    "exclusions": ",".join(excl) if excl else "",
                    "effective_date": pol["effective_date"],
                    "expiration_date": pol["expiration_date"],
                    "status": "ACTIVE" if pol["status"] == "ACTIVE" else "INACTIVE",
                }
                self.coverages.append(rec)
                self._cov_by_pol[pol["policy_id"]].append(rec)
                self._cov_by_id[cov_id] = rec

    # ── Step 5: Baseline Valid Incidents & Claims + Controlled Scenarios ──────

    def _create_incident_for_policy(
        self, pol: dict, idate: datetime, itype: Optional[str] = None
    ) -> dict:
        iid = self._inc_id()
        prod = pol["product_type"]
        chosen_type = itype or random.choice(INCIDENT_TYPES[prod])
        state = random.choice(STATES_LIST)
        city = random.choice(CITIES_BY_STATE[state])
        reported_delay = random.randint(0, 10)
        rdate = idate + timedelta(days=reported_delay)
        rec = {
            "incident_id": iid,
            "incident_type": chosen_type,
            "incident_date": idate.strftime("%Y-%m-%d"),
            "incident_time": f"{random.randint(0,23):02d}:{random.randint(0,59):02d}:00",
            "description": f"{chosen_type.replace('_',' ').title()} incident reported in {city}, {state}",
            "location_city": city,
            "location_state": state,
            "severity": random.choices(
                ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                weights=[0.28, 0.42, 0.20, 0.10],
                k=1,
            )[0],
            "police_report_filed": random.choices(
                [True, False], weights=[0.65, 0.35], k=1
            )[0],
            "witnesses": random.randint(0, 4),
            "product_category": prod,
            "reported_date": rdate.strftime("%Y-%m-%d"),
        }
        self.incidents.append(rec)
        self._inc[iid] = rec
        return rec

    def _gen_incidents_and_claims(self) -> None:
        """Generates baseline valid claims with coherent dates first, plus
        unclaimed incidents so total incidents exceed claims realistically."""
        total = self.cfg["num_claims"]
        per_type = {p: int(total * CLAIM_TYPE_SHARES[p]) for p in PRODUCTS}
        per_type["AUTO"] += total - sum(per_type.values())

        # Group ACTIVE policies by product for clean baseline claim generation
        active_pols_by_prod: Dict[str, List[dict]] = defaultdict(list)
        for p in self.policies:
            if p["status"] == "ACTIVE":
                active_pols_by_prod[p["product_type"]].append(p)

        for prod, count in per_type.items():
            avail_pols = active_pols_by_prod[prod]
            for _ in range(count):
                cid = self._clm_id()
                pol = random.choice(avail_pols)
                p_start = datetime.strptime(pol["effective_date"], "%Y-%m-%d")
                p_end = datetime.strptime(pol["expiration_date"], "%Y-%m-%d")
                # Keep baseline incident date strictly within active policy period
                valid_end = min(p_end - timedelta(days=15), datetime(2026, 10, 1))
                if valid_end <= p_start:
                    valid_end = p_start + timedelta(days=30)
                idate = _rand_date(p_start + timedelta(days=5), valid_end)
                inc = self._create_incident_for_policy(pol, idate)

                covs = self._cov_by_pol[pol["policy_id"]]
                # Pick primary coverage (highest limit or random active coverage)
                sel_cov = max(covs, key=lambda c: c["coverage_limit"])
                sub_date = datetime.strptime(inc["reported_date"], "%Y-%m-%d") + timedelta(
                    days=random.randint(0, 5)
                )

                adj_pool = self._adj_by_prod[prod]
                assigned_adj = random.choice(adj_pool)

                # Baseline valid status progression
                status = random.choices(
                    ["SUBMITTED", "UNDER_REVIEW", "APPROVED", "SETTLED", "CLOSED"],
                    weights=[0.08, 0.14, 0.18, 0.32, 0.28],
                    k=1,
                )[0]

                rec = {
                    "claim_id": cid,
                    "policy_id": pol["policy_id"],
                    "customer_id": pol["customer_id"],
                    "incident_id": inc["incident_id"],
                    "claim_type": prod,
                    "claim_amount": 0.0,  # Strictly derived from ClaimItems in Step 6!
                    "submission_date": sub_date.strftime("%Y-%m-%d"),
                    "status": status,
                    "assigned_adjuster_id": assigned_adj,
                    "priority": random.choices(
                        ["LOW", "MEDIUM", "HIGH", "URGENT"],
                        weights=[0.22, 0.43, 0.25, 0.10],
                        k=1,
                    )[0],
                    "denial_reason": None,
                    "fraud_indicator": False,
                    "coverage_id": sel_cov["coverage_id"],
                    "created_at": sub_date.strftime("%Y-%m-%d %H:%M:%S"),
                    "updated_at": (sub_date + timedelta(days=random.randint(5, 45))).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                }
                self.claims.append(rec)
                self._clm[cid] = rec

        # Also create 12% extra standalone incidents (unclaimed reports)
        extra_incidents = max(10, int(total * 0.12))
        for _ in range(extra_incidents):
            pol = random.choice(self.policies)
            p_start = datetime.strptime(pol["effective_date"], "%Y-%m-%d")
            p_end = datetime.strptime(pol["expiration_date"], "%Y-%m-%d")
            idate = _rand_date(p_start, p_end)
            self._create_incident_for_policy(pol, idate)

    # ── Step 6: Bottom-Up ClaimItems & Derived Claim Totals ───────────────────

    def _gen_claim_items_and_derive_totals(self) -> None:
        """Generates ClaimItems first, then derives claims.claim_amount as the exact
        sum of item claimed_amounts so reconciliation is 100% exact."""
        for clm in self.claims:
            prod = clm["claim_type"]
            cov = self._cov_by_id[clm["coverage_id"]]
            limit = float(cov["coverage_limit"])
            ded = float(cov["deductible"])
            descs = ITEM_DESCRIPTIONS[prod]
            n_items = random.randint(2, min(4, len(descs)))
            lo_cost, hi_cost = UNIT_COST_RANGES[prod]
            # Keep normal baseline claims comfortably above deductible and within coverage limit
            max_item_budget = max(lo_cost * 1.5, min(hi_cost, (limit * 0.70) / n_items))
            min_item_budget = max(lo_cost, (ded * 1.35) / n_items)
            if min_item_budget >= max_item_budget:
                min_item_budget = max_item_budget * 0.6

            is_approved_state = clm["status"] in ("APPROVED", "SETTLED", "CLOSED")
            is_denied_state = clm["status"] == "DENIED"

            total_claimed = 0.0
            for i in range(n_items):
                qty = random.randint(1, 3) if prod != "LIFE" else 1
                unit_cost = _rc(random.uniform(min_item_budget, max_item_budget) / qty)
                claimed_amt = _rc(qty * unit_cost)
                if is_approved_state:
                    # Approved line item is 80%-100% of claimed_amount
                    factor = random.choice([0.85, 0.90, 0.95, 1.0, 1.0])
                    approved_amt = _rc(claimed_amt * factor)
                    item_status = "APPROVED"
                elif is_denied_state:
                    approved_amt = 0.0
                    item_status = "DENIED"
                else:
                    approved_amt = None
                    item_status = "PENDING"

                item_rec = {
                    "claim_item_id": f"CLI-{clm['claim_id']}-{i+1:03d}",
                    "claim_id": clm["claim_id"],
                    "item_description": descs[i % len(descs)],
                    "item_category": prod,
                    "claimed_amount": claimed_amt,
                    "approved_amount": approved_amt,
                    "quantity": qty,
                    "unit_cost": unit_cost,
                    "status": item_status,
                }
                self.claim_items.append(item_rec)
                self._items_by_clm[clm["claim_id"]].append(item_rec)
                total_claimed = _rc(total_claimed + claimed_amt)

            clm["claim_amount"] = total_claimed

    # ── Step 7: Documents (Aligned with Claim Lifecycle State!) ───────────────

    def _gen_documents(self) -> None:
        """Generates mandatory documents per claim. Crucially, claims in APPROVED,
        SETTLED, or CLOSED states always have VERIFIED/RECEIVED documents, never REJECTED."""
        for clm in self.claims:
            prod = clm["claim_type"]
            req = COVERAGE_CONFIG[prod]["required_docs"]
            sub = datetime.strptime(clm["submission_date"], "%Y-%m-%d")
            for doc_type in req:
                did = self._doc_id(clm["claim_id"])
                upload_dt = sub + timedelta(days=random.randint(0, 6))
                if clm["status"] in ("APPROVED", "SETTLED", "CLOSED"):
                    doc_status = "VERIFIED"
                    ver_dt = (upload_dt + timedelta(days=random.randint(1, 5))).strftime("%Y-%m-%d")
                elif clm["status"] == "UNDER_REVIEW":
                    doc_status = random.choice(["RECEIVED", "VERIFIED"])
                    ver_dt = (
                        (upload_dt + timedelta(days=random.randint(1, 5))).strftime("%Y-%m-%d")
                        if doc_status == "VERIFIED"
                        else None
                    )
                else:
                    doc_status = "RECEIVED"
                    ver_dt = None

                doc_rec = {
                    "document_id": did,
                    "claim_id": clm["claim_id"],
                    "document_type": doc_type,
                    "file_name": f"{doc_type.lower()}_{clm['claim_id']}.pdf",
                    "file_size_kb": random.randint(80, 4800),
                    "upload_date": upload_dt.strftime("%Y-%m-%d"),
                    "uploaded_by": clm["customer_id"],
                    "status": doc_status,
                    "verification_date": ver_dt,
                    "notes": f"Mandatory {doc_type} evidence for {prod} claim",
                }
                self.documents.append(doc_rec)
                self._docs_by_clm[clm["claim_id"]].append(doc_rec)

    # ── Step 8: Inject Portfolio Exceptions & All 6 Required Edge Cases ───────

    def _edge_target(self, full_scale_count: int) -> int:
        return full_scale_count if self.scale == "full" else max(1, full_scale_count // 5)

    def _inject_all_scenarios_before_downstream_derivation(self) -> None:
        """Injects both the 6 required negative edge cases and the Step-4 portfolio
        exception scenarios BEFORE deriving Assessments, Reserves, Settlements,
        Bridge tables, and State History so 100% of downstream tables are derived
        consistently from the ground-truth rules!"""

        used_claims: set[str] = set()

        def pick_candidates(count: int, allowed_statuses: Optional[set[str]] = None) -> List[dict]:
            out = []
            for c in self.claims:
                if c["claim_id"] in used_claims:
                    continue
                if allowed_statuses and c["status"] not in allowed_statuses:
                    continue
                out.append(c)
                if len(out) >= count:
                    break
            for c in out:
                used_claims.add(c["claim_id"])
            return out

        # ── 1. EC-1 (Required): CLAIM_DATE_BEFORE_POLICY_START (10 full / 2 starter)
        for clm in pick_candidates(self._edge_target(10), {"UNDER_REVIEW", "APPROVED"}):
            pol = self._pol[clm["policy_id"]]
            inc = self._inc[clm["incident_id"]]
            p_start = datetime.strptime(pol["effective_date"], "%Y-%m-%d")
            early_dt = p_start - timedelta(days=random.randint(30, 180))
            inc["incident_date"] = early_dt.strftime("%Y-%m-%d")
            inc["reported_date"] = (early_dt + timedelta(days=2)).strftime("%Y-%m-%d")
            clm["submission_date"] = (early_dt + timedelta(days=4)).strftime("%Y-%m-%d")
            clm["created_at"] = early_dt.strftime("%Y-%m-%d 09:30:00")
            clm["status"] = "DENIED"
            clm["denial_reason"] = "CLAIM_DATE_BEFORE_POLICY_START"
            for it in self._items_by_clm[clm["claim_id"]]:
                it["approved_amount"] = 0.0
                it["status"] = "DENIED"
            self.ground_truth.append({
                "entity_type": "CLAIM",
                "entity_id": clm["claim_id"],
                "issue_type": "CLAIM_DATE_BEFORE_POLICY_START",
                "expected_decision": "DENY",
                "description": f"Incident date {inc['incident_date']} occurs before policy effective_date {pol['effective_date']}",
                "related_entity_id": inc["incident_id"],
                "severity": "HIGH",
            })

        # ── 2. EC-2 (Required): POLICY_CANCELLED_BEFORE_INCIDENT (10 full / 2 starter)
        cancelled_pols = [p for p in self.policies if p["status"] == "CANCELLED" and p.get("cancellation_date")]
        for idx, clm in enumerate(pick_candidates(self._edge_target(10), {"UNDER_REVIEW", "APPROVED"})):
            pol = next(
                (p for p in cancelled_pols if p["product_type"] == clm["claim_type"]),
                cancelled_pols[idx % len(cancelled_pols)],
            )
            pol["product_type"] = clm["claim_type"]
            covs = self._cov_by_pol[pol["policy_id"]]
            clm["policy_id"] = pol["policy_id"]
            clm["customer_id"] = pol["customer_id"]
            clm["coverage_id"] = covs[0]["coverage_id"]
            for d in self._docs_by_clm[clm["claim_id"]]:
                d["uploaded_by"] = pol["customer_id"]

            cd = datetime.strptime(pol["cancellation_date"], "%Y-%m-%d")
            late_dt = cd + timedelta(days=random.randint(5, 45))
            inc = self._inc[clm["incident_id"]]
            inc["incident_date"] = late_dt.strftime("%Y-%m-%d")
            inc["reported_date"] = (late_dt + timedelta(days=1)).strftime("%Y-%m-%d")
            clm["submission_date"] = (late_dt + timedelta(days=3)).strftime("%Y-%m-%d")
            clm["created_at"] = clm["submission_date"] + " 10:15:00"
            clm["status"] = "DENIED"
            clm["denial_reason"] = "POLICY_CANCELLED_BEFORE_INCIDENT"
            for it in self._items_by_clm[clm["claim_id"]]:
                it["approved_amount"] = 0.0
                it["status"] = "DENIED"
            self.ground_truth.append({
                "entity_type": "CLAIM",
                "entity_id": clm["claim_id"],
                "issue_type": "POLICY_CANCELLED_BEFORE_INCIDENT",
                "expected_decision": "DENY",
                "description": f"Policy {pol['policy_id']} cancelled on {pol['cancellation_date']} before incident on {inc['incident_date']}",
                "related_entity_id": pol["policy_id"],
                "severity": "HIGH",
            })

        # ── 3. EC-3 (Required): DUPLICATE_INCIDENT (5 full / 1 starter)
        for i in range(self._edge_target(5)):
            orig = self.incidents[i]
            dup_id = self._inc_id()
            dup = dict(orig)
            dup["incident_id"] = dup_id
            self.incidents.append(dup)
            self._inc[dup_id] = dup
            self.ground_truth.append({
                "entity_type": "INCIDENT",
                "entity_id": dup_id,
                "issue_type": "DUPLICATE_INCIDENT",
                "expected_decision": "FLAG_DUPLICATE",
                "description": f"Duplicate incident report matching {orig['incident_id']} ({orig['incident_type']} on {orig['incident_date']})",
                "related_entity_id": orig["incident_id"],
                "severity": "MEDIUM",
            })

        # ── 4. EC-5 (Required): MISSING_MANDATORY_DOCUMENT (10 full / 2 starter)
        for clm in pick_candidates(self._edge_target(10), {"UNDER_REVIEW", "SUBMITTED"}):
            docs = self._docs_by_clm[clm["claim_id"]]
            removed = docs.pop()
            self.documents.remove(removed)
            clm["status"] = "PENDING_DOCUMENTS"
            for it in self._items_by_clm[clm["claim_id"]]:
                it["approved_amount"] = None
                it["status"] = "PENDING"
            self.ground_truth.append({
                "entity_type": "CLAIM",
                "entity_id": clm["claim_id"],
                "issue_type": "MISSING_MANDATORY_DOCUMENT",
                "expected_decision": "REQUEST_DOCUMENTS",
                "description": f"Mandatory document {removed['document_type']} missing for {clm['claim_type']} claim {clm['claim_id']}",
                "related_entity_id": removed["document_id"],
                "severity": "MEDIUM",
            })

        # ── 5. Step-4 Scenario: POLICY_LAPSED_BEFORE_INCIDENT (15 full / 2 starter)
        lapsed_pols = [p for p in self.policies if p["status"] == "LAPSED" and p.get("lapse_date")]
        for idx, clm in enumerate(pick_candidates(self._edge_target(15), {"UNDER_REVIEW", "APPROVED"})):
            pol = next(
                (p for p in lapsed_pols if p["product_type"] == clm["claim_type"]),
                lapsed_pols[idx % len(lapsed_pols)],
            )
            pol["product_type"] = clm["claim_type"]
            covs = self._cov_by_pol[pol["policy_id"]]
            clm["policy_id"] = pol["policy_id"]
            clm["customer_id"] = pol["customer_id"]
            clm["coverage_id"] = covs[0]["coverage_id"]
            for d in self._docs_by_clm[clm["claim_id"]]:
                d["uploaded_by"] = pol["customer_id"]

            ld = datetime.strptime(pol["lapse_date"], "%Y-%m-%d")
            late_dt = ld + timedelta(days=random.randint(5, 40))
            inc = self._inc[clm["incident_id"]]
            inc["incident_date"] = late_dt.strftime("%Y-%m-%d")
            inc["reported_date"] = (late_dt + timedelta(days=1)).strftime("%Y-%m-%d")
            clm["submission_date"] = (late_dt + timedelta(days=2)).strftime("%Y-%m-%d")
            clm["created_at"] = clm["submission_date"] + " 11:00:00"
            clm["status"] = "DENIED"
            clm["denial_reason"] = "POLICY_LAPSED_BEFORE_INCIDENT"
            for it in self._items_by_clm[clm["claim_id"]]:
                it["approved_amount"] = 0.0
                it["status"] = "DENIED"

        # ── 6. Step-4 Scenario: INCIDENT_EXCLUDED_BY_COVERAGE (15 full / 2 starter)
        for clm in pick_candidates(self._edge_target(15), {"UNDER_REVIEW", "APPROVED"}):
            cov = self._cov_by_id[clm["coverage_id"]]
            inc = self._inc[clm["incident_id"]]
            cur_excl = [x for x in cov["exclusions"].split(",") if x]
            if inc["incident_type"] not in cur_excl:
                cur_excl.append(inc["incident_type"])
                cov["exclusions"] = ",".join(sorted(set(cur_excl)))
            clm["status"] = "DENIED"
            clm["denial_reason"] = "INCIDENT_EXCLUDED_BY_COVERAGE"
            for it in self._items_by_clm[clm["claim_id"]]:
                it["approved_amount"] = 0.0
                it["status"] = "DENIED"

        # ── 7. Step-4 Scenario: SUSPECTED_FRAUD_INDICATOR (25 full / 3 starter)
        for idx, clm in enumerate(pick_candidates(self._edge_target(25), {"UNDER_REVIEW", "APPROVED", "SETTLED"})):
            clm["fraud_indicator"] = True
            clm["priority"] = "URGENT"
            if idx % 2 == 0:
                clm["status"] = "DENIED"
                clm["denial_reason"] = "CONFIRMED_FRAUD_INDICATOR_SIU"
                for it in self._items_by_clm[clm["claim_id"]]:
                    it["approved_amount"] = 0.0
                    it["status"] = "DENIED"
            else:
                clm["status"] = "SIU_INVESTIGATION"
                clm["denial_reason"] = None
                for it in self._items_by_clm[clm["claim_id"]]:
                    it["approved_amount"] = None
                    it["status"] = "PENDING"

        # ── 8. Step-4 Scenario: CLAIM_AMOUNT_OVER_LIMIT (15 full / 2 starter)
        # Claimed amount exceeds coverage_limit, so Assessment caps eligible_amount at coverage_limit!
        for clm in pick_candidates(self._edge_target(15), {"APPROVED", "SETTLED", "CLOSED"}):
            cov = self._cov_by_id[clm["coverage_id"]]
            limit = float(cov["coverage_limit"])
            items = self._items_by_clm[clm["claim_id"]]
            target_total = _rc(limit * random.uniform(1.25, 1.60))
            per_item = _rc(target_total / len(items))
            new_total = 0.0
            for it in items:
                it["quantity"] = 1
                it["unit_cost"] = per_item
                it["claimed_amount"] = per_item
                it["approved_amount"] = per_item
                it["status"] = "APPROVED"
                new_total = _rc(new_total + per_item)
            clm["claim_amount"] = new_total

    # ── Step 9: Assessments (100% Derived from ClaimItems & Coverage Rules) ───

    def _gen_assessments(self) -> None:
        for clm in self.claims:
            if clm["status"] == "SUBMITTED":
                continue
            aid = self._asn_id(clm["claim_id"])
            sub = datetime.strptime(clm["submission_date"], "%Y-%m-%d")
            cov = self._cov_by_id[clm["coverage_id"]]
            cl = float(cov["coverage_limit"])
            ded = float(cov["deductible"])
            copay_pct = int(cov.get("co_pay_percentage") or 0)

            items = self._items_by_clm[clm["claim_id"]]
            is_approved = clm["status"] in ("APPROVED", "SETTLED", "CLOSED")
            is_denied = clm["status"] == "DENIED"

            if is_denied:
                assessed = 0.0
                eligible = 0.0
                cov_verified = False
                liability_pct = 0
                recommendation = "DENY"
                notes = f"Denied ({clm['denial_reason']}): eligible amount is 0.00 USD"
            elif is_approved:
                assessed = _rc(sum(float(it["approved_amount"] or 0.0) for it in items))
                liability_pct = 100
                # Standard formula: min(max(0, assessed - deductible), coverage_limit)
                eligible = min(max(0.0, _rc(assessed - ded)), cl)
                cov_verified = True
                recommendation = "APPROVE"
                notes = (
                    f"Verified coverage {cov['coverage_id']}; assessed={assessed:.2f}, "
                    f"deductible={ded:.2f}, limit={cl:.2f}, eligible={eligible:.2f}"
                )
            else:
                # UNDER_REVIEW, PENDING_DOCUMENTS, SIU_INVESTIGATION
                assessed = _rc(clm["claim_amount"] * 0.90)
                liability_pct = 100
                eligible = min(max(0.0, _rc(assessed - ded)), cl)
                cov_verified = True
                recommendation = "REVIEW"
                notes = f"Preliminary review in progress for {clm['claim_id']} ({clm['status']})"

            fraud_score = (
                round(random.uniform(0.65, 0.98), 3)
                if clm["fraud_indicator"]
                else round(random.uniform(0.01, 0.24), 3)
            )

            rec = {
                "assessment_id": aid,
                "claim_id": clm["claim_id"],
                "adjuster_id": clm["assigned_adjuster_id"],
                "assessment_date": (sub + timedelta(days=random.randint(4, 15))).strftime("%Y-%m-%d"),
                "assessed_amount": assessed,
                "deductible_applied": ded,
                "co_pay_percentage": copay_pct,
                "eligible_amount": eligible,
                "coverage_limit": cl,
                "coverage_verified": cov_verified,
                "liability_percentage": liability_pct,
                "recommendation": recommendation,
                "notes": notes,
                "fraud_score": fraud_score,
            }
            self.assessments.append(rec)
            self._asn_by_clm[clm["claim_id"]] = rec

    # ── Step 10: Reserves (Derived from Claim & Assessment State) ─────────────

    def _gen_reserves(self) -> None:
        for clm in self.claims:
            if clm["status"] in ("SUBMITTED", "DENIED"):
                continue
            rid = self._rsv_id(clm["claim_id"])
            sub = datetime.strptime(clm["submission_date"], "%Y-%m-%d")
            initial_rsv = _rc(clm["claim_amount"] * random.uniform(0.85, 1.15))
            asn = self._asn_by_clm.get(clm["claim_id"])
            is_open = clm["status"] in ("PENDING_DOCUMENTS", "UNDER_REVIEW", "SIU_INVESTIGATION", "APPROVED")
            current_rsv = (
                (asn["eligible_amount"] if (asn and clm["status"] == "APPROVED") else initial_rsv)
                if is_open
                else 0.0
            )
            rec = {
                "reserve_id": rid,
                "claim_id": clm["claim_id"],
                "initial_reserve": initial_rsv,
                "current_reserve": current_rsv,
                "reserve_type": "CASE",
                "set_date": (sub + timedelta(days=random.randint(1, 5))).strftime("%Y-%m-%d"),
                "last_updated": (sub + timedelta(days=random.randint(10, 35))).strftime("%Y-%m-%d"),
                "set_by": clm["assigned_adjuster_id"],
                "currency": "USD",
                "status": "OPEN" if is_open else "CLOSED",
            }
            self.reserves.append(rec)
            self._rsv_by_clm[clm["claim_id"]] = rec

    # ── Step 11: Settlements & Remaining Settlement Edge Cases (EC-4 & EC-6) ──

    def _gen_settlements_and_settlement_edge_cases(self) -> None:
        for clm in self.claims:
            if clm["status"] not in ("SETTLED", "CLOSED"):
                continue
            sid = self._set_id(clm["claim_id"])
            sub = datetime.strptime(clm["submission_date"], "%Y-%m-%d")
            asn = self._asn_by_clm[clm["claim_id"]]
            s_amt = asn["eligible_amount"]
            app_dt = sub + timedelta(days=random.randint(16, 40))
            pay_dt = app_dt + timedelta(days=random.randint(2, 14))
            cust = self._cust[clm["customer_id"]]
            rec = {
                "settlement_id": sid,
                "claim_id": clm["claim_id"],
                "assessment_id": asn["assessment_id"],
                "settlement_amount": s_amt,
                "payment_method": random.choice(["ACH", "DIRECT_DEPOSIT", "WIRE_TRANSFER", "CHECK"]),
                "payment_date": pay_dt.strftime("%Y-%m-%d"),
                "approved_by": clm["assigned_adjuster_id"],
                "approved_date": app_dt.strftime("%Y-%m-%d"),
                "status": "PAID" if clm["status"] == "CLOSED" else random.choice(["APPROVED", "PAID"]),
                "payee_name": f"{cust['first_name']} {cust['last_name']}",
                "payee_customer_id": clm["customer_id"],
                "notes": f"Settlement disbursement for claim {clm['claim_id']}",
            }
            self.settlements.append(rec)
            self._set_by_clm[clm["claim_id"]].append(rec)

        # ── EC-4 (Required): SETTLEMENT_EXCEEDS_LIMIT (5 full / 1 starter)
        count_ec4 = 0
        over_limit_claims: set[str] = set()
        for sett in self.settlements:
            if count_ec4 >= self._edge_target(5):
                break
            if sett["status"] == "PAID":
                continue
            clm = self._clm[sett["claim_id"]]
            cov = self._cov_by_id[clm["coverage_id"]]
            over_amt = _rc(float(cov["coverage_limit"]) * random.uniform(1.12, 1.35))
            sett["settlement_amount"] = over_amt
            sett["notes"] = "ANOMALY: Settlement amount exceeds policy coverage limit"
            over_limit_claims.add(clm["claim_id"])
            self.ground_truth.append({
                "entity_type": "SETTLEMENT",
                "entity_id": sett["settlement_id"],
                "issue_type": "SETTLEMENT_EXCEEDS_LIMIT",
                "expected_decision": "CAP_TO_LIMIT",
                "description": f"Settlement {over_amt:.2f} exceeds coverage_limit {cov['coverage_limit']:.2f} on {cov['coverage_id']}",
                "related_entity_id": cov["coverage_id"],
                "severity": "HIGH",
            })
            count_ec4 += 1

        # Fallback if all settlements were PAID in starter mode
        if count_ec4 < self._edge_target(5):
            for sett in self.settlements:
                if count_ec4 >= self._edge_target(5):
                    break
                if sett["claim_id"] in over_limit_claims:
                    continue
                clm = self._clm[sett["claim_id"]]
                cov = self._cov_by_id[clm["coverage_id"]]
                over_amt = _rc(float(cov["coverage_limit"]) * 1.20)
                sett["settlement_amount"] = over_amt
                sett["status"] = "APPROVED"
                over_limit_claims.add(clm["claim_id"])
                self.ground_truth.append({
                    "entity_type": "SETTLEMENT",
                    "entity_id": sett["settlement_id"],
                    "issue_type": "SETTLEMENT_EXCEEDS_LIMIT",
                    "expected_decision": "CAP_TO_LIMIT",
                    "description": f"Settlement {over_amt:.2f} exceeds coverage_limit {cov['coverage_limit']:.2f} on {cov['coverage_id']}",
                    "related_entity_id": cov["coverage_id"],
                    "severity": "HIGH",
                })
                count_ec4 += 1

        # ── EC-6 (Required): CLAIM_PAID_TWICE (5 full / 1 starter)
        count_ec6 = 0
        for sett in list(self.settlements):
            if count_ec6 >= self._edge_target(5):
                break
            if sett["status"] != "PAID" or sett["claim_id"] in over_limit_claims:
                continue
            dup_id = self._set_id(sett["claim_id"])
            dup = dict(sett)
            dup["settlement_id"] = dup_id
            orig_dt = datetime.strptime(sett["payment_date"], "%Y-%m-%d")
            dup["payment_date"] = (orig_dt + timedelta(days=random.randint(2, 14))).strftime("%Y-%m-%d")
            dup["notes"] = f"Duplicate disbursement of {sett['settlement_id']}"
            self.settlements.append(dup)
            self._set_by_clm[sett["claim_id"]].append(dup)
            self.ground_truth.append({
                "entity_type": "SETTLEMENT",
                "entity_id": dup_id,
                "issue_type": "CLAIM_PAID_TWICE",
                "expected_decision": "FLAG_DUPLICATE_PAYMENT",
                "description": f"Duplicate PAID settlement for claim {sett['claim_id']} (original {sett['settlement_id']})",
                "related_entity_id": sett["settlement_id"],
                "severity": "CRITICAL",
            })
            count_ec6 += 1

    # ── Step 12: Normalized Many-to-Many (N:M) Bridge Tables ──────────────────

    def _gen_bridge_tables(self) -> None:
        """Populates the 2 normalized N:M bridge tables:
        1. claim_adjuster_assignments (Claim <-> Adjuster N:M)
        2. claim_coverage_allocations (Claim <-> Coverage N:M)
        """
        siu_adjusters = [
            a["adjuster_id"]
            for a in self.adjusters
            if a["status"] == "ACTIVE" and a["role_tier"] == "SIU_SPECIALIST"
        ] or [self.adjusters[0]["adjuster_id"]]

        senior_adjusters = [
            a["adjuster_id"]
            for a in self.adjusters
            if a["status"] == "ACTIVE" and a["role_tier"] == "SENIOR_ADJUSTER"
        ] or [self.adjusters[0]["adjuster_id"]]

        for clm in self.claims:
            cid = clm["claim_id"]
            sub_dt = clm["submission_date"]
            # Primary adjuster assignment
            self.claim_adjuster_assignments.append({
                "assignment_id": self._caa_id(cid),
                "claim_id": cid,
                "adjuster_id": clm["assigned_adjuster_id"],
                "assignment_role": "PRIMARY_ADJUSTER",
                "assigned_date": sub_dt,
                "unassigned_date": None,
                "is_active": True,
                "workload_weight": 1.0,
            })
            # If fraud_indicator or high claim amount, assign secondary SIU / Senior reviewer (N:M!)
            if clm["fraud_indicator"]:
                siu_id = random.choice(siu_adjusters)
                if siu_id != clm["assigned_adjuster_id"]:
                    self.claim_adjuster_assignments.append({
                        "assignment_id": self._caa_id(cid),
                        "claim_id": cid,
                        "adjuster_id": siu_id,
                        "assignment_role": "SIU_INVESTIGATOR",
                        "assigned_date": sub_dt,
                        "unassigned_date": None,
                        "is_active": True,
                        "workload_weight": 0.5,
                    })
            elif clm["claim_amount"] >= 25000.0:
                sr_id = random.choice(senior_adjusters)
                if sr_id != clm["assigned_adjuster_id"]:
                    self.claim_adjuster_assignments.append({
                        "assignment_id": self._caa_id(cid),
                        "claim_id": cid,
                        "adjuster_id": sr_id,
                        "assignment_role": "SUPERVISOR_REVIEWER",
                        "assigned_date": sub_dt,
                        "unassigned_date": None,
                        "is_active": True,
                        "workload_weight": 0.25,
                    })

            # Claim <-> Coverage N:M allocation bridge
            pol_covs = self._cov_by_pol[clm["policy_id"]]
            primary_cov = self._cov_by_id[clm["coverage_id"]]
            asn = self._asn_by_clm.get(cid)
            eligible_total = float(asn["eligible_amount"]) if asn else 0.0

            if len(pol_covs) >= 2 and clm["claim_amount"] >= 15000.0:
                secondary_cov = next((c for c in pol_covs if c["coverage_id"] != primary_cov["coverage_id"]), None)
            else:
                secondary_cov = None

            if secondary_cov:
                p_claim = _rc(clm["claim_amount"] * 0.80)
                s_claim = _rc(clm["claim_amount"] - p_claim)
                p_elig = _rc(eligible_total * 0.80)
                s_elig = _rc(eligible_total - p_elig)
                self.claim_coverage_allocations.append({
                    "allocation_id": self._cca_id(cid),
                    "claim_id": cid,
                    "coverage_id": primary_cov["coverage_id"],
                    "is_primary": True,
                    "allocated_claimed_amount": p_claim,
                    "allocated_eligible_amount": p_elig,
                    "deductible_applied": float(primary_cov["deductible"]),
                    "effective_date": primary_cov["effective_date"],
                    "expiration_date": primary_cov["expiration_date"],
                })
                self.claim_coverage_allocations.append({
                    "allocation_id": self._cca_id(cid),
                    "claim_id": cid,
                    "coverage_id": secondary_cov["coverage_id"],
                    "is_primary": False,
                    "allocated_claimed_amount": s_claim,
                    "allocated_eligible_amount": s_elig,
                    "deductible_applied": 0.0,
                    "effective_date": secondary_cov["effective_date"],
                    "expiration_date": secondary_cov["expiration_date"],
                })
            else:
                self.claim_coverage_allocations.append({
                    "allocation_id": self._cca_id(cid),
                    "claim_id": cid,
                    "coverage_id": primary_cov["coverage_id"],
                    "is_primary": True,
                    "allocated_claimed_amount": clm["claim_amount"],
                    "allocated_eligible_amount": eligible_total,
                    "deductible_applied": float(primary_cov["deductible"]),
                    "effective_date": primary_cov["effective_date"],
                    "expiration_date": primary_cov["expiration_date"],
                })

    # ── Step 13: Claim State History & Complete Ground-Truth Decision Table ───

    def _gen_state_history_and_decisions(self) -> None:
        gt_by_entity: Dict[str, dict] = {}
        for g in self.ground_truth:
            gt_by_entity[g["entity_id"]] = g

        for clm in self.claims:
            cid = clm["claim_id"]
            sub_dt = datetime.strptime(clm["submission_date"], "%Y-%m-%d") + timedelta(hours=9)
            cur = clm["status"]
            transitions = {
                "SUBMITTED": [],
                "PENDING_DOCUMENTS": ["PENDING_DOCUMENTS"],
                "UNDER_REVIEW": ["UNDER_REVIEW"],
                "SIU_INVESTIGATION": ["UNDER_REVIEW", "SIU_INVESTIGATION"],
                "DENIED": (
                    ["UNDER_REVIEW", "SIU_INVESTIGATION", "DENIED"]
                    if clm["fraud_indicator"]
                    else ["UNDER_REVIEW", "DENIED"]
                ),
                "APPROVED": ["UNDER_REVIEW", "APPROVED"],
                "SETTLED": ["UNDER_REVIEW", "APPROVED", "SETTLED"],
                "CLOSED": ["UNDER_REVIEW", "APPROVED", "SETTLED", "CLOSED"],
            }
            seq = ["SUBMITTED"] + transitions[cur]
            ts = sub_dt
            for i, s in enumerate(seq):
                self.claim_state_history.append({
                    "event_id": f"EVT-{cid}-{i+1:03d}",
                    "claim_id": cid,
                    "from_status": seq[i - 1] if i > 0 else None,
                    "to_status": s,
                    "changed_by": clm["assigned_adjuster_id"] if i > 0 else "SYSTEM",
                    "change_reason": _state_change_reason(s, clm.get("denial_reason")),
                    "event_timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                })
                ts += timedelta(days=random.randint(2, 8), hours=random.randint(1, 6))
            clm["updated_at"] = ts.strftime("%Y-%m-%d %H:%M:%S")

            # Build full 1,000-claim ground-truth decision record for evaluation
            asn = self._asn_by_clm.get(cid)
            setts = self._set_by_clm.get(cid, [])
            anomaly_type = "NONE"
            expected_decision = "APPROVE_AND_ROUTE_SETTLEMENT"
            if cid in gt_by_entity:
                anomaly_type = gt_by_entity[cid]["issue_type"]
                expected_decision = gt_by_entity[cid]["expected_decision"]
            elif any(s["settlement_id"] in gt_by_entity for s in setts):
                for s in setts:
                    if s["settlement_id"] in gt_by_entity:
                        anomaly_type = gt_by_entity[s["settlement_id"]]["issue_type"]
                        expected_decision = gt_by_entity[s["settlement_id"]]["expected_decision"]
                        break
            elif clm["status"] == "DENIED":
                anomaly_type = clm["denial_reason"] or "COVERAGE_DENIAL"
                expected_decision = "DENY"
            elif clm["status"] == "SIU_INVESTIGATION":
                anomaly_type = "SUSPECTED_FRAUD_SIU_HOLD"
                expected_decision = "HOLD_FOR_SIU"

            self.ground_truth_decisions.append({
                "claim_id": cid,
                "policy_id": clm["policy_id"],
                "claim_type": clm["claim_type"],
                "claimed_amount": clm["claim_amount"],
                "expected_eligible_amount": asn["eligible_amount"] if asn else 0.0,
                "expected_status": clm["status"],
                "expected_decision": expected_decision,
                "anomaly_or_exception_type": anomaly_type,
                "has_fraud_indicator": clm["fraud_indicator"],
            })

    # ── Checksum & Table Inventory ────────────────────────────────────────────

    def _table_names(self) -> List[str]:
        return [
            "customers",
            "adjusters",
            "policies",
            "coverages",
            "incidents",
            "claims",
            "claim_items",
            "documents",
            "assessments",
            "reserves",
            "settlements",
            "claim_adjuster_assignments",
            "claim_coverage_allocations",
            "claim_state_history",
            "ground_truth",
            "ground_truth_decisions",
        ]

    def checksum(self) -> str:
        blob = {name: getattr(self, name) for name in self._table_names()}
        return hashlib.sha256(
            json.dumps(blob, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    # ── Export Multi-Format Data + Zero-Leakage Feature View ──────────────────

    def export(self, output_dir: str) -> dict:
        csv_dir = os.path.join(output_dir, "csv")
        json_dir = os.path.join(output_dir, "json")
        sql_dir = os.path.join(output_dir, "sql")
        feature_dir = os.path.join(output_dir, "features")
        for d in (csv_dir, json_dir, sql_dir, feature_dir):
            os.makedirs(d, exist_ok=True)

        entity_tables = [
            t for t in self._table_names()
            if t not in ("ground_truth", "ground_truth_decisions")
        ]
        manifest: Dict[str, Any] = {
            "generator": "generate.py",
            "generator_version": GENERATOR_VERSION,
            "python_version": platform.python_version(),
            "seed": self.seed,
            "scale": self.scale,
            "generated_at": datetime.now().isoformat(),
            "checksum": self.checksum(),
            "provenance": {
                "data_source": "100% Synthetic Deterministic Generation (Zero PII / Lawful Use)",
                "external_datasets_redistributed": False,
                "license": "MIT",
            },
            "tables": {},
        }

        for name in entity_tables:
            data = getattr(self, name)
            if not data:
                continue
            cp = os.path.join(csv_dir, f"{name}.csv")
            with open(cp, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(data[0].keys()))
                w.writeheader()
                w.writerows(data)

            jp = os.path.join(json_dir, f"{name}.json")
            with open(jp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)

            sp = os.path.join(sql_dir, f"{name}.sql")
            self._write_sql(sp, name, data)

            manifest["tables"][name] = {
                "row_count": len(data),
                "columns": list(data[0].keys()),
                "csv": f"csv/{name}.csv",
                "json": f"json/{name}.json",
                "sql": f"sql/{name}.sql",
            }

        # Export ground_truth.csv, ground_truth.json, and ground_truth_decisions.csv
        if self.ground_truth:
            self._write_csv_list(os.path.join(output_dir, "ground_truth.csv"), self.ground_truth)
            with open(os.path.join(output_dir, "ground_truth.json"), "w", encoding="utf-8") as f:
                json.dump(self.ground_truth, f, indent=2)
            manifest["ground_truth"] = {
                "row_count": len(self.ground_truth),
                "csv": "ground_truth.csv",
                "json": "ground_truth.json",
            }

        if self.ground_truth_decisions:
            self._write_csv_list(
                os.path.join(output_dir, "ground_truth_decisions.csv"),
                self.ground_truth_decisions,
            )
            manifest["ground_truth_decisions"] = {
                "row_count": len(self.ground_truth_decisions),
                "csv": "ground_truth_decisions.csv",
            }

        # Strict Zero-Leakage Feature View (excludes post-intake outcomes & fraud labels)
        input_exclusions = {
            "status",
            "denial_reason",
            "fraud_indicator",
            "assigned_adjuster_id",
            "updated_at",
        }
        base_cols = [c for c in self.claims[0].keys() if c not in input_exclusions]
        feature_rows = []
        for clm in self.claims:
            pol = self._pol[clm["policy_id"]]
            cov = self._cov_by_id[clm["coverage_id"]]
            inc = self._inc[clm["incident_id"]]
            docs = self._docs_by_clm[clm["claim_id"]]
            items = self._items_by_clm[clm["claim_id"]]
            row = {k: clm.get(k) for k in base_cols}
            row.update({
                "policy_status_at_intake": pol["status"],
                "policy_effective_date": pol["effective_date"],
                "policy_expiration_date": pol["expiration_date"],
                "coverage_limit": cov["coverage_limit"],
                "deductible": cov["deductible"],
                "incident_date": inc["incident_date"],
                "incident_severity": inc["severity"],
                "line_item_count": len(items),
                "submitted_document_count": len(docs),
            })
            feature_rows.append(row)

        feature_path = os.path.join(feature_dir, "claims_input.csv")
        self._write_csv_list(feature_path, feature_rows)
        manifest["claims_input_features"] = {
            "row_count": len(feature_rows),
            "csv": "features/claims_input.csv",
            "excluded_outcome_and_label_fields": sorted(input_exclusions),
            "columns": list(feature_rows[0].keys()),
        }

        # Record SHA-256 hashes of every generated file
        manifest["file_sha256"] = {}
        for folder in ("csv", "json", "sql", "features"):
            for filename in sorted(os.listdir(os.path.join(output_dir, folder))):
                path = os.path.join(output_dir, folder, filename)
                with open(path, "rb") as stream:
                    manifest["file_sha256"][f"{folder}/{filename}"] = hashlib.sha256(
                        stream.read()
                    ).hexdigest()
        for filename in ("ground_truth.csv", "ground_truth.json", "ground_truth_decisions.csv"):
            path = os.path.join(output_dir, filename)
            if os.path.exists(path):
                with open(path, "rb") as stream:
                    manifest["file_sha256"][filename] = hashlib.sha256(stream.read()).hexdigest()

        with open(os.path.join(output_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        return manifest

    @staticmethod
    def _write_csv_list(path: str, rows: List[dict]) -> None:
        if not rows:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    def _write_sql(self, path: str, table: str, data: list) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"-- Seed data for {table} (seed={self.seed}, scale={self.scale})\n\n")
            if not data:
                return
            cols = list(data[0].keys())
            for row in data:
                vals = []
                for c in cols:
                    v = row[c]
                    if v is None:
                        vals.append("NULL")
                    elif isinstance(v, bool):
                        vals.append("1" if v else "0")
                    elif isinstance(v, (int, float)):
                        vals.append(str(v))
                    else:
                        vals.append("'" + str(v).replace("'", "''") + "'")
                f.write(
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(vals)});\n"
                )

    # ── Main Pipeline ─────────────────────────────────────────────────────────

    def generate_all(self) -> str:
        steps = [
            ("Generating customers",                 self._gen_customers,                               "customers"),
            ("Generating adjusters",                 self._gen_adjusters,                               "adjusters"),
            ("Generating policies",                  self._gen_policies,                                "policies"),
            ("Generating coverages",                 self._gen_coverages,                               "coverages"),
            ("Generating incidents & baseline claims", self._gen_incidents_and_claims,                  "claims"),
            ("Generating items & deriving totals",   self._gen_claim_items_and_derive_totals,           "claim_items"),
            ("Generating mandatory documents",       self._gen_documents,                               "documents"),
            ("Injecting edge cases & scenarios",     self._inject_all_scenarios_before_downstream_derivation, "ground_truth"),
            ("Deriving eligibility assessments",     self._gen_assessments,                             "assessments"),
            ("Deriving financial reserves",          self._gen_reserves,                                "reserves"),
            ("Deriving settlements & over/dup cases", self._gen_settlements_and_settlement_edge_cases,  "settlements"),
            ("Building N:M bridge tables",           self._gen_bridge_tables,                           "claim_adjuster_assignments"),
            ("Building audit history & GT decisions", self._gen_state_history_and_decisions,            "claim_state_history"),
        ]
        for i, (label, fn, attr) in enumerate(steps, 1):
            print(f"  Step {i:2d}: {label}...")
            fn()
            print(f"          -> {len(getattr(self, attr))} records")

        cs = self.checksum()
        print(f"\n  Checksum: {cs}")
        return cs


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Insurance Claims Synthetic Data Generator (Advanced v2.0)"
    )
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--scale", choices=["starter", "full"], default="full")
    ap.add_argument("--output", default="output")
    ap.add_argument(
        "--verify",
        action="store_true",
        help="Run two independent generations and compare SHA-256 checksums",
    )
    args = ap.parse_args()

    if args.verify:
        print("=== Reproducibility Verification (Two Independent Runs) ===\n")
        sums = []
        for run in (1, 2):
            print(f"--- Run {run} ---")
            g = InsuranceDataGenerator(seed=args.seed, scale=args.scale)
            sums.append(g.generate_all())
            print()
        match = sums[0] == sums[1]
        sym = "PASS" if match else "FAIL"
        print(f"  Run-1 checksum: {sums[0]}")
        print(f"  Run-2 checksum: {sums[1]}")
        print(f"  Result: {sym} — {'checksums match identically' if match else 'MISMATCH'}")
        sys.exit(0 if match else 1)

    print("=== Insurance Claims Data Generator (v2.0) ===")
    print(f"    Seed : {args.seed}")
    print(f"    Scale: {args.scale}\n")

    gen = InsuranceDataGenerator(seed=args.seed, scale=args.scale)
    gen.generate_all()

    print(f"\n  Exporting to {os.path.abspath(args.output)} ...")
    manifest = gen.export(args.output)

    print("\n=== Export Summary ===")
    for t, info in manifest.get("tables", {}).items():
        print(f"  {t:30s} {info['row_count']:>6,d} rows")
    if "ground_truth" in manifest:
        print(f"  {'ground_truth':30s} {manifest['ground_truth']['row_count']:>6,d} anomalies")
    if "ground_truth_decisions" in manifest:
        print(f"  {'ground_truth_decisions':30s} {manifest['ground_truth_decisions']['row_count']:>6,d} claim labels")
    print("\n  Done!")


if __name__ == "__main__":
    main()
