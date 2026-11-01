import io
import json
import unittest
from pathlib import Path

from app import PAGE, Handler
from workflow import ClaimsService

ROOT = Path(__file__).resolve().parents[1]


class AppSmokeTests(unittest.TestCase):
    def setUp(self):
        self.service = ClaimsService(ROOT / "output")

    def tearDown(self):
        self.service.close()

    def call(self, method, path, body=None, role="requester"):
        handler = object.__new__(Handler)
        handler.service = self.service
        handler.path = path
        encoded = json.dumps(body or {}).encode()
        handler.headers = {"Content-Length": str(len(encoded)), "X-Role": role, "X-Actor": "api-test"}
        handler.rfile = io.BytesIO(encoded)
        handler.wfile = io.BytesIO()
        handler._status = []
        handler.send_response = lambda status: handler._status.append(status)
        handler.send_header = lambda *_: None
        handler.end_headers = lambda: None
        getattr(handler, f"do_{method}")()
        raw = handler.wfile.getvalue()
        content = raw.decode() if path == "/" else json.loads(raw) if raw else None
        return handler._status[-1], content

    def test_browser_dashboard_exposes_workflow_and_metrics(self):
        self.assertIn("Insurance Claims Desk", PAGE)
        for action in ("intake()", "requestDocs()", "receiveDoc()", "assess()", "assign()", "approve()", "pay()", "escalateSiu()"):
            self.assertIn(action, PAGE)
        status, refs = self.call("GET", "/api/reference")
        self.assertEqual(status, 200)
        self.assertTrue(refs)
        self.assertIn("required_documents", self.service.rules())
        m = self.service.metrics()
        self.assertIn("document_completeness_rate", m)
        self.assertGreater(m["paid_settlements"], 0)
        self.assertGreater(m["audit_events"], 3000)
        self.assertGreater(m["adjuster_bridge_assignments"], 1000)

    def test_full_http_action_flow_and_role_restrictions(self):
        _, refs = self.call("GET", "/api/reference")
        status, claim = self.call(
            "POST",
            "/api/claims",
            {"policy_id": refs[0]["policy_id"], "incident_id": refs[0]["incident_id"], "claim_amount": 1500},
        )
        self.assertEqual(status, 200)
        claim_id = claim["claim_id"]
        self.assertEqual(len(claim["claim_items"]), 1)
        self.assertEqual(float(claim["claim_items"][0]["claimed_amount"]), 1500.0)

        status, _ = self.call("POST", f"/api/claims/{claim_id}/documents/request", role="operations_analyst")
        self.assertEqual(status, 200)
        status, _ = self.call("POST", f"/api/claims/{claim_id}/assign", role="operations_analyst")
        self.assertEqual(status, 200)
        status, _ = self.call("POST", f"/api/claims/{claim_id}/documents/receive", role="requester")
        self.assertEqual(status, 200)
        status, assessed = self.call("POST", f"/api/claims/{claim_id}/assess", role="operations_analyst")
        self.assertEqual(status, 200)
        self.assertEqual(assessed["status"], "APPROVAL_PENDING")
        status, approved = self.call(
            "POST", f"/api/claims/{claim_id}/approve", {"decision": "APPROVE"}, role="approver"
        )
        self.assertEqual(status, 200)
        self.assertEqual(approved["status"], "SETTLEMENT_APPROVED")
        status, paid = self.call("POST", f"/api/claims/{claim_id}/pay", role="system_administrator")
        self.assertEqual(status, 200)
        self.assertEqual(paid["status"], "PAID")
        status, denied = self.call("POST", f"/api/claims/{claim_id}/assess", role="process_owner")
        self.assertEqual(status, 403)
        self.assertIn("error", denied)
        self.assertGreaterEqual(len(paid["claim_events"]), 6)

    def test_siu_escalation_endpoint(self):
        _, refs = self.call("GET", "/api/reference")
        status, claim = self.call(
            "POST",
            "/api/claims",
            {"policy_id": refs[1]["policy_id"], "incident_id": refs[1]["incident_id"], "claim_amount": 8500},
        )
        self.assertEqual(status, 200)
        cid = claim["claim_id"]
        status, siu = self.call(
            "POST", f"/api/claims/{cid}/siu", {"reason": "Suspicious velocity"}, role="operations_analyst"
        )
        self.assertEqual(status, 200)
        self.assertEqual(siu["status"], "SIU_INVESTIGATION")
        self.assertEqual(siu["fraud_indicator"], 1)


if __name__ == "__main__":
    unittest.main()
