"""Unified, auditable 14-table claims workflow engine backed by SQLite and the generated CSV data."""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROLES = {"requester", "process_owner", "approver", "operations_analyst", "system_administrator"}
REQUIRED_DOCUMENTS = {
    "AUTO": {"POLICE_REPORT", "PHOTOS", "REPAIR_ESTIMATE", "DRIVERS_LICENSE"},
    "HOME": {"PHOTOS", "INVENTORY_LIST", "REPAIR_ESTIMATE", "POLICE_REPORT"},
    "HEALTH": {"MEDICAL_RECORDS", "BILLS", "REFERRAL", "ID_PROOF"},
    "LIFE": {"DEATH_CERTIFICATE", "MEDICAL_RECORDS", "ID_PROOF", "BENEFICIARY_PROOF"},
    "TRAVEL": {"TRAVEL_ITINERARY", "RECEIPTS", "MEDICAL_RECORDS", "AIRLINE_CONFIRMATION"},
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ClaimsService:
    """Owns workflow decisions across all 14 tables and appends every decision/action
    to both claim_state_history and claim_events for complete auditability."""

    def __init__(self, data_dir: str | Path, db_path: str | Path = ":memory:"):
        self.data_dir = Path(data_dir)
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._schema()
        self._seed_once()

    def close(self) -> None:
        self.db.close()

    def _schema(self) -> None:
        self.db.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS customers(
          customer_id TEXT PRIMARY KEY, first_name TEXT NOT NULL, last_name TEXT NOT NULL,
          date_of_birth TEXT, email TEXT, phone TEXT, address_line1 TEXT, city TEXT,
          state TEXT, zip_code TEXT, risk_tier TEXT DEFAULT 'STANDARD', created_at TEXT, status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS adjusters(
          adjuster_id TEXT PRIMARY KEY, first_name TEXT, last_name TEXT, email TEXT, phone TEXT,
          specializations TEXT NOT NULL, role_tier TEXT DEFAULT 'STANDARD_ADJUSTER',
          license_number TEXT, hire_date TEXT, status TEXT NOT NULL, max_concurrent_claims INTEGER DEFAULT 20
        );
        CREATE TABLE IF NOT EXISTS policies(
          policy_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL,
          product_type TEXT NOT NULL, policy_number TEXT, effective_date TEXT NOT NULL,
          expiration_date TEXT NOT NULL, term_months INTEGER DEFAULT 12, premium_amount REAL DEFAULT 1000,
          payment_frequency TEXT DEFAULT 'ANNUAL', status TEXT NOT NULL,
          cancellation_date TEXT, lapse_date TEXT, underwriting_status TEXT DEFAULT 'APPROVED',
          created_at TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS coverages(
          coverage_id TEXT PRIMARY KEY, policy_id TEXT NOT NULL REFERENCES policies,
          coverage_type TEXT, coverage_limit REAL NOT NULL, deductible REAL NOT NULL,
          co_pay_percentage INTEGER DEFAULT 0, exclusions TEXT,
          effective_date TEXT NOT NULL, expiration_date TEXT NOT NULL, status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS incidents(
          incident_id TEXT PRIMARY KEY, incident_type TEXT NOT NULL,
          incident_date TEXT NOT NULL, incident_time TEXT DEFAULT '12:00:00',
          description TEXT, location_city TEXT, location_state TEXT, severity TEXT DEFAULT 'MEDIUM',
          police_report_filed INTEGER DEFAULT 1, witnesses INTEGER DEFAULT 0,
          product_category TEXT NOT NULL, reported_date TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS claims(
          claim_id TEXT PRIMARY KEY, policy_id TEXT NOT NULL REFERENCES policies,
          customer_id TEXT NOT NULL, incident_id TEXT NOT NULL REFERENCES incidents,
          claim_type TEXT NOT NULL, claim_amount REAL NOT NULL CHECK(claim_amount > 0),
          submission_date TEXT NOT NULL, status TEXT NOT NULL,
          denial_reason TEXT, assigned_adjuster_id TEXT, priority TEXT DEFAULT 'MEDIUM',
          fraud_indicator INTEGER DEFAULT 0, coverage_id TEXT REFERENCES coverages,
          eligible_amount REAL, created_at TEXT NOT NULL, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS claim_items(
          claim_item_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES claims,
          item_description TEXT NOT NULL, item_category TEXT NOT NULL,
          claimed_amount REAL NOT NULL, approved_amount REAL,
          quantity INTEGER NOT NULL, unit_cost REAL NOT NULL, status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS documents(
          document_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES claims,
          document_type TEXT NOT NULL, file_name TEXT, file_size_kb INTEGER DEFAULT 250,
          status TEXT NOT NULL, requested_at TEXT NOT NULL, received_at TEXT,
          uploaded_by TEXT, verification_date TEXT, notes TEXT
        );
        CREATE TABLE IF NOT EXISTS assessments(
          assessment_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES claims,
          adjuster_id TEXT, assessed_amount REAL NOT NULL, deductible REAL NOT NULL,
          co_pay_percentage INTEGER DEFAULT 0, eligible_amount REAL NOT NULL,
          coverage_limit REAL NOT NULL, coverage_verified INTEGER DEFAULT 1,
          liability_percentage INTEGER DEFAULT 100, decision TEXT NOT NULL,
          reason TEXT NOT NULL, fraud_score REAL DEFAULT 0.05, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reserves(
          reserve_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES claims,
          amount REAL NOT NULL, current_reserve REAL NOT NULL, reserve_type TEXT DEFAULT 'CASE',
          set_by TEXT, currency TEXT NOT NULL, status TEXT DEFAULT 'OPEN', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settlements(
          settlement_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES claims,
          assessment_id TEXT, amount REAL NOT NULL, payment_method TEXT DEFAULT 'ACH',
          payment_date TEXT, status TEXT NOT NULL, approved_by TEXT NOT NULL,
          payee_name TEXT, payee_customer_id TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS claim_adjuster_assignments(
          assignment_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES claims,
          adjuster_id TEXT NOT NULL REFERENCES adjusters, assignment_role TEXT NOT NULL,
          assigned_date TEXT NOT NULL, unassigned_date TEXT, is_active INTEGER DEFAULT 1,
          workload_weight REAL DEFAULT 1.0
        );
        CREATE TABLE IF NOT EXISTS claim_coverage_allocations(
          allocation_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES claims,
          coverage_id TEXT NOT NULL REFERENCES coverages, is_primary INTEGER DEFAULT 1,
          allocated_claimed_amount REAL NOT NULL, allocated_eligible_amount REAL NOT NULL,
          deductible_applied REAL NOT NULL, effective_date TEXT NOT NULL, expiration_date TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS claim_state_history(
          event_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES claims,
          from_status TEXT, to_status TEXT NOT NULL, changed_by TEXT NOT NULL,
          change_reason TEXT NOT NULL, event_timestamp TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS claim_events(
          event_id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT NOT NULL REFERENCES claims,
          actor_role TEXT NOT NULL, actor_id TEXT NOT NULL, action TEXT NOT NULL,
          from_status TEXT, to_status TEXT, reason TEXT NOT NULL, payload TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        self.db.commit()

    def _seed_once(self) -> None:
        if self.db.execute("SELECT 1 FROM metadata WHERE key='seeded'").fetchone():
            return

        for row in _read_csv(self.data_dir / "csv" / "customers.csv"):
            self.db.execute(
                "INSERT OR IGNORE INTO customers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(
                    row.get(k) or None
                    for k in (
                        "customer_id", "first_name", "last_name", "date_of_birth",
                        "email", "phone", "address_line1", "city", "state",
                        "zip_code", "risk_tier", "created_at", "status",
                    )
                ),
            )

        for row in _read_csv(self.data_dir / "csv" / "adjusters.csv"):
            self.db.execute(
                "INSERT OR IGNORE INTO adjusters VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["adjuster_id"], row.get("first_name"), row.get("last_name"),
                    row.get("email"), row.get("phone"), row.get("specializations", ""),
                    row.get("role_tier", "STANDARD_ADJUSTER"), row.get("license_number"),
                    row.get("hire_date"), row.get("status", "ACTIVE"),
                    int(row.get("max_concurrent_claims") or 20),
                ),
            )

        for row in _read_csv(self.data_dir / "csv" / "policies.csv"):
            self.db.execute(
                "INSERT OR IGNORE INTO policies VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(
                    row.get(k) or None
                    for k in (
                        "policy_id", "customer_id", "product_type", "policy_number",
                        "effective_date", "expiration_date", "term_months", "premium_amount",
                        "payment_frequency", "status", "cancellation_date", "lapse_date",
                        "underwriting_status", "created_at", "updated_at",
                    )
                ),
            )

        for row in _read_csv(self.data_dir / "csv" / "coverages.csv"):
            self.db.execute(
                "INSERT OR IGNORE INTO coverages VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    row["coverage_id"], row["policy_id"], row.get("coverage_type"),
                    float(row["coverage_limit"]), float(row["deductible"]),
                    int(row.get("co_pay_percentage") or 0), row.get("exclusions"),
                    row["effective_date"], row["expiration_date"], row["status"],
                ),
            )

        for row in _read_csv(self.data_dir / "csv" / "incidents.csv"):
            self.db.execute(
                "INSERT OR IGNORE INTO incidents VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["incident_id"], row["incident_type"], row["incident_date"],
                    row.get("incident_time", "12:00:00"), row.get("description"),
                    row.get("location_city"), row.get("location_state"),
                    row.get("severity", "MEDIUM"),
                    1 if str(row.get("police_report_filed")).lower() in ("true", "1") else 0,
                    int(row.get("witnesses") or 0), row["product_category"], row["reported_date"],
                ),
            )

        asn_by_claim = {
            r["claim_id"]: r for r in _read_csv(self.data_dir / "csv" / "assessments.csv")
        }
        for row in _read_csv(self.data_dir / "csv" / "claims.csv"):
            cid = row["claim_id"]
            elig = float(asn_by_claim[cid]["eligible_amount"]) if cid in asn_by_claim else None
            self.db.execute(
                "INSERT OR IGNORE INTO claims VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    cid, row["policy_id"], row["customer_id"], row["incident_id"],
                    row["claim_type"], float(row["claim_amount"]), row["submission_date"],
                    row["status"], row.get("denial_reason") or None,
                    row.get("assigned_adjuster_id") or None, row.get("priority", "MEDIUM"),
                    1 if str(row.get("fraud_indicator")).lower() in ("true", "1") else 0,
                    row.get("coverage_id") or None, elig,
                    row["created_at"], row.get("updated_at") or row["created_at"],
                ),
            )

        for row in _read_csv(self.data_dir / "csv" / "claim_items.csv"):
            app_amt = float(row["approved_amount"]) if row.get("approved_amount") not in (None, "") else None
            self.db.execute(
                "INSERT OR IGNORE INTO claim_items VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    row["claim_item_id"], row["claim_id"], row["item_description"],
                    row["item_category"], float(row["claimed_amount"]), app_amt,
                    int(row["quantity"]), float(row["unit_cost"]), row["status"],
                ),
            )

        for row in _read_csv(self.data_dir / "csv" / "documents.csv"):
            source_status = (row.get("status") or "RECEIVED").upper()
            doc_status = "RECEIVED" if source_status in {"RECEIVED", "VERIFIED", "APPROVED"} else "REQUESTED"
            uploaded_at = row.get("upload_date") or _now()
            self.db.execute(
                "INSERT OR IGNORE INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["document_id"], row["claim_id"], row.get("document_type", "OTHER"),
                    row.get("file_name"), int(row.get("file_size_kb") or 250),
                    doc_status, uploaded_at, uploaded_at if doc_status == "RECEIVED" else None,
                    row.get("uploaded_by"), row.get("verification_date"), row.get("notes"),
                ),
            )

        for row in _read_csv(self.data_dir / "csv" / "assessments.csv"):
            self.db.execute(
                "INSERT OR IGNORE INTO assessments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["assessment_id"], row["claim_id"], row.get("adjuster_id"),
                    float(row["assessed_amount"]), float(row["deductible_applied"]),
                    int(row.get("co_pay_percentage") or 0), float(row["eligible_amount"]),
                    float(row["coverage_limit"]),
                    1 if str(row.get("coverage_verified")).lower() in ("true", "1") else 0,
                    int(row.get("liability_percentage") or 100), row["recommendation"],
                    row.get("notes", ""), float(row.get("fraud_score") or 0.05),
                    row["assessment_date"],
                ),
            )

        for row in _read_csv(self.data_dir / "csv" / "reserves.csv"):
            self.db.execute(
                "INSERT OR IGNORE INTO reserves VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    row["reserve_id"], row["claim_id"], float(row["initial_reserve"]),
                    float(row["current_reserve"]), row.get("reserve_type", "CASE"),
                    row.get("set_by"), row.get("currency", "USD"),
                    row.get("status", "OPEN"), row["set_date"],
                ),
            )

        for row in _read_csv(self.data_dir / "csv" / "settlements.csv"):
            self.db.execute(
                "INSERT OR IGNORE INTO settlements VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["settlement_id"], row["claim_id"], row.get("assessment_id"),
                    float(row["settlement_amount"]), row.get("payment_method", "ACH"),
                    row.get("payment_date"), row["status"], row["approved_by"],
                    row.get("payee_name"), row.get("payee_customer_id"), row["approved_date"],
                ),
            )

        for row in _read_csv(self.data_dir / "csv" / "claim_adjuster_assignments.csv"):
            self.db.execute(
                "INSERT OR IGNORE INTO claim_adjuster_assignments VALUES(?,?,?,?,?,?,?,?)",
                (
                    row["assignment_id"], row["claim_id"], row["adjuster_id"],
                    row["assignment_role"], row["assigned_date"], row.get("unassigned_date") or None,
                    1 if str(row.get("is_active")).lower() in ("true", "1") else 0,
                    float(row.get("workload_weight") or 1.0),
                ),
            )

        for row in _read_csv(self.data_dir / "csv" / "claim_coverage_allocations.csv"):
            self.db.execute(
                "INSERT OR IGNORE INTO claim_coverage_allocations VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    row["allocation_id"], row["claim_id"], row["coverage_id"],
                    1 if str(row.get("is_primary")).lower() in ("true", "1") else 0,
                    float(row["allocated_claimed_amount"]), float(row["allocated_eligible_amount"]),
                    float(row["deductible_applied"]), row["effective_date"], row["expiration_date"],
                ),
            )

        for row in _read_csv(self.data_dir / "csv" / "claim_state_history.csv"):
            self.db.execute(
                "INSERT OR IGNORE INTO claim_state_history VALUES(?,?,?,?,?,?,?)",
                (
                    row["event_id"], row["claim_id"], row.get("from_status") or None,
                    row["to_status"], row["changed_by"], row["change_reason"],
                    row["event_timestamp"],
                ),
            )
            self.db.execute(
                "INSERT INTO claim_events(claim_id,actor_role,actor_id,action,from_status,to_status,reason,payload,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    row["claim_id"],
                    "system_administrator" if row["changed_by"] == "SYSTEM" else "operations_analyst",
                    row["changed_by"],
                    f"STATE_{row['to_status']}",
                    row.get("from_status") or None,
                    row["to_status"],
                    row["change_reason"],
                    "{}",
                    row["event_timestamp"],
                ),
            )

        self.db.execute("INSERT INTO metadata VALUES('seeded','1')")
        self.db.commit()

    def _actor(self, role: str, allowed: set[str]) -> None:
        if role not in ROLES or role not in allowed:
            raise PermissionError(f"Role {role!r} is not allowed for this action")

    def _event(
        self,
        claim_id: str,
        role: str,
        actor_id: str,
        action: str,
        old: str | None,
        new: str | None,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        ts = _now()
        self.db.execute(
            "INSERT INTO claim_events(claim_id,actor_role,actor_id,action,from_status,to_status,reason,payload,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (claim_id, role, actor_id, action, old, new, reason, json.dumps(payload or {}, sort_keys=True), ts),
        )
        seq = self.db.execute(
            "SELECT COUNT(*) n FROM claim_state_history WHERE claim_id=?", (claim_id,)
        ).fetchone()["n"] + 1
        self.db.execute(
            "INSERT INTO claim_state_history VALUES(?,?,?,?,?,?,?)",
            (f"EVT-{claim_id}-{seq:03d}", claim_id, old, new or old or "SUBMITTED", actor_id, reason, ts),
        )

    def _claim(self, claim_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown claim: {claim_id}")
        return row

    def list_claims(
        self,
        limit: int = 100,
        status: str | None = None,
        claim_type: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM claims WHERE 1=1"
        params: list[Any] = []
        if status:
            query += " AND status=?"
            params.append(status)
        if claim_type:
            query += " AND claim_type=?"
            params.append(claim_type)
        query += " ORDER BY created_at DESC, claim_id LIMIT ?"
        params.append(limit)
        rows = self.db.execute(query, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def reference_pairs(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """SELECT p.policy_id,i.incident_id,p.product_type
            FROM policies p JOIN incidents i ON i.product_category=p.product_type
            WHERE p.status='ACTIVE' AND p.effective_date<=i.incident_date
              AND p.expiration_date>=i.incident_date
              AND EXISTS(SELECT 1 FROM coverages v WHERE v.policy_id=p.policy_id AND v.status='ACTIVE'
                         AND v.effective_date<=i.incident_date AND v.expiration_date>=i.incident_date)
              AND NOT EXISTS(SELECT 1 FROM claims c WHERE c.policy_id=p.policy_id AND c.incident_id=i.incident_id)
            LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_claim(self, claim_id: str) -> dict[str, Any]:
        claim = dict(self._claim(claim_id))
        for name in (
            "claim_items",
            "documents",
            "assessments",
            "reserves",
            "settlements",
            "claim_adjuster_assignments",
            "claim_coverage_allocations",
            "claim_state_history",
            "claim_events",
        ):
            claim[name] = [
                dict(r)
                for r in self.db.execute(f"SELECT * FROM {name} WHERE claim_id=? ORDER BY rowid", (claim_id,))
            ]
        pol = self.db.execute("SELECT * FROM policies WHERE policy_id=?", (claim["policy_id"],)).fetchone()
        cust = self.db.execute("SELECT * FROM customers WHERE customer_id=?", (claim["customer_id"],)).fetchone()
        inc = self.db.execute("SELECT * FROM incidents WHERE incident_id=?", (claim["incident_id"],)).fetchone()
        claim["policy"] = dict(pol) if pol else None
        claim["customer"] = dict(cust) if cust else None
        claim["incident"] = dict(inc) if inc else None
        return claim

    def intake(
        self,
        policy_id: str,
        incident_id: str,
        claim_amount: float,
        role: str = "requester",
        actor_id: str = "requester",
        claim_type: str | None = None,
    ) -> dict[str, Any]:
        self._actor(role, {"requester", "system_administrator"})
        if claim_amount <= 0:
            raise ValueError("claim_amount must be positive")
        pol = self.db.execute("SELECT * FROM policies WHERE policy_id=?", (policy_id,)).fetchone()
        inc = self.db.execute("SELECT * FROM incidents WHERE incident_id=?", (incident_id,)).fetchone()
        if pol is None or inc is None:
            raise ValueError("policy_id and incident_id must reference existing records")
        if claim_type and claim_type != pol["product_type"]:
            raise ValueError("claim_type must match the policy product")
        typ = pol["product_type"]
        duplicate = self.db.execute(
            "SELECT claim_id FROM claims WHERE policy_id=? AND incident_id=?", (policy_id, incident_id)
        ).fetchone()
        if duplicate:
            raise ValueError(f"Duplicate claim for this policy and incident: {duplicate['claim_id']}")
        cover = self.db.execute(
            "SELECT * FROM coverages WHERE policy_id=? AND status='ACTIVE' ORDER BY coverage_limit DESC LIMIT 1",
            (policy_id,),
        ).fetchone()
        now = _now()
        seq = self.db.execute("SELECT COUNT(*) n FROM claims WHERE claim_id LIKE 'CLM-2026-%'").fetchone()["n"] + 1
        claim_id = f"CLM-2026-{seq:06d}"
        reason = "Coverage validated"
        status = "UNDER_REVIEW"
        if not cover:
            status, reason = "DENIED", "No active coverage"
        elif not (cover["effective_date"] <= inc["incident_date"] <= cover["expiration_date"]):
            status, reason = "DENIED", "Incident date is outside coverage effective dates"
        elif pol["status"] == "CANCELLED" and pol["cancellation_date"] and inc["incident_date"] > pol["cancellation_date"]:
            status, reason = "DENIED", "Policy cancelled before incident"
        elif pol["status"] == "LAPSED" and pol["lapse_date"] and inc["incident_date"] > pol["lapse_date"]:
            status, reason = "DENIED", "Policy lapsed before incident"
        elif pol["status"] not in {"ACTIVE", "CANCELLED", "LAPSED"}:
            status, reason = "DENIED", f"Policy is not in force ({pol['status']})"
        elif not (pol["effective_date"] <= inc["incident_date"] <= pol["expiration_date"]):
            status, reason = "DENIED", "Incident date is outside policy effective dates"
        elif cover and cover["exclusions"] and cover["exclusions"].strip().upper() not in {"NONE", "N/A"}:
            terms = {t.strip().upper() for t in cover["exclusions"].split(",") if t.strip()}
            if inc["incident_type"].upper() in terms or "ALL" in terms:
                status, reason = "DENIED", "Incident type is excluded by coverage"

        cust = pol["customer_id"]
        amt = round(float(claim_amount), 2)
        self.db.execute(
            "INSERT INTO claims(claim_id,policy_id,customer_id,incident_id,claim_type,claim_amount,submission_date,status,denial_reason,coverage_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                claim_id, policy_id, cust, incident_id, typ, amt,
                date.today().isoformat(), status,
                reason if status == "DENIED" else None,
                cover["coverage_id"] if cover else None, now, now,
            ),
        )
        # Create matching ClaimItem so bottom-up item reconciliation holds!
        self.db.execute(
            "INSERT INTO claim_items VALUES(?,?,?,?,?,?,?,?,?)",
            (
                f"CLI-{claim_id}-001", claim_id, f"Primary {typ} loss item", typ,
                amt, 0.0 if status == "DENIED" else None, 1, amt,
                "DENIED" if status == "DENIED" else "PENDING",
            ),
        )
        if cover:
            self.db.execute(
                "INSERT INTO claim_coverage_allocations VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    f"CCA-{claim_id}-001", claim_id, cover["coverage_id"], 1,
                    amt, 0.0, float(cover["deductible"]), cover["effective_date"], cover["expiration_date"],
                ),
            )
        missing = REQUIRED_DOCUMENTS.get(typ, set())
        if status != "DENIED":
            status = "PENDING_DOCUMENTS"
            self.db.execute("UPDATE claims SET status=? WHERE claim_id=?", (status, claim_id))
            for i, doc_type in enumerate(sorted(missing), 1):
                self.db.execute(
                    "INSERT INTO documents VALUES(?,?,?,?,?,?,?,NULL,?,NULL,?)",
                    (
                        f"DOC-{claim_id}-{i:03d}", claim_id, doc_type,
                        f"{doc_type.lower()}_{claim_id}.pdf", 250, "REQUESTED",
                        now, cust, f"Requested at intake for {typ} claim",
                    ),
                )
            reason = f"Coverage valid; requested {len(missing)} required documents"
        self._event(
            claim_id, role, actor_id, "CLAIM_INTAKE", None, status, reason,
            {"policy_id": policy_id, "incident_id": incident_id, "coverage_id": cover["coverage_id"] if cover else None},
        )
        self.db.commit()
        return self.get_claim(claim_id)

    def receive_document(
        self,
        claim_id: str,
        document_type: str,
        role: str = "requester",
        actor_id: str = "requester",
    ) -> dict[str, Any]:
        self._actor(role, {"requester", "operations_analyst", "system_administrator"})
        claim = self._claim(claim_id)
        now = _now()
        row = self.db.execute(
            "SELECT document_id FROM documents WHERE claim_id=? AND document_type=? AND status='REQUESTED' ORDER BY document_id LIMIT 1",
            (claim_id, document_type),
        ).fetchone()
        if row:
            self.db.execute(
                "UPDATE documents SET status='RECEIVED',received_at=?,verification_date=? WHERE document_id=?",
                (now, date.today().isoformat(), row["document_id"]),
            )
        else:
            seq = self.db.execute("SELECT COUNT(*) n FROM documents WHERE claim_id=?", (claim_id,)).fetchone()["n"] + 1
            self.db.execute(
                "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"DOC-{claim_id}-{seq:03d}", claim_id, document_type,
                    f"{document_type.lower()}_{claim_id}.pdf", 320, "RECEIVED",
                    now, now, claim["customer_id"], date.today().isoformat(), "Uploaded by claimant",
                ),
            )
        remaining = self.db.execute(
            "SELECT COUNT(*) n FROM documents WHERE claim_id=? AND status!='RECEIVED'", (claim_id,)
        ).fetchone()["n"]
        new = "PENDING_DOCUMENTS" if remaining else "UNDER_REVIEW"
        self.db.execute("UPDATE claims SET status=?,updated_at=? WHERE claim_id=?", (new, now, claim_id))
        self._event(
            claim_id, role, actor_id, "DOCUMENT_RECEIVED", claim["status"], new,
            f"Received {document_type}; {remaining} required items remain", {"document_type": document_type},
        )
        self.db.commit()
        return self.get_claim(claim_id)

    def request_documents(
        self, claim_id: str, role: str = "operations_analyst", actor_id: str = "analyst"
    ) -> dict[str, Any]:
        self._actor(role, {"operations_analyst", "system_administrator"})
        claim = self._claim(claim_id)
        missing = self.db.execute(
            "SELECT document_type FROM documents WHERE claim_id=? AND status!='RECEIVED' ORDER BY document_type",
            (claim_id,),
        ).fetchall()
        if not missing:
            raise ValueError("There are no outstanding documents to request")
        for row in missing:
            self._event(
                claim_id, role, actor_id, "DOCUMENT_REQUESTED", claim["status"], claim["status"],
                f"Requested {row['document_type']}", {"document_type": row["document_type"]},
            )
        self.db.commit()
        return self.get_claim(claim_id)

    def assign_adjuster(
        self,
        claim_id: str,
        adjuster_id: str,
        role: str = "operations_analyst",
        actor_id: str = "analyst",
        assignment_role: str = "PRIMARY_ADJUSTER",
    ) -> dict[str, Any]:
        self._actor(role, {"operations_analyst", "system_administrator"})
        claim = self._claim(claim_id)
        adj = self.db.execute(
            "SELECT * FROM adjusters WHERE adjuster_id=? AND status='ACTIVE'", (adjuster_id,)
        ).fetchone()
        if not adj:
            raise ValueError("Adjuster is not active or does not exist")
        if claim["claim_type"] not in adj["specializations"].upper() and assignment_role == "PRIMARY_ADJUSTER":
            raise ValueError("Adjuster is not specialized for this claim type")
        if assignment_role == "PRIMARY_ADJUSTER":
            self.db.execute(
                "UPDATE claims SET assigned_adjuster_id=?,updated_at=? WHERE claim_id=?",
                (adjuster_id, _now(), claim_id),
            )
        seq = self.db.execute(
            "SELECT COUNT(*) n FROM claim_adjuster_assignments WHERE claim_id=?", (claim_id,)
        ).fetchone()["n"] + 1
        self.db.execute(
            "INSERT INTO claim_adjuster_assignments VALUES(?,?,?,?,?,NULL,1,1.0)",
            (f"CAA-{claim_id}-{seq:03d}", claim_id, adjuster_id, assignment_role, date.today().isoformat()),
        )
        self._event(
            claim_id, role, actor_id, "ADJUSTER_ASSIGNED", claim["status"], claim["status"],
            f"Assigned {adjuster_id} ({assignment_role})",
            {"adjuster_id": adjuster_id, "assignment_role": assignment_role},
        )
        self.db.commit()
        return self.get_claim(claim_id)

    def escalate_siu(
        self,
        claim_id: str,
        reason: str = "Suspected fraud indicator flagged for SIU investigation",
        role: str = "operations_analyst",
        actor_id: str = "analyst",
    ) -> dict[str, Any]:
        self._actor(role, {"operations_analyst", "approver", "system_administrator"})
        claim = self._claim(claim_id)
        now = _now()
        self.db.execute(
            "UPDATE claims SET status='SIU_INVESTIGATION',fraud_indicator=1,priority='URGENT',updated_at=? WHERE claim_id=?",
            (now, claim_id),
        )
        self._event(
            claim_id, role, actor_id, "SIU_ESCALATION", claim["status"], "SIU_INVESTIGATION",
            reason, {"fraud_indicator": True},
        )
        self.db.commit()
        return self.get_claim(claim_id)

    def assess(
        self, claim_id: str, role: str = "operations_analyst", actor_id: str = "analyst"
    ) -> dict[str, Any]:
        self._actor(role, {"operations_analyst", "system_administrator"})
        claim = self._claim(claim_id)
        if claim["status"] not in {"UNDER_REVIEW", "PENDING_DOCUMENTS", "SIU_INVESTIGATION"}:
            raise ValueError("Claim must be under review before assessment")
        pending = self.db.execute(
            "SELECT document_type FROM documents WHERE claim_id=? AND status!='RECEIVED'", (claim_id,)
        ).fetchall()
        if pending:
            raise ValueError("Required documents are still missing: " + ", ".join(r[0] for r in pending))
        cov = self.db.execute(
            "SELECT * FROM coverages WHERE coverage_id=?", (claim["coverage_id"],)
        ).fetchone()
        if cov is None:
            raise ValueError("Claim has no applicable coverage")
        amount = min(max(0.0, round(claim["claim_amount"] - cov["deductible"], 2)), cov["coverage_limit"])
        assessment_id = f"ASN-{claim_id}-001"
        reserve_id = f"RSV-{claim_id}-001"
        now = _now()
        self.db.execute(
            "UPDATE claim_items SET approved_amount=claimed_amount,status=? WHERE claim_id=?",
            ("APPROVED" if amount > 0 else "DENIED", claim_id),
        )
        self.db.execute(
            "UPDATE claim_coverage_allocations SET allocated_eligible_amount=? WHERE claim_id=? AND is_primary=1",
            (amount, claim_id),
        )
        self.db.execute(
            "INSERT OR REPLACE INTO assessments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                assessment_id, claim_id, claim["assigned_adjuster_id"],
                claim["claim_amount"], cov["deductible"], int(cov["co_pay_percentage"] or 0),
                amount, cov["coverage_limit"], 1, 100,
                "RECOMMEND_APPROVAL" if amount else "DENY",
                "Eligible amount is claim less deductible, capped by coverage limit",
                0.05, now,
            ),
        )
        self.db.execute(
            "INSERT OR REPLACE INTO reserves VALUES(?,?,?,?,?,?,?,?,?)",
            (
                reserve_id, claim_id, round(claim["claim_amount"], 2),
                round(amount, 2), "CASE", claim["assigned_adjuster_id"], "USD", "OPEN", now,
            ),
        )
        new = "APPROVAL_PENDING" if amount > 0 else "DENIED"
        reason = "Assessment complete; awaiting approver" if amount > 0 else "Eligible amount is zero after deductible"
        self.db.execute(
            "UPDATE claims SET status=?,eligible_amount=?,updated_at=? WHERE claim_id=?",
            (new, amount, now, claim_id),
        )
        self._event(
            claim_id, role, actor_id, "ASSESSMENT", claim["status"], new, reason,
            {"eligible_amount": amount, "coverage_limit": cov["coverage_limit"], "deductible": cov["deductible"]},
        )
        self.db.commit()
        return self.get_claim(claim_id)

    def approve(
        self, claim_id: str, decision: str, role: str = "approver", actor_id: str = "approver"
    ) -> dict[str, Any]:
        self._actor(role, {"approver", "system_administrator"})
        claim = self._claim(claim_id)
        decision = decision.upper()
        if claim["status"] not in {"APPROVAL_PENDING", "UNDER_REVIEW"}:
            raise ValueError("Claim must have a completed assessment before approval")
        now = _now()
        if decision == "APPROVE":
            amount = claim["eligible_amount"] or 0.0
            cust = self.db.execute(
                "SELECT first_name,last_name FROM customers WHERE customer_id=?", (claim["customer_id"],)
            ).fetchone()
            payee_name = f"{cust['first_name']} {cust['last_name']}" if cust else claim["customer_id"]
            self.db.execute(
                "INSERT INTO settlements VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"SET-{claim_id}-001", claim_id, f"ASN-{claim_id}-001",
                    amount, "ACH", None, "APPROVED", actor_id,
                    payee_name, claim["customer_id"], now,
                ),
            )
            new, reason = "SETTLEMENT_APPROVED", f"Approved settlement of {amount:.2f} USD within coverage limit"
        elif decision == "REJECT":
            self.db.execute(
                "UPDATE claims SET denial_reason=? WHERE claim_id=?", ("APPROVER_REJECTED", claim_id)
            )
            self.db.execute("UPDATE reserves SET status='CLOSED',current_reserve=0 WHERE claim_id=?", (claim_id,))
            new, reason = "DENIED", "Approver rejected the assessment"
        else:
            raise ValueError("decision must be APPROVE or REJECT")
        self.db.execute("UPDATE claims SET status=?,updated_at=? WHERE claim_id=?", (new, now, claim_id))
        self._event(
            claim_id, role, actor_id, "APPROVER_DECISION", claim["status"], new, reason, {"decision": decision}
        )
        self.db.commit()
        return self.get_claim(claim_id)

    def pay(
        self, claim_id: str, role: str = "system_administrator", actor_id: str = "payment_system"
    ) -> dict[str, Any]:
        self._actor(role, {"system_administrator"})
        claim = self._claim(claim_id)
        sett = self.db.execute(
            "SELECT * FROM settlements WHERE claim_id=? AND status='APPROVED'", (claim_id,)
        ).fetchone()
        if not sett:
            raise ValueError("Only an approved settlement can be paid")
        now = _now()
        self.db.execute(
            "UPDATE settlements SET status='PAID',payment_date=? WHERE settlement_id=?",
            (date.today().isoformat(), sett["settlement_id"]),
        )
        self.db.execute("UPDATE reserves SET status='CLOSED',current_reserve=0 WHERE claim_id=?", (claim_id,))
        self.db.execute("UPDATE claims SET status='PAID',updated_at=? WHERE claim_id=?", (now, claim_id))
        self._event(
            claim_id, role, actor_id, "PAYMENT", claim["status"], "PAID",
            f"Paid settlement {sett['settlement_id']}", {"amount": sett["amount"]},
        )
        self.db.commit()
        return self.get_claim(claim_id)

    def metrics(self) -> dict[str, Any]:
        total = self.db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        statuses = {
            r[0]: r[1] for r in self.db.execute("SELECT status,COUNT(*) FROM claims GROUP BY status")
        }
        received: dict[str, set[str]] = {}
        for row in self.db.execute(
            "SELECT claim_id,document_type FROM documents WHERE status='RECEIVED'"
        ).fetchall():
            received.setdefault(row["claim_id"], set()).add(row["document_type"])
        complete = 0
        for claim in self.db.execute("SELECT claim_id,claim_type FROM claims").fetchall():
            present = received.get(claim["claim_id"], set())
            required = REQUIRED_DOCUMENTS.get(claim["claim_type"], set())
            complete += int(bool(required) and required.issubset(present))
        paid = self.db.execute("SELECT COUNT(*) FROM settlements WHERE status='PAID'").fetchone()[0]
        within = self.db.execute(
            "SELECT COUNT(*) FROM settlements s JOIN claims c ON c.claim_id=s.claim_id WHERE s.status='PAID' AND s.amount<=COALESCE((SELECT coverage_limit FROM coverages WHERE coverage_id=c.coverage_id),0)"
        ).fetchone()[0]
        events = self.db.execute("SELECT COUNT(*) FROM claim_events").fetchone()[0]
        bridge_assigns = self.db.execute("SELECT COUNT(*) FROM claim_adjuster_assignments").fetchone()[0]
        bridge_allocs = self.db.execute("SELECT COUNT(*) FROM claim_coverage_allocations").fetchone()[0]
        return {
            "total_claims": total,
            "status_counts": statuses,
            "document_completeness_rate": round(complete / total, 4) if total else 0,
            "paid_settlements": paid,
            "settlement_reconciliation_rate": round(within / paid, 4) if paid else 0,
            "claims_with_audit_events": self.db.execute(
                "SELECT COUNT(DISTINCT claim_id) FROM claim_events"
            ).fetchone()[0],
            "audit_events": events,
            "adjuster_bridge_assignments": bridge_assigns,
            "coverage_bridge_allocations": bridge_allocs,
        }

    def rules(self) -> dict[str, Any]:
        return {
            "required_documents": {k: sorted(v) for k, v in REQUIRED_DOCUMENTS.items()},
            "coverage": "incident must be within coverage dates; policy in force; excluded incidents denied",
            "eligibility": "max(0, min(claim amount - deductible, coverage limit))",
            "bottom_up_reconciliation": "claim_amount == sum(claim_items.claimed_amount); assessed_amount == sum(claim_items.approved_amount)",
            "roles": sorted(ROLES),
        }
