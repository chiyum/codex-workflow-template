#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/verify-evidence.py"


class VerifyEvidenceTests(unittest.TestCase):
    def run_case(self, actual: dict[str, str], result: str, deltas: list[str]) -> int:
        with tempfile.TemporaryDirectory(prefix="verify-evidence.") as directory:
            root = Path(directory)
            acceptance = root / "case.md"
            acceptance.write_text(
                "# fixture\n\n### A1 不可替代互動\n"
                "- 不可替代驗法: environment=production; viewport=390x600; "
                "interaction=touch-swipe; data=fixture-a; execution_path=production\n",
                encoding="utf-8",
            )
            evidence = root / "case/evidence"
            evidence.mkdir(parents=True)
            (evidence / "verifier-A1-screen.png").write_bytes(b"png")
            expected = {
                "environment": "production", "viewport": "390x600",
                "interaction": "touch-swipe", "data": "fixture-a",
                "execution_path": "production",
            }
            (evidence / "verifier-A1-receipt.json").write_text(json.dumps({
                "schema_version": "evidence-receipt/v1", "acceptance_id": "A1",
                "role": "verifier", "result": result, "expected": expected,
                "actual": actual, "deltas": deltas,
            }), encoding="utf-8")
            return subprocess.run(
                ["python3", str(SCRIPT), str(acceptance), "--profile", "standard"], check=False,
            ).returncode

    def test_substitute_viewport_wheel_and_dev_harness_are_blocked(self) -> None:
        actual = {
            "environment": "dev", "viewport": "390x844", "interaction": "wheel",
            "data": "fixture-a", "execution_path": "dev-harness",
        }
        self.assertEqual(1, self.run_case(
            actual, "BLOCKED", ["environment", "execution_path", "interaction", "viewport"],
        ))

    def test_exact_non_substitutable_execution_can_pass(self) -> None:
        actual = {
            "environment": "production", "viewport": "390x600",
            "interaction": "touch-swipe", "data": "fixture-a",
            "execution_path": "production",
        }
        self.assertEqual(0, self.run_case(actual, "PASS", []))

    def test_natural_language_constraint_is_exact_execution_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="verify-evidence-natural-language.") as directory:
            root = Path(directory)
            acceptance = root / "case.md"
            constraint = "三台實體裝置、各自瀏覽器與麥克風；HTTP 不可替代。"
            acceptance.write_text(
                "# fixture\n\n### A1 不可替代互動\n"
                f"- 不可替代驗法: {constraint}\n",
                encoding="utf-8",
            )
            evidence = root / "case/evidence"
            evidence.mkdir(parents=True)
            (evidence / "verifier-A1-artifact.txt").write_text("proof\n", encoding="utf-8")
            expected = {"execution_path": constraint}
            (evidence / "verifier-A1-receipt.json").write_text(json.dumps({
                "schema_version": "evidence-receipt/v1", "acceptance_id": "A1",
                "role": "verifier", "result": "PASS", "expected": expected,
                "actual": expected, "deltas": [],
            }), encoding="utf-8")
            completed = subprocess.run(
                ["python3", str(SCRIPT), str(acceptance), "--profile", "standard"],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stdout)

    def test_natural_language_constraint_rejects_substitute_execution_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="verify-evidence-natural-language-substitute.") as directory:
            root = Path(directory)
            acceptance = root / "case.md"
            constraint = "三台實體裝置、各自瀏覽器與麥克風；HTTP 不可替代。"
            acceptance.write_text(
                "# fixture\n\n### A1 不可替代互動\n"
                f"- 不可替代驗法: {constraint}\n",
                encoding="utf-8",
            )
            evidence = root / "case/evidence"
            evidence.mkdir(parents=True)
            (evidence / "verifier-A1-artifact.txt").write_text("proof\n", encoding="utf-8")
            (evidence / "verifier-A1-receipt.json").write_text(json.dumps({
                "schema_version": "evidence-receipt/v1", "acceptance_id": "A1",
                "role": "verifier", "result": "BLOCKED",
                "expected": {"execution_path": constraint},
                "actual": {"execution_path": "單台裝置透過 HTTP 模擬"},
                "deltas": ["execution_path"],
            }), encoding="utf-8")
            completed = subprocess.run(
                ["python3", str(SCRIPT), str(acceptance), "--profile", "standard"],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(1, completed.returncode, completed.stdout)
            self.assertIn("verifier_non_substitutable_mismatch", completed.stdout)

    def test_main_audit_priority_prefers_most_constrained_items(self) -> None:
        with tempfile.TemporaryDirectory(prefix="verify-evidence-priority.") as directory:
            root = Path(directory)
            acceptance = root / "case.md"
            acceptance.write_text(
                "# fixture\n\n### A1 one\n- 不可替代驗法: environment=dev\n"
                "### A2 five\n- 不可替代驗法: environment=production; viewport=390x600; "
                "interaction=touch-swipe; data=fixture-a; execution_path=production\n",
                encoding="utf-8",
            )
            evidence = root / "case/evidence"
            evidence.mkdir(parents=True)
            expected_by_item = {
                "A1": {"environment": "dev"},
                "A2": {
                    "environment": "production", "viewport": "390x600",
                    "interaction": "touch-swipe", "data": "fixture-a",
                    "execution_path": "production",
                },
            }
            for identifier, expected in expected_by_item.items():
                (evidence / f"verifier-{identifier}-artifact.txt").write_text("proof\n", encoding="utf-8")
                (evidence / f"verifier-{identifier}-receipt.json").write_text(json.dumps({
                    "schema_version": "evidence-receipt/v1", "acceptance_id": identifier,
                    "role": "verifier", "result": "PASS", "expected": expected,
                    "actual": expected, "deltas": [],
                }), encoding="utf-8")
            completed = subprocess.run(
                ["python3", str(SCRIPT), str(acceptance), "--profile", "standard"], text=True,
                capture_output=True, check=False,
            )
            self.assertEqual(0, completed.returncode)
            self.assertIn("main_audit_priority=A2,A1", completed.stdout)

    def test_domain_prefixed_acceptance_ids_are_supported(self) -> None:
        with tempfile.TemporaryDirectory(prefix="verify-evidence-domain-ids.") as directory:
            root = Path(directory)
            acceptance = root / "case.md"
            acceptance.write_text(
                "# fixture\n\n### L1 leave\n- 不可替代驗法: environment=local\n"
                "### O1 overtime\n- 不可替代驗法: environment=local\n",
                encoding="utf-8",
            )
            evidence = root / "case/evidence"
            evidence.mkdir(parents=True)
            expected = {"environment": "local"}
            for identifier in ("L1", "O1"):
                (evidence / f"verifier-{identifier}-artifact.txt").write_text("proof\n", encoding="utf-8")
                (evidence / f"verifier-{identifier}-receipt.json").write_text(json.dumps({
                    "schema_version": "evidence-receipt/v1", "acceptance_id": identifier,
                    "role": "verifier", "result": "PASS", "expected": expected,
                    "actual": expected, "deltas": [],
                }), encoding="utf-8")
            completed = subprocess.run(
                ["python3", str(SCRIPT), str(acceptance), "--profile", "standard"], text=True,
                capture_output=True, check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stdout)
            self.assertIn("L1:passed", completed.stdout)
            self.assertIn("O1:passed", completed.stdout)

    def test_unknown_owner_cannot_bypass_required_role(self) -> None:
        with tempfile.TemporaryDirectory(prefix="verify-evidence-owner.") as directory:
            root = Path(directory)
            acceptance = root / "case.md"
            acceptance.write_text("# fixture\n\n### A1 proof\n", encoding="utf-8")
            evidence = root / "case/evidence"
            evidence.mkdir(parents=True)
            (evidence / "misc-A1-artifact.txt").write_text("not owned\n", encoding="utf-8")
            completed = subprocess.run(
                ["python3", str(SCRIPT), str(acceptance), "--profile", "lite"],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(1, completed.returncode)
            self.assertIn("unknown_owner_prefix", completed.stdout)
            self.assertIn("verifier_artifact_missing", completed.stdout)

    def test_full_requires_independent_qa_and_pm_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="verify-evidence-full.") as directory:
            root = Path(directory)
            acceptance = root / "case.md"
            acceptance.write_text("# fixture\n\n### A1 proof\n", encoding="utf-8")
            evidence = root / "case/evidence"
            evidence.mkdir(parents=True)
            (evidence / "qa-A1-artifact.txt").write_text("qa proof\n", encoding="utf-8")
            missing_pm = subprocess.run(
                ["python3", str(SCRIPT), str(acceptance), "--profile", "full"],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(1, missing_pm.returncode)
            self.assertIn("pm_artifact_missing", missing_pm.stdout)
            (evidence / "pm-A1-artifact.txt").write_text("pm proof\n", encoding="utf-8")
            complete = subprocess.run(
                ["python3", str(SCRIPT), str(acceptance), "--profile", "full"],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, complete.returncode, complete.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
