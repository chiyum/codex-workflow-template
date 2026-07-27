#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/workflow-profile.py"


def run(*args: str, expected: int = 0) -> dict:
    completed = subprocess.run(["python3", str(SCRIPT), *args], text=True, capture_output=True, check=False)
    if completed.returncode != expected:
        raise AssertionError(f"rc={completed.returncode} stdout={completed.stdout} stderr={completed.stderr}")
    return json.loads(completed.stdout)


class WorkflowProfileTests(unittest.TestCase):
    def test_l1_requires_every_eligibility_predicate(self) -> None:
        result = run("lane", "--l1-candidate", "--acceptance-count", "2", "--single-repo", "--rollbackable", "--target", "dev")
        self.assertEqual("L1", result["lane"])
        missing = run("lane", "--l1-candidate", "--acceptance-count", "2", "--single-repo", "--target", "dev")
        self.assertEqual("L2", missing["lane"])

    def test_every_frozen_high_risk_category_forces_l3(self) -> None:
        risks = (
            "auth", "permission", "tenant", "database", "migration", "data-consistency",
            "cross-repo-contract", "infrastructure", "payment", "irreversible", "prod",
            "major-architecture", "new-visual",
        )
        for risk in risks:
            with self.subTest(risk=risk):
                result = run("lane", "--risk", risk, "--l1-candidate", "--acceptance-count", "1", "--single-repo", "--rollbackable", "--target", "dev")
                self.assertEqual("L3", result["lane"])

    def test_model_effort_matrix_and_bounded_context(self) -> None:
        main = run("resolve", "--lane", "L2", "--role", "main")
        self.assertEqual(("gpt-5.6-sol", "medium"), (main["model"], main["model_reasoning_effort"]))
        low_arch = run("resolve", "--lane", "L1", "--role", "architect")
        self.assertEqual(("gpt-5.6-sol", "medium", "3"), (low_arch["model"], low_arch["model_reasoning_effort"], low_arch["fork_turns"]))
        mechanical = run("resolve", "--lane", "L1", "--role", "pm")
        self.assertEqual(("gpt-5.6-terra", "low", "none"), (mechanical["model"], mechanical["model_reasoning_effort"], mechanical["fork_turns"]))
        for role, effort in (("qa", "medium"), ("test", "medium"), ("evidence", "low")):
            with self.subTest(role=role):
                mechanical = run("resolve", "--lane", "L3", "--role", role)
                self.assertEqual(
                    ("gpt-5.6-terra", effort, "none", "none"),
                    (
                        mechanical["model"],
                        mechanical["model_reasoning_effort"],
                        mechanical["history_mode"],
                        mechanical["fork_turns"],
                    ),
                )
        high_pm = run("resolve", "--lane", "L3", "--role", "pm")
        self.assertEqual(("gpt-5.6-sol", "high", "3"), (high_pm["model"], high_pm["model_reasoning_effort"], high_pm["fork_turns"]))
        security = run("resolve", "--lane", "L1", "--role", "security_auditor")
        self.assertEqual("high", security["model_reasoning_effort"])

    def test_xhigh_requires_reason_role_and_non_l1(self) -> None:
        allowed = run("resolve", "--lane", "L3", "--role", "reviewer", "--xhigh-reason", "深層 race 單線推理")
        self.assertEqual("xhigh", allowed["model_reasoning_effort"])
        denied = run("resolve", "--lane", "L1", "--role", "architect", "--xhigh-reason", "不該允許", expected=2)
        self.assertIn("error", denied)
        whitespace = run("resolve", "--lane", "L3", "--role", "reviewer", "--xhigh-reason", "   ", expected=2)
        self.assertIn("不可為空", whitespace["error"])

    def test_reuse_resolves_to_followup_without_new_fork(self) -> None:
        reused = run("resolve", "--lane", "L1", "--role", "qa", "--reuse")
        self.assertEqual("followup", reused["action"])
        self.assertIsNone(reused["fork_turns"])

    def test_l1_dev_auto_plan_skips_pm_spawn_and_uses_targeted_pre_review(self) -> None:
        l1 = run("plan", "--lane", "L1", "--mode", "auto")
        self.assertEqual("main", l1["acceptance_owner"])
        self.assertEqual(0, l1["acceptance_pm_spawn_count"])
        self.assertEqual("targeted", l1["pre_review"])
        self.assertEqual("main_evidence_audit", l1["pm_acceptance"])
        for lane in ("L2", "L3"):
            with self.subTest(lane=lane):
                result = run("plan", "--lane", lane, "--mode", "auto")
                self.assertEqual(("pm", 1, "full"), (
                    result["acceptance_owner"], result["acceptance_pm_spawn_count"], result["pre_review"],
                ))

    def test_agent_guides_never_require_full_knowledge_index_load(self) -> None:
        forbidden = "Read `~/.codex/knowledge/INDEX.md`"
        offenders = [path.name for path in (ROOT / "agent-guides").glob("*.md") if forbidden in path.read_text(encoding="utf-8")]
        self.assertEqual([], offenders)

    def test_dev_owner_and_reviewer_modules_are_wired(self) -> None:
        dev_skill = (ROOT / "skills/dev/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("L1 由主 Codex", dev_skill)
        self.assertIn("L2/L3 才由 PM", dev_skill)
        reviewer = (ROOT / "agent-guides/reviewer.md").read_text(encoding="utf-8")
        modules = (ROOT / "agent-guides/reviewer-modules.md").read_text(encoding="utf-8")
        module_names = [line[3:] for line in modules.splitlines() if line.startswith("## ")]
        self.assertIn("reviewer-modules.md", reviewer)
        self.assertTrue(module_names)
        self.assertEqual([], [name for name in module_names if f"`{name}`" not in reviewer])


if __name__ == "__main__":
    unittest.main(verbosity=2)
