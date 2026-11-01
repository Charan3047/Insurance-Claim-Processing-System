#!/usr/bin/env python3
"""
Insurance Claims Processing — Synthetic Data Generator
=======================================================
Generates a complete insurance-claims dataset with:
  • 2,000 policies across 5 product types (AUTO, HOME, HEALTH, LIFE, TRAVEL)
  • 1,000 claims with full lifecycle
  • Edge cases and anomalies with separate ground-truth labels
  • Reproducible via fixed seed (20261022)

Usage
-----
    python generate.py                        # full-scale, seed 20261022
    python generate.py --scale starter        # small-scale (200 policies / 100 claims)
    python generate.py --verify               # reproducibility check (two runs)
    python generate.py --seed 42 --output out # custom seed & output dir
"""

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
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_SEED = 20261022

SCALE_CONFIGS = {
    "starter": {
        "num_customers": 150,
        "num_policies": 200,
        "num_claims": 100,
        "num_adjusters": 10,
    },
    "full": {
        "num_customers": 1500,
        "num_policies": 2000,
        "num_claims": 1000,
        "num_adjusters": 50,
    },
}

PRODUCTS = ["AUTO", "HOME", "HEALTH", "LIFE", "TRAVEL"]

POLICY_START_RANGE = (datetime(2024, 1, 1), datetime(2026, 6, 30))
CLAIM_DATE_RANGE = (datetime(2025, 1, 1), datetime(2026, 10, 31))

POLICY_STATE_WEIGHTS = {"ACTIVE": 0.60, "EXPIRED": 0.20, "CANCELLED": 0.10, "LAPSED": 0.10}

COVERAGE_CONFIG = {
    "AUTO": {
        "types": [
            "LIABILITY",
            "COLLISION",
            "COMPREHENSIVE",
            "UNINSURED_MOTORIST",
            "MEDICAL_PAYMENTS",
        ],
        "limit_range": (25_000, 500_000),
        "deductible_range": (250, 2_500),
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
        "limit_range": (50_000, 1_000_000),
        "deductible_range": (500, 5_000),
        "exclusions": ["FLOOD", "EARTHQUAKE", "INTENTIONAL_DAMAGE", "NORMAL_WEAR"],
        "required_docs": ["PHOTOS", "INVENTORY_LIST", "REPAIR_ESTIMATE", "POLICE_REPORT"],
    },
    "HEALTH": {
        "types": ["HOSPITALIZATION", "OUTPATIENT", "PRESCRIPTION", "DENTAL", "VISION"],
        "limit_range": (10_000, 2_000_000),
        "deductible_range": (500, 10_000),
        "exclusions": ["COSMETIC", "EXPERIMENTAL", "PRE_EXISTING_UNCOVERED"],
        "required_docs": ["MEDICAL_RECORDS", "BILLS", "REFERRAL", "ID_PROOF"],
    },
    "LIFE": {
        "types": ["TERM_DEATH", "ACCIDENTAL_DEATH", "DISABILITY", "CRITICAL_ILLNESS"],
        "limit_range": (50_000, 5_000_000),
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
        "limit_range": (1_000, 100_000),
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

CLAIM_STATES = [
    "SUBMITTED",
    "UNDER_REVIEW",
    "PENDING_DOCUMENTS",
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
EMAIL_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "aol.com"]

BASE_PREMIUMS = {"AUTO": 1200, "HOME": 1800, "HEALTH": 4800, "LIFE": 600, "TRAVEL": 200}

# Distribution of claims across the 5 types
CLAIM_TYPE_SHARES = {"AUTO": 0.30, "HOME": 0.20, "HEALTH": 0.25, "LIFE": 0.10, "TRAVEL": 0.15}


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


def _rand_date(start: datetime, end: datetime) -> datetime:
    delta = end - start
    return start + timedelta(days=random.randint(0, max(delta.days, 0)))


def _rand_phone() -> str:
    return f"({random.randint(200,999)}) {random.randint(200,999)}-{random.randint(1000,9999)}"


def _rand_email(first: str, last: str) -> str:
    sep = random.choice([".", "_", ""])
    n = random.randint(1, 999)
    return f"{first.lower()}{sep}{last.lower()}{n}@{random.choice(EMAIL_DOMAINS)}"


def _rc(val: float) -> float:
    """Round to 2 decimals (currency)."""
    return round(val, 2)


def _weighted(d: Dict[str, float]) -> str:
    return random.choices(list(d.keys()), weights=list(d.values()), k=1)[0]


def _state_change_reason(state: str, denial_reason: Optional[str] = None) -> str:
    return {
        "SUBMITTED": "Claim submitted by policyholder",
        "UNDER_REVIEW": "Assigned to adjuster for review",
        "PENDING_DOCUMENTS": "Additional documentation requested",
        "APPROVED": "Claim validated and approved for settlement",
        "DENIED": denial_reason or "Coverage validation failed",
        "SETTLED": "Settlement amount disbursed",
        "CLOSED": "Claim process completed and archived",
    }.get(state, "Status updated")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════


class InsuranceDataGenerator:
    """Deterministic, seed-controlled generator for the full claims-processing
    data model.  Call ``generate_all()`` then ``export(output_dir)``."""

    def __init__(self, seed: int = DEFAULT_SEED, scale: str = "full"):
        self.seed = seed
        self.scale = scale
        self.cfg = SCALE_CONFIGS[scale]
        random.seed(seed)

        # Monotonic counters keyed by prefix
        self._ctr: Dict[str, int] = defaultdict(int)

        # Entity stores
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
        self.claim_state_history: List[dict] = []
        self.ground_truth: List[dict] = []

        # Indexes
        self._pol: Dict[str, dict] = {}
        self._cust: Dict[str, dict] = {}
        self._cov_by_pol: Dict[str, List[dict]] = defaultdict(list)
        self._inc: Dict[str, dict] = {}
        self._clm: Dict[str, dict] = {}
        self._adj: Dict[str, dict] = {}
        self._adj_by_prod: Dict[str, List[str]] = defaultdict(list)

    # ── ID factories ──────────────────────────────────────────────────────────

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

    # ── 1. Customers ──────────────────────────────────────────────────────────

    def _gen_customers(self):
        for _ in range(self.cfg["num_customers"]):
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
                    datetime(1950, 1, 1), datetime(2000, 12, 31)
                ).strftime("%Y-%m-%d"),
                "email": _rand_email(first, last),
                "phone": _rand_phone(),
                "address_line1": (
                    f"{random.randint(100,9999)} "
                    f"{random.choice(STREET_NAMES)} "
                    f"{random.choice(STREET_TYPES)}"
                ),
                "city": city,
                "state": state,
                "zip_code": f"{random.randint(10000,99999)}",
                "created_at": _rand_date(
                    datetime(2020, 1, 1), datetime(2024, 12, 31)
                ).strftime("%Y-%m-%d %H:%M:%S"),
                "status": random.choices(
                    ["ACTIVE", "INACTIVE"], weights=[0.9, 0.1], k=1
                )[0],
            }
            self.customers.append(rec)
            self._cust[cid] = rec

    # ── 2. Adjusters ──────────────────────────────────────────────────────────

    def _gen_adjusters(self):
        for _ in range(self.cfg["num_adjusters"]):
            aid = self._adj_id()
            first = random.choice(FIRST_NAMES)
            last = random.choice(LAST_NAMES)
            specs = random.sample(PRODUCTS, k=random.randint(1, 3))
            rec = {
                "adjuster_id": aid,
                "first_name": first,
                "last_name": last,
                "email": _rand_email(first, last),
                "phone": _rand_phone(),
                "specializations": ",".join(specs),
                "license_number": f"ADJ-LIC-{random.randint(100000,999999)}",
                "hire_date": _rand_date(
                    datetime(2015, 1, 1), datetime(2024, 6, 30)
                ).strftime("%Y-%m-%d"),
                "status": random.choices(
                    ["ACTIVE", "ON_LEAVE", "INACTIVE"],
                    weights=[0.85, 0.10, 0.05],
                    k=1,
                )[0],
                "max_concurrent_claims": random.choice([10, 15, 20, 25]),
            }
            self.adjusters.append(rec)
            self._adj[aid] = rec
            if rec["status"] == "ACTIVE":
                for s in specs:
                    self._adj_by_prod[s].append(aid)

        # Guarantee at least one active specialist for each product, including
        # the small starter scale, so the operational demo always has an assignee.
        for product in PRODUCTS:
            candidates = [a for a in self.adjusters if product in a["specializations"].split(",")]
            active = next((a for a in candidates if a["status"] == "ACTIVE"), None)
            if active:
                continue
            target = candidates[0] if candidates else self.adjusters[0]
            target["status"] = "ACTIVE"
            if product not in target["specializations"].split(","):
                target["specializations"] = ",".join([target["specializations"], product]).strip(",")
            if target["adjuster_id"] not in self._adj_by_prod[product]:
                self._adj_by_prod[product].append(target["adjuster_id"])

    # ── 3. Policies ───────────────────────────────────────────────────────────

    def _gen_policies(self):
        cust_ids = [c["customer_id"] for c in self.customers]
        for _ in range(self.cfg["num_policies"]):
            product = random.choice(PRODUCTS)
            pid = self._pol_id(product)
            cid = random.choice(cust_ids)
            start = _rand_date(*POLICY_START_RANGE)
            term = random.choice([6, 12, 24, 36])
            end = start + timedelta(days=term * 30)
            st = _weighted(POLICY_STATE_WEIGHTS)
            cancel_dt = None
            lapse_dt = None
            if st == "CANCELLED":
                cancel_dt = _rand_date(
                    start + timedelta(days=30), end
                ).strftime("%Y-%m-%d")
            elif st == "LAPSED":
                lapse_dt = _rand_date(
                    start + timedelta(days=60), end
                ).strftime("%Y-%m-%d")
            premium = _rc(
                BASE_PREMIUMS[product] * random.uniform(0.5, 2.5) * (term / 12)
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
                    ["APPROVED", "CONDITIONAL", "DECLINED"],
                    weights=[0.80, 0.15, 0.05],
                    k=1,
                )[0],
                "created_at": start.strftime("%Y-%m-%d %H:%M:%S"),
                "updated_at": datetime(2026, 10, 31).strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.policies.append(rec)
            self._pol[pid] = rec

    # ── 4. Coverages ──────────────────────────────────────────────────────────

    def _gen_coverages(self):
        for pol in self.policies:
            prod = pol["product_type"]
            cc = COVERAGE_CONFIG[prod]
            n = random.randint(2, min(4, len(cc["types"])))
            for cov_type in random.sample(cc["types"], n):
                cov_id = self._cov_id(pol["policy_id"])
                lim = _rc(random.uniform(*cc["limit_range"]))
                ded = (
                    _rc(random.uniform(*cc["deductible_range"]))
                    if cc["deductible_range"][1] > 0
                    else 0.0
                )
                ne = random.randint(0, min(2, len(cc["exclusions"])))
                excl = random.sample(cc["exclusions"], ne)
                rec = {
                    "coverage_id": cov_id,
                    "policy_id": pol["policy_id"],
                    "coverage_type": cov_type,
                    "coverage_limit": lim,
                    "deductible": ded,
                    "co_pay_percentage": (
                        random.choice([0, 10, 20, 30]) if prod == "HEALTH" else 0
                    ),
                    "exclusions": ",".join(excl) if excl else "",
                    "effective_date": pol["effective_date"],
                    "expiration_date": pol["expiration_date"],
                    "status": "ACTIVE" if pol["status"] == "ACTIVE" else "INACTIVE",
                }
                self.coverages.append(rec)
                self._cov_by_pol[pol["policy_id"]].append(rec)

    # ── 5. Incidents ──────────────────────────────────────────────────────────

    def _gen_incidents(self):
        n = int(self.cfg["num_claims"] * 1.15)
        for _ in range(n):
            iid = self._inc_id()
            prod = random.choice(PRODUCTS)
            itype = random.choice(INCIDENT_TYPES[prod])
            idate = _rand_date(*CLAIM_DATE_RANGE)
            state = random.choice(STATES_LIST)
            city = random.choice(CITIES_BY_STATE[state])
            rec = {
                "incident_id": iid,
                "incident_type": itype,
                "incident_date": idate.strftime("%Y-%m-%d"),
                "incident_time": f"{random.randint(0,23):02d}:{random.randint(0,59):02d}:00",
                "description": f"{itype.replace('_',' ').title()} incident reported",
                "location_city": city,
                "location_state": state,
                "severity": random.choices(
                    ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                    weights=[0.30, 0.40, 0.20, 0.10],
                    k=1,
                )[0],
                "police_report_filed": random.choices(
                    [True, False], weights=[0.6, 0.4], k=1
                )[0],
                "witnesses": random.randint(0, 5),
                "product_category": prod,
                "reported_date": (
                    idate + timedelta(days=random.randint(0, 14))
                ).strftime("%Y-%m-%d"),
            }
            self.incidents.append(rec)
            self._inc[iid] = rec

    # ── 6. Claims ─────────────────────────────────────────────────────────────

    def _gen_claims(self):
        total = self.cfg["num_claims"]
        per_type = {
            p: int(total * CLAIM_TYPE_SHARES[p]) for p in PRODUCTS
        }
        # fix rounding
        per_type["AUTO"] += total - sum(per_type.values())

        pols_by_prod: Dict[str, list] = defaultdict(list)
        for p in self.policies:
            pols_by_prod[p["product_type"]].append(p)

        incs_by_prod: Dict[str, list] = defaultdict(list)
        for i in self.incidents:
            incs_by_prod[i["product_category"]].append(i)

        for prod, count in per_type.items():
            avail_pols = pols_by_prod.get(prod, [])
            avail_incs = incs_by_prod.get(prod, [])
            if not avail_pols or not avail_incs:
                continue
            for _ in range(count):
                cid = self._clm_id()
                pol = random.choice(avail_pols)
                inc = random.choice(avail_incs)

                p_start = datetime.strptime(pol["effective_date"], "%Y-%m-%d")
                p_end = datetime.strptime(pol["expiration_date"], "%Y-%m-%d")
                i_date = datetime.strptime(inc["incident_date"], "%Y-%m-%d")

                valid = True
                deny = None

                if i_date < p_start or i_date > p_end:
                    valid = False
                    deny = "INCIDENT_OUTSIDE_COVERAGE_PERIOD"

                if pol["status"] == "CANCELLED" and pol.get("cancellation_date"):
                    cd = datetime.strptime(pol["cancellation_date"], "%Y-%m-%d")
                    if i_date > cd:
                        valid = False
                        deny = "POLICY_CANCELLED_BEFORE_INCIDENT"

                if pol["status"] == "LAPSED" and pol.get("lapse_date"):
                    ld = datetime.strptime(pol["lapse_date"], "%Y-%m-%d")
                    if i_date > ld:
                        valid = False
                        deny = "POLICY_LAPSED_BEFORE_INCIDENT"

                covs = self._cov_by_pol.get(pol["policy_id"], [])
                if not covs:
                    valid = False
                    deny = "NO_COVERAGE_FOUND"

                sel_cov = random.choice(covs) if covs else None
                if sel_cov:
                    cl = sel_cov["coverage_limit"]
                    claim_amt = _rc(random.uniform(500, cl * 0.8))
                else:
                    cl = 0
                    claim_amt = _rc(random.uniform(500, 50000))

                if not valid:
                    status = "DENIED"
                else:
                    status = random.choices(
                        ["SUBMITTED", "UNDER_REVIEW", "APPROVED", "SETTLED", "CLOSED"],
                        weights=[0.10, 0.15, 0.20, 0.35, 0.20],
                        k=1,
                    )[0]

                sub_date = datetime.strptime(
                    inc["reported_date"], "%Y-%m-%d"
                ) + timedelta(days=random.randint(0, 7))

                adj_pool = self._adj_by_prod.get(prod, [])
                if not adj_pool:
                    adj_pool = [a["adjuster_id"] for a in self.adjusters]
                assigned_adj = random.choice(adj_pool) if adj_pool else None

                fraud = random.choices([False, True], weights=[0.95, 0.05], k=1)[0]

                rec = {
                    "claim_id": cid,
                    "policy_id": pol["policy_id"],
                    "customer_id": pol["customer_id"],
                    "incident_id": inc["incident_id"],
                    "claim_type": prod,
                    "claim_amount": claim_amt,
                    "submission_date": sub_date.strftime("%Y-%m-%d"),
                    "status": status,
                    "assigned_adjuster_id": assigned_adj,
                    "priority": random.choices(
                        ["LOW", "MEDIUM", "HIGH", "URGENT"],
                        weights=[0.20, 0.40, 0.30, 0.10],
                        k=1,
                    )[0],
                    "denial_reason": deny,
                    "fraud_indicator": fraud,
                    "coverage_id": sel_cov["coverage_id"] if sel_cov else None,
                    "created_at": sub_date.strftime("%Y-%m-%d %H:%M:%S"),
                    "updated_at": (
                        sub_date + timedelta(days=random.randint(1, 60))
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                }
                self.claims.append(rec)
                self._clm[cid] = rec
                self._build_state_history(rec, sub_date)

    # ── state-history helper ──────────────────────────────────────────────────

    def _build_state_history(self, claim: dict, sub_date: datetime):
        seq = ["SUBMITTED"]
        cur = claim["status"]
        transitions = {
            "DENIED":        ["UNDER_REVIEW", "DENIED"],
            "UNDER_REVIEW":  ["UNDER_REVIEW"],
            "APPROVED":      ["UNDER_REVIEW", "APPROVED"],
            "SETTLED":       ["UNDER_REVIEW", "APPROVED", "SETTLED"],
            "CLOSED":        ["UNDER_REVIEW", "APPROVED", "SETTLED", "CLOSED"],
        }
        seq.extend(transitions.get(cur, []))
        ts = sub_date
        for i, s in enumerate(seq):
            self.claim_state_history.append(
                {
                    "event_id": f"EVT-{claim['claim_id']}-{i+1:03d}",
                    "claim_id": claim["claim_id"],
                    "from_status": seq[i - 1] if i > 0 else None,
                    "to_status": s,
                    "changed_by": (
                        claim["assigned_adjuster_id"] if i > 0 else "SYSTEM"
                    ),
                    "change_reason": _state_change_reason(
                        s, claim.get("denial_reason")
                    ),
                    "event_timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
            ts += timedelta(days=random.randint(1, 14))

    # ── 7. Claim Items ────────────────────────────────────────────────────────

    def _gen_claim_items(self):
        item_descs = {
            "AUTO": ["Vehicle body repair", "Windshield replacement", "Towing service",
                      "Rental car", "Paint restoration"],
            "HOME": ["Roof repair", "Water extraction", "Furniture replacement",
                      "Electrical rewiring", "Structural assessment"],
            "HEALTH": ["Hospital stay", "Surgical procedure", "Lab tests",
                        "Prescription medication", "Physical therapy"],
            "LIFE": ["Death benefit payout", "Accidental death rider",
                      "Disability income", "Critical illness lump sum"],
            "TRAVEL": ["Trip rebooking", "Emergency evacuation", "Lost luggage compensation",
                        "Hotel accommodation", "Medical treatment abroad"],
        }
        for clm in self.claims:
            n = random.randint(1, 5)
            descs = item_descs.get(clm["claim_type"], ["General item"])
            for i in range(n):
                amt = _rc(clm["claim_amount"] / n * random.uniform(0.5, 1.5))
                qty = random.randint(1, 3)
                approved = clm["status"] in ("APPROVED", "SETTLED", "CLOSED")
                self.claim_items.append(
                    {
                        "claim_item_id": f"CLI-{clm['claim_id']}-{i+1:03d}",
                        "claim_id": clm["claim_id"],
                        "item_description": descs[i % len(descs)],
                        "item_category": clm["claim_type"],
                        "claimed_amount": amt,
                        "approved_amount": (
                            _rc(amt * random.uniform(0.5, 1.0)) if approved else None
                        ),
                        "quantity": qty,
                        "unit_cost": _rc(amt / qty),
                        "status": (
                            "APPROVED"
                            if approved
                            else ("DENIED" if clm["status"] == "DENIED" else "PENDING")
                        ),
                    }
                )

    # ── 8. Documents ──────────────────────────────────────────────────────────

    def _gen_documents(self):
        for clm in self.claims:
            prod = clm["claim_type"]
            req = COVERAGE_CONFIG[prod]["required_docs"]
            sub = datetime.strptime(clm["submission_date"], "%Y-%m-%d")
            for doc_type in req:
                did = self._doc_id(clm["claim_id"])
                self.documents.append(
                    {
                        "document_id": did,
                        "claim_id": clm["claim_id"],
                        "document_type": doc_type,
                        "file_name": f"{doc_type.lower()}_{clm['claim_id']}.pdf",
                        "file_size_kb": random.randint(50, 5000),
                        "upload_date": (
                            sub + timedelta(days=random.randint(0, 10))
                        ).strftime("%Y-%m-%d"),
                        "uploaded_by": clm["customer_id"],
                        "status": random.choices(
                            ["RECEIVED", "VERIFIED", "REJECTED"],
                            weights=[0.30, 0.60, 0.10],
                            k=1,
                        )[0],
                        "verification_date": (
                            (sub + timedelta(days=random.randint(5, 20))).strftime(
                                "%Y-%m-%d"
                            )
                            if random.random() > 0.2
                            else None
                        ),
                        "notes": "",
                    }
                )

    # ── 9. Assessments ────────────────────────────────────────────────────────

    def _gen_assessments(self):
        for clm in self.claims:
            if clm["status"] == "SUBMITTED":
                continue
            aid = self._asn_id(clm["claim_id"])
            sub = datetime.strptime(clm["submission_date"], "%Y-%m-%d")
            cov = None
            if clm.get("coverage_id"):
                for c in self._cov_by_pol.get(clm["policy_id"], []):
                    if c["coverage_id"] == clm["coverage_id"]:
                        cov = c
                        break
            cl = cov["coverage_limit"] if cov else 0
            ded = cov["deductible"] if cov else 0
            assessed = _rc(clm["claim_amount"] * random.uniform(0.7, 1.0))
            eligible = max(0.0, _rc(assessed - ded))
            if cl > 0:
                eligible = min(eligible, cl)
            is_approved = clm["status"] in ("APPROVED", "SETTLED", "CLOSED")
            self.assessments.append(
                {
                    "assessment_id": aid,
                    "claim_id": clm["claim_id"],
                    "adjuster_id": clm["assigned_adjuster_id"],
                    "assessment_date": (
                        sub + timedelta(days=random.randint(3, 21))
                    ).strftime("%Y-%m-%d"),
                    "assessed_amount": assessed,
                    "deductible_applied": ded,
                    "eligible_amount": eligible,
                    "coverage_limit": cl,
                    "coverage_verified": clm["status"] != "DENIED",
                    "liability_percentage": random.choice([0, 25, 50, 75, 100]),
                    "recommendation": (
                        "APPROVE"
                        if is_approved
                        else ("DENY" if clm["status"] == "DENIED" else "REVIEW")
                    ),
                    "notes": f"Assessment completed for claim {clm['claim_id']}",
                    "fraud_score": (
                        round(random.uniform(0, 0.3), 3)
                        if not clm["fraud_indicator"]
                        else round(random.uniform(0.5, 1.0), 3)
                    ),
                }
            )

    # ── 10. Reserves ──────────────────────────────────────────────────────────

    def _gen_reserves(self):
        for clm in self.claims:
            if clm["status"] in ("SUBMITTED", "DENIED"):
                continue
            rid = self._rsv_id(clm["claim_id"])
            sub = datetime.strptime(clm["submission_date"], "%Y-%m-%d")
            amt = _rc(clm["claim_amount"] * random.uniform(0.8, 1.2))
            self.reserves.append(
                {
                    "reserve_id": rid,
                    "claim_id": clm["claim_id"],
                    "initial_reserve": amt,
                    "current_reserve": amt,
                    "reserve_type": "CASE",
                    "set_date": (
                        sub + timedelta(days=random.randint(1, 7))
                    ).strftime("%Y-%m-%d"),
                    "last_updated": (
                        sub + timedelta(days=random.randint(7, 30))
                    ).strftime("%Y-%m-%d"),
                    "set_by": clm["assigned_adjuster_id"],
                    "currency": "USD",
                    "status": (
                        "OPEN"
                        if clm["status"] in ("UNDER_REVIEW", "APPROVED")
                        else "CLOSED"
                    ),
                }
            )

    # ── 11. Settlements ───────────────────────────────────────────────────────

    def _gen_settlements(self):
        # Index assessments by claim_id for fast lookup
        asn_idx: Dict[str, dict] = {}
        for a in self.assessments:
            asn_idx.setdefault(a["claim_id"], a)

        for clm in self.claims:
            if clm["status"] not in ("SETTLED", "CLOSED"):
                continue
            sid = self._set_id(clm["claim_id"])
            sub = datetime.strptime(clm["submission_date"], "%Y-%m-%d")
            asn = asn_idx.get(clm["claim_id"])
            s_amt = asn["eligible_amount"] if asn else _rc(clm["claim_amount"] * 0.8)
            s_date = sub + timedelta(days=random.randint(14, 90))
            cust = self._cust.get(clm["customer_id"], {})
            self.settlements.append(
                {
                    "settlement_id": sid,
                    "claim_id": clm["claim_id"],
                    "assessment_id": asn["assessment_id"] if asn else None,
                    "settlement_amount": s_amt,
                    "payment_method": random.choice(
                        ["CHECK", "ACH", "WIRE_TRANSFER", "DIRECT_DEPOSIT"]
                    ),
                    "payment_date": s_date.strftime("%Y-%m-%d"),
                    "approved_by": clm["assigned_adjuster_id"],
                    "approved_date": (
                        s_date - timedelta(days=random.randint(1, 7))
                    ).strftime("%Y-%m-%d"),
                    "status": (
                        "PAID"
                        if clm["status"] == "CLOSED"
                        else random.choice(["PENDING", "APPROVED", "PAID"])
                    ),
                    "payee_name": (
                        f"{cust.get('first_name','')} {cust.get('last_name','')}"
                    ).strip(),
                    "payee_customer_id": clm["customer_id"],
                    "notes": f"Settlement for claim {clm['claim_id']}",
                }
            )

    # ── 12. Edge-case injection ───────────────────────────────────────────────

    def _clone_incident_for_claim(self, clm: dict, incident: dict, new_date: str) -> dict:
        """Keep an injected incident anomaly isolated to its labelled claim."""
        clone = dict(incident)
        clone["incident_id"] = self._inc_id()
        clone["incident_date"] = new_date
        self.incidents.append(clone)
        self._inc[clone["incident_id"]] = clone
        clm["incident_id"] = clone["incident_id"]
        return clone

    def _record_injected_state(self, clm: dict, new_status: str, reason: str) -> None:
        history = [e for e in self.claim_state_history if e["claim_id"] == clm["claim_id"]]
        history.sort(key=lambda e: e["event_timestamp"])
        current = history[-1]["to_status"] if history else None
        if current == new_status:
            return
        path = [new_status]
        if current == "SUBMITTED" and new_status == "DENIED":
            path = ["UNDER_REVIEW", "DENIED"]
        stamp = (datetime.strptime(history[-1]["event_timestamp"], "%Y-%m-%d %H:%M:%S")
                 if history else datetime.strptime(clm["submission_date"], "%Y-%m-%d"))
        for status in path:
            stamp += timedelta(minutes=1)
            seq = len(history) + 1
            event = {
                "event_id": f"EVT-{clm['claim_id']}-{seq:03d}",
                "claim_id": clm["claim_id"],
                "from_status": current,
                "to_status": status,
                "changed_by": "SYSTEM",
                "change_reason": reason,
                "event_timestamp": stamp.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.claim_state_history.append(event)
            history.append(event)
            current = status

    def _reflect_injected_denial(self, claim_id: str, reason: str) -> None:
        for assessment in self.assessments:
            if assessment["claim_id"] == claim_id:
                assessment["recommendation"] = "DENY"
                assessment["coverage_verified"] = False
                assessment["notes"] = f"Denied: {reason}"
        for reserve in self.reserves:
            if reserve["claim_id"] == claim_id:
                reserve["status"] = "CLOSED"

    def _edge_target(self, full_scale_count: int) -> int:
        """Keep all six scenario classes in starter data at a smaller count."""
        return full_scale_count if self.scale == "full" else 1

    def _inject_edge_cases(self):
        # ── EC-1  Claim date before policy start (10 cases) ───────────────────
        count = 0
        for clm in self.claims:
            if count >= self._edge_target(10):
                break
            if clm["status"] in {"DENIED", "SETTLED", "CLOSED"}:
                continue
            pol = self._pol.get(clm["policy_id"])
            inc = self._inc.get(clm["incident_id"])
            if not pol or not inc:
                continue
            p_start = datetime.strptime(pol["effective_date"], "%Y-%m-%d")
            early = p_start - timedelta(days=random.randint(30, 365))
            inc = self._clone_incident_for_claim(clm, inc, early.strftime("%Y-%m-%d"))
            clm["status"] = "DENIED"
            clm["denial_reason"] = "CLAIM_DATE_BEFORE_POLICY_START"
            self._record_injected_state(clm, "DENIED", clm["denial_reason"])
            self._reflect_injected_denial(clm["claim_id"], clm["denial_reason"])
            self.ground_truth.append(
                {
                    "entity_type": "CLAIM",
                    "entity_id": clm["claim_id"],
                    "issue_type": "CLAIM_DATE_BEFORE_POLICY_START",
                    "expected_decision": "DENY",
                    "description": (
                        f"Incident date {early.strftime('%Y-%m-%d')} < "
                        f"policy start {pol['effective_date']}"
                    ),
                    "related_entity_id": inc["incident_id"],
                    "severity": "HIGH",
                }
            )
            count += 1

        # ── EC-2  Policy cancelled before incident (10 cases) ─────────────────
        count = 0
        already_labelled = {r["entity_id"] for r in self.ground_truth if r["entity_type"] == "CLAIM"}
        for pol in self.policies:
            if count >= self._edge_target(10):
                break
            if pol["status"] != "CANCELLED" or not pol.get("cancellation_date"):
                continue
            coverages = self._cov_by_pol.get(pol["policy_id"], [])
            if not coverages:
                continue
            clm = next((c for c in self.claims
                        if c["claim_type"] == pol["product_type"]
                        and c["status"] not in {"DENIED", "SETTLED", "CLOSED"}
                        and c["claim_id"] not in already_labelled), None)
            if clm is None:
                continue
            # Repoint one otherwise-unlabelled claim to a coherent cancelled policy
            # of the same product so starter-scale runs also contain this scenario.
            clm["policy_id"] = pol["policy_id"]
            clm["customer_id"] = pol["customer_id"]
            clm["coverage_id"] = coverages[0]["coverage_id"]
            cd = datetime.strptime(pol["cancellation_date"], "%Y-%m-%d")
            late = cd + timedelta(days=random.randint(1, 60))
            inc = self._inc.get(clm["incident_id"])
            if not inc:
                continue
            inc = self._clone_incident_for_claim(clm, inc, late.strftime("%Y-%m-%d"))
            clm["status"] = "DENIED"
            clm["denial_reason"] = "POLICY_CANCELLED_BEFORE_INCIDENT"
            self._record_injected_state(clm, "DENIED", clm["denial_reason"])
            self._reflect_injected_denial(clm["claim_id"], clm["denial_reason"])
            new_cov = coverages[0]
            for assessment in self.assessments:
                if assessment["claim_id"] == clm["claim_id"]:
                    assessment["coverage_limit"] = new_cov["coverage_limit"]
                    assessment["deductible_applied"] = new_cov["deductible"]
                    assessment["eligible_amount"] = min(
                        max(0.0, _rc(assessment["assessed_amount"] - new_cov["deductible"])),
                        new_cov["coverage_limit"],
                    )
            self._reflect_injected_denial(clm["claim_id"], clm["denial_reason"])
            self.ground_truth.append({
                "entity_type": "CLAIM", "entity_id": clm["claim_id"],
                "issue_type": "POLICY_CANCELLED_BEFORE_INCIDENT", "expected_decision": "DENY",
                "description": f"Policy {pol['policy_id']} cancelled {pol['cancellation_date']}, incident {late.strftime('%Y-%m-%d')}",
                "related_entity_id": pol["policy_id"], "severity": "HIGH",
            })
            already_labelled.add(clm["claim_id"])
            count += 1

        # ── EC-3  Duplicate incident (5 cases) ───────────────────────────────
        for i in range(min(self._edge_target(5), len(self.incidents))):
            orig = self.incidents[i]
            dup_id = self._inc_id()
            dup = dict(orig)
            dup["incident_id"] = dup_id
            self.incidents.append(dup)
            self._inc[dup_id] = dup
            self.ground_truth.append(
                {
                    "entity_type": "INCIDENT",
                    "entity_id": dup_id,
                    "issue_type": "DUPLICATE_INCIDENT",
                    "expected_decision": "FLAG_DUPLICATE",
                    "description": f"Duplicate of incident {orig['incident_id']}",
                    "related_entity_id": orig["incident_id"],
                    "severity": "MEDIUM",
                }
            )

        # ── EC-4  Settlement exceeds coverage limit (5 cases) ─────────────────
        count = 0
        for sett in self.settlements:
            if count >= self._edge_target(5):
                break
            # Keep the over-limit example separate from the duplicate-payment case.
            if sett.get("status") == "PAID":
                continue
            clm = self._clm.get(sett["claim_id"])
            if not clm or not clm.get("coverage_id"):
                continue
            for cov in self._cov_by_pol.get(clm["policy_id"], []):
                if cov["coverage_id"] == clm["coverage_id"]:
                    over = _rc(cov["coverage_limit"] * random.uniform(1.1, 1.5))
                    sett["settlement_amount"] = over
                    self.ground_truth.append(
                        {
                            "entity_type": "SETTLEMENT",
                            "entity_id": sett["settlement_id"],
                            "issue_type": "SETTLEMENT_EXCEEDS_LIMIT",
                            "expected_decision": "CAP_TO_LIMIT",
                            "description": (
                                f"Settlement {over} > limit {cov['coverage_limit']}"
                            ),
                            "related_entity_id": cov["coverage_id"],
                            "severity": "HIGH",
                        }
                    )
                    count += 1
                    break

        # ── EC-5  Missing mandatory document (10 cases) ──────────────────────
        docs_by_clm: Dict[str, List[dict]] = defaultdict(list)
        for d in self.documents:
            docs_by_clm[d["claim_id"]].append(d)
        count = 0
        for clm in self.claims:
            if count >= self._edge_target(10):
                break
            docs = docs_by_clm.get(clm["claim_id"], [])
            if len(docs) >= 2 and clm["status"] == "UNDER_REVIEW":
                removed = docs[-1]
                self.documents.remove(removed)
                clm["status"] = "PENDING_DOCUMENTS"
                self._record_injected_state(clm, "PENDING_DOCUMENTS", "Mandatory document missing; request evidence")
                self.ground_truth.append(
                    {
                        "entity_type": "CLAIM",
                        "entity_id": clm["claim_id"],
                        "issue_type": "MISSING_MANDATORY_DOCUMENT",
                        "expected_decision": "REQUEST_DOCUMENTS",
                        "description": (
                            f"Missing {removed['document_type']} "
                            f"for claim {clm['claim_id']}"
                        ),
                        "related_entity_id": removed["document_id"],
                        "severity": "MEDIUM",
                    }
                )
                count += 1

        # ── EC-6  Claim paid twice (5 cases) ──────────────────────────────────
        count = 0
        for sett in list(self.settlements):
            if count >= self._edge_target(5):
                break
            if sett["status"] != "PAID":
                continue
            dup_id = self._set_id(sett["claim_id"])
            dup = dict(sett)
            dup["settlement_id"] = dup_id
            orig_dt = datetime.strptime(sett["payment_date"], "%Y-%m-%d")
            dup["payment_date"] = (
                orig_dt + timedelta(days=random.randint(1, 30))
            ).strftime("%Y-%m-%d")
            self.settlements.append(dup)
            self.ground_truth.append(
                {
                    "entity_type": "SETTLEMENT",
                    "entity_id": dup_id,
                    "issue_type": "CLAIM_PAID_TWICE",
                    "expected_decision": "FLAG_DUPLICATE_PAYMENT",
                    "description": (
                        f"Duplicate payment for claim {sett['claim_id']}, "
                        f"original {sett['settlement_id']}"
                    ),
                    "related_entity_id": sett["settlement_id"],
                    "severity": "CRITICAL",
                }
            )
            count += 1

    # ── Checksum ──────────────────────────────────────────────────────────────

    def checksum(self) -> str:
        # Hash every record so changes in middle rows are detected as well.
        blob = {name: getattr(self, name) for name in self._table_names()}
        return hashlib.sha256(
            json.dumps(blob, sort_keys=True, default=str).encode()
        ).hexdigest()

    def _table_names(self) -> List[str]:
        return [
            "customers", "adjusters", "policies", "coverages", "incidents",
            "claims", "claim_items", "documents", "assessments", "reserves",
            "settlements", "claim_state_history", "ground_truth",
        ]

    # ── Export ────────────────────────────────────────────────────────────────

    def export(self, output_dir: str) -> dict:
        csv_dir = os.path.join(output_dir, "csv")
        json_dir = os.path.join(output_dir, "json")
        sql_dir = os.path.join(output_dir, "sql")
        for d in (csv_dir, json_dir, sql_dir):
            os.makedirs(d, exist_ok=True)

        datasets = {n: getattr(self, n) for n in self._table_names() if n != "ground_truth"}
        manifest: Dict[str, Any] = {
            "generator": "generate.py",
            "generator_version": "1.1.0",
            "python_version": platform.python_version(),
            "seed": self.seed,
            "scale": self.scale,
            "generated_at": datetime.now().isoformat(),
            "checksum": self.checksum(),
            "tables": {},
        }

        for name, data in datasets.items():
            if not data:
                continue
            # CSV
            cp = os.path.join(csv_dir, f"{name}.csv")
            with open(cp, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(data[0].keys()))
                w.writeheader()
                w.writerows(data)
            # JSON
            jp = os.path.join(json_dir, f"{name}.json")
            with open(jp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            # SQL
            sp = os.path.join(sql_dir, f"{name}.sql")
            self._write_sql(sp, name, data)

            manifest["tables"][name] = {
                "row_count": len(data),
                "columns": list(data[0].keys()),
                "csv": f"csv/{name}.csv",
                "json": f"json/{name}.json",
                "sql": f"sql/{name}.sql",
            }

        # Ground truth
        if self.ground_truth:
            for ext, writer in [("csv", self._write_gt_csv), ("json", self._write_gt_json)]:
                writer(os.path.join(output_dir, f"ground_truth.{ext}"))
            manifest["ground_truth"] = {
                "row_count": len(self.ground_truth),
                "csv": "ground_truth.csv",
                "json": "ground_truth.json",
            }

        # Publish an explicit model-input view without post-decision outcomes.
        # Operational claims retain status and reason; this projection does not.
        input_exclusions = {"status", "denial_reason"}
        feature_columns = [c for c in self.claims[0].keys() if c not in input_exclusions]
        feature_dir = os.path.join(output_dir, "features")
        os.makedirs(feature_dir, exist_ok=True)
        feature_path = os.path.join(feature_dir, "claims_input.csv")
        with open(feature_path, "w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=feature_columns)
            writer.writeheader()
            writer.writerows({k: row.get(k) for k in feature_columns} for row in self.claims)
        manifest["claims_input_features"] = {
            "row_count": len(self.claims), "csv": "features/claims_input.csv",
            "excluded_outcome_fields": sorted(input_exclusions), "columns": feature_columns,
        }

        # Hash every emitted table artifact; the manifest timestamp is deliberately
        # excluded so identical datasets produce identical table hashes.
        manifest["file_sha256"] = {}
        for folder in ("csv", "json", "sql", "features"):
            for filename in sorted(os.listdir(os.path.join(output_dir, folder))):
                path = os.path.join(output_dir, folder, filename)
                with open(path, "rb") as stream:
                    manifest["file_sha256"][f"{folder}/{filename}"] = hashlib.sha256(stream.read()).hexdigest()
        for filename in ("ground_truth.csv", "ground_truth.json"):
            path = os.path.join(output_dir, filename)
            if os.path.exists(path):
                with open(path, "rb") as stream:
                    manifest["file_sha256"][filename] = hashlib.sha256(stream.read()).hexdigest()

        # Manifest
        with open(os.path.join(output_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        return manifest

    def _write_sql(self, path: str, table: str, data: list):
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
                        vals.append("TRUE" if v else "FALSE")
                    elif isinstance(v, (int, float)):
                        vals.append(str(v))
                    else:
                        vals.append("'" + str(v).replace("'", "''") + "'")
                f.write(
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(vals)});\n"
                )

    def _write_gt_csv(self, path: str):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(self.ground_truth[0].keys()))
            w.writeheader()
            w.writerows(self.ground_truth)

    def _write_gt_json(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.ground_truth, f, indent=2)

    # ── Main pipeline ─────────────────────────────────────────────────────────

    def generate_all(self) -> str:
        steps = [
            ("Generating customers",    self._gen_customers,    "customers"),
            ("Generating adjusters",    self._gen_adjusters,    "adjusters"),
            ("Generating policies",     self._gen_policies,     "policies"),
            ("Generating coverages",    self._gen_coverages,    "coverages"),
            ("Generating incidents",    self._gen_incidents,    "incidents"),
            ("Generating claims",       self._gen_claims,       "claims"),
            ("Generating claim items",  self._gen_claim_items,  "claim_items"),
            ("Generating documents",    self._gen_documents,    "documents"),
            ("Generating assessments",  self._gen_assessments,  "assessments"),
            ("Generating reserves",     self._gen_reserves,     "reserves"),
            ("Generating settlements",  self._gen_settlements,  "settlements"),
            ("Injecting edge cases",    self._inject_edge_cases, "ground_truth"),
        ]
        for i, (label, fn, attr) in enumerate(steps, 1):
            print(f"  Step {i:2d}: {label}...")
            fn()
            print(f"          -> {len(getattr(self, attr))} records")

        cs = self.checksum()
        print(f"\n  Checksum: {cs}")
        return cs


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description="Insurance Claims Synthetic Data Generator"
    )
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--scale", choices=["starter", "full"], default="full")
    ap.add_argument("--output", default="output")
    ap.add_argument(
        "--verify",
        action="store_true",
        help="Run two generations and compare checksums",
    )
    args = ap.parse_args()

    if args.verify:
        print("=== Reproducibility Verification ===\n")
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
        print(f"  Result: {sym} — {'checksums match' if match else 'MISMATCH'}")
        sys.exit(0 if match else 1)

    print(f"=== Insurance Claims Data Generator ===")
    print(f"    Seed : {args.seed}")
    print(f"    Scale: {args.scale}\n")

    gen = InsuranceDataGenerator(seed=args.seed, scale=args.scale)
    gen.generate_all()

    print(f"\n  Exporting to {os.path.abspath(args.output)} ...")
    manifest = gen.export(args.output)

    print("\n=== Export Summary ===")
    for t, info in manifest.get("tables", {}).items():
        print(f"  {t:25s} {info['row_count']:>6,d} rows")
    if "ground_truth" in manifest:
        print(f"  {'ground_truth':25s} {manifest['ground_truth']['row_count']:>6,d} anomalies")
    print(f"\n  Done!")


if __name__ == "__main__":
    main()
