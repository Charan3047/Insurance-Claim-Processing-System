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
    def test_starter_and_full_outputs_validate_and_keep_labels_separate(self):
        for scale, expected in (("starter", 100), ("full", 1000)):
            with self.subTest(scale=scale), tempfile.TemporaryDirectory() as tmp:
                generator = InsuranceDataGenerator(seed=20261022, scale=scale)
                with contextlib.redirect_stdout(io.StringIO()):
                    generator.generate_all()
                    manifest = generator.export(tmp)
                with contextlib.redirect_stdout(io.StringIO()):
                    result = DataValidator(tmp).validate()
                self.assertTrue(result.passed)
                self.assertEqual(len(generator.claims), expected)
                self.assertEqual(len({r["issue_type"] for r in generator.ground_truth}), 6)
                features = Path(tmp, "features", "claims_input.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
                self.assertNotIn("status", features); self.assertNotIn("denial_reason", features)
                self.assertIn("csv/claims.csv", manifest["file_sha256"])
                disk_manifest = json.loads(Path(tmp, "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(disk_manifest["checksum"], generator.checksum())
                db = sqlite3.connect(":memory:"); db.execute("PRAGMA foreign_keys=ON")
                db.executescript((Path(__file__).resolve().parents[1] / "schema" / "schema.sql").read_text(encoding="utf-8"))
                for table in ("customers", "adjusters", "policies", "coverages", "incidents", "claims",
                              "claim_items", "documents", "assessments", "reserves", "settlements", "claim_state_history"):
                    db.executescript(Path(tmp, "sql", f"{table}.sql").read_text(encoding="utf-8"))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM claims").fetchone()[0], expected)
                db.close()

    def test_full_record_checksum_is_repeatable(self):
        checksums = []
        for _ in range(2):
            generator = InsuranceDataGenerator(seed=20261022, scale="starter")
            with contextlib.redirect_stdout(io.StringIO()):
                checksums.append(generator.generate_all())
        self.assertEqual(checksums[0], checksums[1])


if __name__ == "__main__": unittest.main()
