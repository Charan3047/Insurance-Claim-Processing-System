import csv
import unittest
from pathlib import Path

from workflow import ClaimsService

ROOT = Path(__file__).resolve().parents[1]


def rows(name):
    with (ROOT / "output" / "csv" / f"{name}.csv").open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def valid_pair():
    coverages = rows("coverages")
    policies = {r["policy_id"]: r for r in rows("policies")}
    incidents = rows("incidents")
    claims = {(r["policy_id"], r["incident_id"]) for r in rows("claims")}
    for cov in coverages:
        pol = policies[cov["policy_id"]]
        if pol["status"] != "ACTIVE" or cov["status"] != "ACTIVE":
            continue
        for inc in incidents:
            if (pol["policy_id"], inc["incident_id"]) in claims:
                continue
            if (
                inc["product_category"] == pol["product_type"]
                and cov["effective_date"] <= inc["incident_date"] <= cov["expiration_date"]
            ):
                return pol["policy_id"], inc["incident_id"], pol["product_type"]
    raise AssertionError("No valid policy/incident sample in generated data")


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.service = ClaimsService(ROOT / "output")

    def tearDown(self):
        self.service.close()

    def new_claim(self, amount=1250):
        policy, incident, kind = valid_pair()
        return self.service.intake(policy, incident, amount, claim_type=kind)

    def test_intake_requests_required_documents_and_records_actor(self):
        claim = self.new_claim()
        self.assertEqual(claim["status"], "PENDING_DOCUMENTS")
        self.assertGreaterEqual(len(claim["documents"]), 2)
        self.assertEqual(len(claim["claim_items"]), 1)
        self.assertEqual(len(claim["claim_coverage_allocations"]), 1)
        self.assertEqual(claim["claim_events"][0]["action"], "CLAIM_INTAKE")
        self.assertEqual(claim["claim_events"][0]["actor_role"], "requester")

    def test_document_request_and_adjuster_assignment_are_audited_and_populate_bridge(self):
        claim = self.new_claim()
        requested = self.service.request_documents(claim["claim_id"])
        self.assertTrue(any(e["action"] == "DOCUMENT_REQUESTED" for e in requested["claim_events"]))
        adjuster = next(
            r
            for r in self.service.db.execute("SELECT adjuster_id,specializations FROM adjusters WHERE status='ACTIVE'")
            if claim["claim_type"] in r["specializations"].upper()
        )
        assigned = self.service.assign_adjuster(claim["claim_id"], adjuster["adjuster_id"])
        self.assertEqual(assigned["assigned_adjuster_id"], adjuster["adjuster_id"])
        self.assertGreaterEqual(len(assigned["claim_adjuster_assignments"]), 1)
        self.assertTrue(any(e["action"] == "ADJUSTER_ASSIGNED" for e in assigned["claim_events"]))

    def test_role_gates_and_missing_documents_block_assessment(self):
        claim = self.new_claim()
        with self.assertRaises(PermissionError):
            self.service.assess(claim["claim_id"], "requester")
        with self.assertRaises(ValueError):
            self.service.assess(claim["claim_id"])

    def test_eligible_amount_is_capped_and_payment_requires_approval(self):
        claim = self.new_claim(amount=2_000_000)
        for doc in claim["documents"]:
            self.service.receive_document(claim["claim_id"], doc["document_type"])
        assessed = self.service.assess(claim["claim_id"])
        cov = self.service.db.execute(
            "SELECT coverage_limit FROM coverages WHERE coverage_id=?", (assessed["coverage_id"],)
        ).fetchone()
        self.assertGreater(assessed["eligible_amount"], 0)
        self.assertLessEqual(assessed["eligible_amount"], cov[0])
        self.assertEqual(assessed["reserves"][0]["amount"], claim["claim_amount"])
        with self.assertRaises(ValueError):
            self.service.pay(claim["claim_id"])
        approved = self.service.approve(claim["claim_id"], "APPROVE")
        self.assertEqual(approved["status"], "SETTLEMENT_APPROVED")
        paid = self.service.pay(claim["claim_id"])
        self.assertEqual(paid["status"], "PAID")
        self.assertEqual(paid["settlements"][0]["status"], "PAID")
        self.assertEqual(paid["reserves"][0]["status"], "CLOSED")
        self.assertIn("APPROVER_DECISION", [e["action"] for e in paid["claim_events"]])

    def test_duplicate_policy_incident_intake_is_rejected(self):
        claim = self.new_claim()
        with self.assertRaisesRegex(ValueError, "Duplicate claim"):
            self.service.intake(claim["policy_id"], claim["incident_id"], 500)

    def test_exclusion_is_a_denial_with_a_reason_and_event(self):
        policy, incident, kind = valid_pair()
        self.service.db.execute(
            "UPDATE coverages SET exclusions='ALL' WHERE policy_id=? AND status='ACTIVE'", (policy,)
        )
        self.service.db.commit()
        claim = self.service.intake(policy, incident, 700, claim_type=kind)
        self.assertEqual(claim["status"], "DENIED")
        self.assertIn("excluded", claim["denial_reason"])
        self.assertTrue(claim["claim_events"][0]["reason"])

    def test_metrics_are_measurable_and_include_seeded_portfolio_and_audit_coverage(self):
        self.new_claim()
        metrics = self.service.metrics()
        self.assertGreaterEqual(metrics["total_claims"], 1001)
        self.assertGreaterEqual(metrics["claims_with_audit_events"], 1001)
        self.assertGreater(metrics["paid_settlements"], 200)
        self.assertIn("document_completeness_rate", metrics)


if __name__ == "__main__":
    unittest.main()
