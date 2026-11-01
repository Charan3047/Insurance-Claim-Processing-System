import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from generate import InsuranceDataGenerator
from validate import DataValidator


class PipelineTests(unittest.TestCase):
    def test_starter_and_full_outputs_validate_with_zero_warnings_and_keep_labels_separate(self):
        for scale, expected in (("starter", 100), ("full", 1000)):
            with self.subTest(scale=scale), tempfile.TemporaryDirectory() as tmp:
                generator = InsuranceDataGenerator(seed=20261022, scale=scale)
                with contextlib.redirect_stdout(io.StringIO()):
                    generator.generate_all()
                    manifest = generator.export(tmp)
                with contextlib.redirect_stdout(io.StringIO()):
                    result = DataValidator(tmp).validate()
                self.assertTrue(result.passed)
                self.assertEqual(result._fail, 0)
                self.assertEqual(result._warn, 0)
                self.assertEqual(len(generator.claims), expected)
                self.assertEqual(len({r["issue_type"] for r in generator.ground_truth}), 6)
                features = Path(tmp, "features", "claims_input.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
                for forbidden in ("status", "denial_reason", "fraud_indicator", "assigned_adjuster_id", "updated_at"):
                    self.assertNotIn(forbidden, features)
                self.assertIn("csv/claims.csv", manifest["file_sha256"])
                self.assertIn("csv/claim_adjuster_assignments.csv", manifest["file_sha256"])
                self.assertIn("csv/claim_coverage_allocations.csv", manifest["file_sha256"])
                disk_manifest = json.loads(Path(tmp, "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(disk_manifest["checksum"], generator.checksum())

                # Verify all 14 SQL seed files load cleanly into SQLite with foreign keys ON
                db = sqlite3.connect(":memory:")
                db.execute("PRAGMA foreign_keys=ON")
                db.executescript(
                    (Path(__file__).resolve().parents[1] / "schema" / "schema.sql").read_text(encoding="utf-8")
                )
                for table in (
                    "customers", "adjusters", "policies", "coverages", "incidents", "claims",
                    "claim_items", "documents", "assessments", "reserves", "settlements",
                    "claim_adjuster_assignments", "claim_coverage_allocations", "claim_state_history",
                ):
                    db.executescript(Path(tmp, "sql", f"{table}.sql").read_text(encoding="utf-8"))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM claims").fetchone()[0], expected)
                self.assertGreaterEqual(
                    db.execute("SELECT COUNT(*) FROM claim_adjuster_assignments").fetchone()[0], expected
                )
                self.assertGreaterEqual(
                    db.execute("SELECT COUNT(*) FROM claim_coverage_allocations").fetchone()[0], expected
                )
                db.close()

    def test_bottom_up_arithmetic_reconciliation_is_exact(self):
        generator = InsuranceDataGenerator(seed=20261022, scale="starter")
        with contextlib.redirect_stdout(io.StringIO()):
            generator.generate_all()
        items_by_clm = {}
        for it in generator.claim_items:
            self.assertAlmostEqual(round(it["unit_cost"] * it["quantity"], 2), it["claimed_amount"], places=2)
            items_by_clm.setdefault(it["claim_id"], []).append(it)
        for clm in generator.claims:
            s = round(sum(x["claimed_amount"] for x in items_by_clm[clm["claim_id"]]), 2)
            self.assertAlmostEqual(s, clm["claim_amount"], places=2)

    def test_full_record_checksum_is_repeatable(self):
        checksums = []
        for _ in range(2):
            generator = InsuranceDataGenerator(seed=20261022, scale="starter")
            with contextlib.redirect_stdout(io.StringIO()):
                checksums.append(generator.generate_all())
        self.assertEqual(checksums[0], checksums[1])


if __name__ == "__main__":
    unittest.main()
