"""Small, auditable claims workflow backed by SQLite and the generated CSV data."""
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
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ClaimsService:
    """Owns workflow decisions and appends every decision/action to an event log."""

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
        CREATE TABLE IF NOT EXISTS policies(policy_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL,
          product_type TEXT NOT NULL, effective_date TEXT NOT NULL, expiration_date TEXT NOT NULL,
          status TEXT NOT NULL, cancellation_date TEXT, lapse_date TEXT);
        CREATE TABLE IF NOT EXISTS coverages(coverage_id TEXT PRIMARY KEY, policy_id TEXT NOT NULL REFERENCES policies,
          coverage_type TEXT, coverage_limit REAL NOT NULL, deductible REAL NOT NULL, exclusions TEXT,
          effective_date TEXT NOT NULL, expiration_date TEXT NOT NULL, status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS incidents(incident_id TEXT PRIMARY KEY, incident_type TEXT NOT NULL,
          incident_date TEXT NOT NULL, product_category TEXT NOT NULL, reported_date TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS adjusters(adjuster_id TEXT PRIMARY KEY, specializations TEXT NOT NULL, status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS claims(claim_id TEXT PRIMARY KEY, policy_id TEXT NOT NULL REFERENCES policies,
          customer_id TEXT NOT NULL, incident_id TEXT NOT NULL REFERENCES incidents, claim_type TEXT NOT NULL,
          claim_amount REAL NOT NULL CHECK(claim_amount > 0), submission_date TEXT NOT NULL, status TEXT NOT NULL,
          denial_reason TEXT, assigned_adjuster_id TEXT, coverage_id TEXT REFERENCES coverages,
          eligible_amount REAL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS documents(document_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES claims,
          document_type TEXT NOT NULL, status TEXT NOT NULL, requested_at TEXT NOT NULL, received_at TEXT);
        CREATE TABLE IF NOT EXISTS assessments(assessment_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES claims,
          adjuster_id TEXT, assessed_amount REAL NOT NULL, deductible REAL NOT NULL, eligible_amount REAL NOT NULL,
          coverage_limit REAL NOT NULL, decision TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reserves(reserve_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES claims,
          amount REAL NOT NULL, currency TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settlements(settlement_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES claims,
          amount REAL NOT NULL, status TEXT NOT NULL, approved_by TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS claim_events(event_id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT NOT NULL REFERENCES claims,
          actor_role TEXT NOT NULL, actor_id TEXT NOT NULL, action TEXT NOT NULL, from_status TEXT, to_status TEXT,
          reason TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        self.db.commit()

    def _seed_once(self) -> None:
        if self.db.execute("SELECT 1 FROM metadata WHERE key='seeded'").fetchone():
            return
        for row in _read_csv(self.data_dir / "csv" / "policies.csv"):
            self.db.execute("INSERT OR IGNORE INTO policies VALUES(?,?,?,?,?,?,?,?)", tuple(row.get(k) or None for k in
                ("policy_id", "customer_id", "product_type", "effective_date", "expiration_date", "status", "cancellation_date", "lapse_date")))
        for row in _read_csv(self.data_dir / "csv" / "coverages.csv"):
            self.db.execute("INSERT OR IGNORE INTO coverages VALUES(?,?,?,?,?,?,?,?,?)", tuple(row.get(k) or None for k in
                ("coverage_id", "policy_id", "coverage_type", "coverage_limit", "deductible", "exclusions", "effective_date", "expiration_date", "status")))
        for row in _read_csv(self.data_dir / "csv" / "incidents.csv"):
            self.db.execute("INSERT OR IGNORE INTO incidents VALUES(?,?,?,?,?)", tuple(row.get(k) for k in
                ("incident_id", "incident_type", "incident_date", "product_category", "reported_date")))
        for row in _read_csv(self.data_dir / "csv" / "adjusters.csv"):
            self.db.execute("INSERT OR IGNORE INTO adjusters VALUES(?,?,?)", (row["adjuster_id"], row.get("specializations", ""), row.get("status", "ACTIVE")))
        for row in _read_csv(self.data_dir / "csv" / "claims.csv"):
            self.db.execute("INSERT OR IGNORE INTO claims(claim_id,policy_id,customer_id,incident_id,claim_type,claim_amount,submission_date,status,denial_reason,assigned_adjuster_id,coverage_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(row.get(k) or None for k in ("claim_id", "policy_id", "customer_id", "incident_id", "claim_type", "claim_amount", "submission_date", "status", "denial_reason", "assigned_adjuster_id", "coverage_id", "created_at")))
        for row in _read_csv(self.data_dir / "csv" / "documents.csv"):
            source_status = (row.get("status") or "RECEIVED").upper()
            doc_status = "RECEIVED" if source_status in {"RECEIVED", "VERIFIED", "APPROVED"} else "REQUESTED"
            uploaded_at = row.get("upload_date") or _now()
            self.db.execute("INSERT OR IGNORE INTO documents VALUES(?,?,?,?,?,?)", (row["document_id"], row["claim_id"], row.get("document_type", "OTHER"), doc_status, uploaded_at, uploaded_at if doc_status == "RECEIVED" else None))
        self.db.execute("INSERT INTO metadata VALUES('seeded','1')")
        self.db.commit()

    def _actor(self, role: str, allowed: set[str]) -> None:
        if role not in ROLES or role not in allowed:
            raise PermissionError(f"Role {role!r} is not allowed for this action")

    def _event(self, claim_id: str, role: str, actor_id: str, action: str, old: str | None,
               new: str | None, reason: str, payload: dict[str, Any] | None = None) -> None:
        self.db.execute("INSERT INTO claim_events(claim_id,actor_role,actor_id,action,from_status,to_status,reason,payload,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (claim_id, role, actor_id, action, old, new, reason, json.dumps(payload or {}, sort_keys=True), _now()))

    def _claim(self, claim_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown claim: {claim_id}")
        return row

    def list_claims(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM claims ORDER BY created_at DESC, claim_id LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def reference_pairs(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.execute("""SELECT p.policy_id,i.incident_id,p.product_type
            FROM policies p JOIN incidents i ON i.product_category=p.product_type
            WHERE p.status='ACTIVE' AND p.effective_date<=i.incident_date
              AND p.expiration_date>=i.incident_date
              AND EXISTS(SELECT 1 FROM coverages v WHERE v.policy_id=p.policy_id AND v.status='ACTIVE'
                         AND v.effective_date<=i.incident_date AND v.expiration_date>=i.incident_date)
              AND NOT EXISTS(SELECT 1 FROM claims c WHERE c.policy_id=p.policy_id AND c.incident_id=i.incident_id)
            LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_claim(self, claim_id: str) -> dict[str, Any]:
        claim = dict(self._claim(claim_id))
        for name in ("documents", "assessments", "reserves", "settlements", "claim_events"):
            claim[name] = [dict(r) for r in self.db.execute(f"SELECT * FROM {name} WHERE claim_id=? ORDER BY rowid", (claim_id,))]
        return claim

    def intake(self, policy_id: str, incident_id: str, claim_amount: float, role: str = "requester",
               actor_id: str = "requester", claim_type: str | None = None) -> dict[str, Any]:
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
        duplicate = self.db.execute("SELECT claim_id FROM claims WHERE policy_id=? AND incident_id=?", (policy_id, incident_id)).fetchone()
        if duplicate:
            raise ValueError(f"Duplicate claim for this policy and incident: {duplicate['claim_id']}")
        cover = self.db.execute("SELECT * FROM coverages WHERE policy_id=? AND status='ACTIVE' ORDER BY coverage_limit DESC LIMIT 1", (policy_id,)).fetchone()
        now = _now()
        seq = self.db.execute("SELECT COUNT(*) n FROM claims WHERE claim_id LIKE 'CLM-2026-%'").fetchone()["n"] + 1
        claim_id = f"CLM-2026-{seq:06d}"
        reason = "Coverage validated"; status = "UNDER_REVIEW"
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
        self.db.execute("INSERT INTO claims(claim_id,policy_id,customer_id,incident_id,claim_type,claim_amount,submission_date,status,denial_reason,coverage_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (claim_id, policy_id, cust, incident_id, typ, claim_amount, date.today().isoformat(), status, reason if status == "DENIED" else None, cover["coverage_id"] if cover else None, now))
        missing = REQUIRED_DOCUMENTS.get(typ, set())
        if status != "DENIED":
            status = "PENDING_DOCUMENTS"
            self.db.execute("UPDATE claims SET status=? WHERE claim_id=?", (status, claim_id))
            for i, doc_type in enumerate(sorted(missing), 1):
                self.db.execute("INSERT INTO documents VALUES(?,?,?,?,?,NULL)", (f"DOC-{claim_id}-{i:03d}", claim_id, doc_type, "REQUESTED", now))
            reason = f"Coverage valid; requested {len(missing)} required documents"
        self._event(claim_id, role, actor_id, "CLAIM_INTAKE", None, status, reason, {"policy_id": policy_id, "incident_id": incident_id, "coverage_id": cover["coverage_id"] if cover else None})
        self.db.commit()
        return self.get_claim(claim_id)

    def receive_document(self, claim_id: str, document_type: str, role: str = "requester", actor_id: str = "requester") -> dict[str, Any]:
        self._actor(role, {"requester", "operations_analyst", "system_administrator"})
        claim = self._claim(claim_id); now = _now()
        row = self.db.execute("SELECT document_id FROM documents WHERE claim_id=? AND document_type=? AND status='REQUESTED' ORDER BY document_id LIMIT 1", (claim_id, document_type)).fetchone()
        if row:
            self.db.execute("UPDATE documents SET status='RECEIVED',received_at=? WHERE document_id=?", (now, row["document_id"]))
        else:
            seq = self.db.execute("SELECT COUNT(*) n FROM documents WHERE claim_id=?", (claim_id,)).fetchone()["n"] + 1
            self.db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)", (f"DOC-{claim_id}-{seq:03d}", claim_id, document_type, "RECEIVED", now, now))
        remaining = self.db.execute("SELECT COUNT(*) n FROM documents WHERE claim_id=? AND status!='RECEIVED'", (claim_id,)).fetchone()["n"]
        new = "PENDING_DOCUMENTS" if remaining else "UNDER_REVIEW"
        self.db.execute("UPDATE claims SET status=? WHERE claim_id=?", (new, claim_id))
        self._event(claim_id, role, actor_id, "DOCUMENT_RECEIVED", claim["status"], new, f"Received {document_type}; {remaining} required items remain", {"document_type": document_type})
        self.db.commit(); return self.get_claim(claim_id)

    def request_documents(self, claim_id: str, role: str = "operations_analyst", actor_id: str = "analyst") -> dict[str, Any]:
        self._actor(role, {"operations_analyst", "system_administrator"})
        claim = self._claim(claim_id)
        missing = self.db.execute("SELECT document_type FROM documents WHERE claim_id=? AND status!='RECEIVED' ORDER BY document_type", (claim_id,)).fetchall()
        if not missing:
            raise ValueError("There are no outstanding documents to request")
        for row in missing:
            self._event(claim_id, role, actor_id, "DOCUMENT_REQUESTED", claim["status"], claim["status"], f"Requested {row['document_type']}", {"document_type": row["document_type"]})
        self.db.commit()
        return self.get_claim(claim_id)

    def assess(self, claim_id: str, role: str = "operations_analyst", actor_id: str = "analyst") -> dict[str, Any]:
        self._actor(role, {"operations_analyst", "system_administrator"})
        claim = self._claim(claim_id)
        if claim["status"] not in {"UNDER_REVIEW", "PENDING_DOCUMENTS"}:
            raise ValueError("Claim must be under review before assessment")
        pending = self.db.execute("SELECT document_type FROM documents WHERE claim_id=? AND status!='RECEIVED'", (claim_id,)).fetchall()
        if pending:
            raise ValueError("Required documents are still missing: " + ", ".join(r[0] for r in pending))
        cov = self.db.execute("SELECT * FROM coverages WHERE coverage_id=?", (claim["coverage_id"],)).fetchone()
        if cov is None:
            raise ValueError("Claim has no applicable coverage")
        amount = min(max(0.0, round(claim["claim_amount"] - cov["deductible"], 2)), cov["coverage_limit"])
        assessment_id = f"ASN-{claim_id}-001"; reserve_id = f"RSV-{claim_id}-001"
        self.db.execute("INSERT OR REPLACE INTO assessments VALUES(?,?,?,?,?,?,?,?,?,?)", (assessment_id, claim_id, claim["assigned_adjuster_id"], claim["claim_amount"], cov["deductible"], amount, cov["coverage_limit"], "RECOMMEND_APPROVAL" if amount else "DENY", "Eligible amount is claim less deductible, capped by coverage limit", _now()))
        self.db.execute("INSERT OR REPLACE INTO reserves VALUES(?,?,?,?,?)", (reserve_id, claim_id, round(claim["claim_amount"], 2), "USD", _now()))
        new = "APPROVAL_PENDING" if amount > 0 else "DENIED"
        reason = "Assessment complete; awaiting approver" if amount > 0 else "Eligible amount is zero after deductible"
        self.db.execute("UPDATE claims SET status=?,eligible_amount=? WHERE claim_id=?", (new, amount, claim_id))
        self._event(claim_id, role, actor_id, "ASSESSMENT", claim["status"], new, reason, {"eligible_amount": amount, "coverage_limit": cov["coverage_limit"], "deductible": cov["deductible"]})
        self.db.commit(); return self.get_claim(claim_id)

    def assign_adjuster(self, claim_id: str, adjuster_id: str, role: str = "operations_analyst", actor_id: str = "analyst") -> dict[str, Any]:
        self._actor(role, {"operations_analyst", "system_administrator"})
        claim = self._claim(claim_id)
        adj = self.db.execute("SELECT * FROM adjusters WHERE adjuster_id=? AND status='ACTIVE'", (adjuster_id,)).fetchone()
        if not adj:
            raise ValueError("Adjuster is not active or does not exist")
        if claim["claim_type"] not in adj["specializations"].upper():
            raise ValueError("Adjuster is not specialized for this claim type")
        self.db.execute("UPDATE claims SET assigned_adjuster_id=? WHERE claim_id=?", (adjuster_id, claim_id))
        self._event(claim_id, role, actor_id, "ADJUSTER_ASSIGNED", claim["status"], claim["status"], f"Assigned {adjuster_id}", {"adjuster_id": adjuster_id})
        self.db.commit(); return self.get_claim(claim_id)

    def approve(self, claim_id: str, decision: str, role: str = "approver", actor_id: str = "approver") -> dict[str, Any]:
        self._actor(role, {"approver", "system_administrator"})
        claim = self._claim(claim_id)
        decision = decision.upper()
        if claim["status"] != "APPROVAL_PENDING":
            raise ValueError("Claim must have a completed assessment before approval")
        if decision == "APPROVE":
            amount = claim["eligible_amount"]
            self.db.execute("INSERT INTO settlements VALUES(?,?,?,?,?,?)", (f"SET-{claim_id}-001", claim_id, amount, "APPROVED", actor_id, _now()))
            new, reason = "SETTLEMENT_APPROVED", f"Approved settlement of {amount:.2f} USD within coverage limit"
        elif decision == "REJECT":
            self.db.execute("UPDATE claims SET denial_reason=? WHERE claim_id=?", ("APPROVER_REJECTED", claim_id))
            new, reason = "DENIED", "Approver rejected the assessment"
        else:
            raise ValueError("decision must be APPROVE or REJECT")
        self.db.execute("UPDATE claims SET status=? WHERE claim_id=?", (new, claim_id))
        self._event(claim_id, role, actor_id, "APPROVER_DECISION", claim["status"], new, reason, {"decision": decision})
        self.db.commit(); return self.get_claim(claim_id)

    def pay(self, claim_id: str, role: str = "system_administrator", actor_id: str = "payment_system") -> dict[str, Any]:
        self._actor(role, {"system_administrator"})
        claim = self._claim(claim_id)
        sett = self.db.execute("SELECT * FROM settlements WHERE claim_id=? AND status='APPROVED'", (claim_id,)).fetchone()
        if not sett:
            raise ValueError("Only an approved settlement can be paid")
        self.db.execute("UPDATE settlements SET status='PAID' WHERE settlement_id=?", (sett["settlement_id"],))
        self.db.execute("UPDATE claims SET status='PAID' WHERE claim_id=?", (claim_id,))
        self._event(claim_id, role, actor_id, "PAYMENT", claim["status"], "PAID", f"Paid settlement {sett['settlement_id']}", {"amount": sett["amount"]})
        self.db.commit(); return self.get_claim(claim_id)

    def metrics(self) -> dict[str, Any]:
        total = self.db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        statuses = {r[0]: r[1] for r in self.db.execute("SELECT status,COUNT(*) FROM claims GROUP BY status")}
        received: dict[str, set[str]] = {}
        for row in self.db.execute("SELECT claim_id,document_type FROM documents WHERE status='RECEIVED'").fetchall():
            received.setdefault(row["claim_id"], set()).add(row["document_type"])
        complete = 0
        for claim in self.db.execute("SELECT claim_id,claim_type FROM claims").fetchall():
            present = received.get(claim["claim_id"], set())
            required = REQUIRED_DOCUMENTS.get(claim["claim_type"], set())
            complete += int(bool(required) and required.issubset(present))
        paid = self.db.execute("SELECT COUNT(*) FROM settlements WHERE status='PAID'").fetchone()[0]
        within = self.db.execute("SELECT COUNT(*) FROM settlements s JOIN claims c ON c.claim_id=s.claim_id WHERE s.amount<=COALESCE((SELECT coverage_limit FROM coverages WHERE coverage_id=c.coverage_id),0)").fetchone()[0]
        events = self.db.execute("SELECT COUNT(*) FROM claim_events").fetchone()[0]
        return {"total_claims": total, "status_counts": statuses, "document_completeness_rate": round(complete / total, 4) if total else 0,
                "paid_settlements": paid, "settlement_reconciliation_rate": round(within / paid, 4) if paid else 0,
                "claims_with_audit_events": self.db.execute("SELECT COUNT(DISTINCT claim_id) FROM claim_events").fetchone()[0], "audit_events": events}

    def rules(self) -> dict[str, Any]:
        return {"required_documents": REQUIRED_DOCUMENTS, "coverage": "incident must be within coverage dates; policy in force; excluded incidents denied",
                "eligibility": "max(0, min(claim amount - deductible, coverage limit))", "roles": sorted(ROLES)}
