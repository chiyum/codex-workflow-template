#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
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

    def test_dev_modifier_parser_and_defaults(self) -> None:
        for profile in ("full", "standard", "lite"):
            with self.subTest(profile=profile):
                parsed = run("parse", "--input", f"$dev {profile} 完成功能")
                self.assertEqual((profile, "standard", "完成功能"), (
                    parsed["requested_profile"], parsed["mode"], parsed["request"],
                ))
                autonomous = run("parse", "--input", f"$dev {profile} auto 完成功能")
                self.assertEqual((profile, "auto"), (
                    autonomous["requested_profile"], autonomous["mode"],
                ))
        legacy_auto = run("parse", "--input", "$dev auto 完成功能")
        self.assertEqual(("standard", "auto"), (legacy_auto["requested_profile"], legacy_auto["mode"]))
        default_dev = run("parse", "--input", "$dev 完成功能")
        natural = run("parse", "--input", "完成自然語言需求")
        self.assertEqual("standard", default_dev["requested_profile"])
        self.assertEqual(("natural_language", "standard"), (natural["entrypoint"], natural["requested_profile"]))
        continuation = run("parse", "--input", "$dev 繼續 task-slug")
        self.assertEqual(("continue", "task-slug", None), (
            continuation["mode"], continuation["continue_slug"], continuation["requested_profile"],
        ))

    def test_lane_profile_floor_never_downgrades(self) -> None:
        expected = {
            "L1": {"lite": "lite", "standard": "standard", "full": "full"},
            "L2": {"lite": "standard", "standard": "standard", "full": "full"},
            "L3": {"lite": "full", "standard": "full", "full": "full"},
        }
        for lane, profiles in expected.items():
            for requested, effective in profiles.items():
                with self.subTest(lane=lane, requested=requested):
                    result = run("plan", "--lane", lane, "--mode", "standard", "--profile", requested)
                    self.assertEqual(requested, result["requested_profile"])
                    self.assertEqual(effective, result["effective_profile"])
                    if requested == effective:
                        self.assertIsNone(result["profile_upgrade_reason"])
                    else:
                        self.assertTrue(result["profile_upgrade_reason"].strip())

    def test_auto_changes_confirmation_not_profile_or_gates(self) -> None:
        ignored = {"mode", "confirmation_required"}
        for lane in ("L1", "L2", "L3"):
            for profile in ("lite", "standard", "full"):
                with self.subTest(lane=lane, profile=profile):
                    standard = run("plan", "--lane", lane, "--mode", "standard", "--profile", profile)
                    autonomous = run("plan", "--lane", lane, "--mode", "auto", "--profile", profile)
                    self.assertTrue(standard["confirmation_required"])
                    self.assertFalse(autonomous["confirmation_required"])
                    self.assertEqual(
                        {key: value for key, value in standard.items() if key not in ignored},
                        {key: value for key, value in autonomous.items() if key not in ignored},
                    )
                    self.assertEqual(
                        ["irreversible_delete", "spending", "security", "prod", "contradictory_requirements"],
                        autonomous["mandatory_interruptions"],
                    )

    def test_profiles_have_mutually_exclusive_ordered_role_gates(self) -> None:
        full = run("plan", "--lane", "L1", "--profile", "full")
        standard = run("plan", "--lane", "L1", "--profile", "standard")
        lite = run("plan", "--lane", "L1", "--profile", "lite")
        self.assertEqual(["pm", "architect", "reviewer", "qa", "pm"], full["agent_sequence"])
        self.assertEqual(["verifier", "architect", "reviewer", "verifier"], standard["agent_sequence"])
        self.assertEqual(["architect", "verifier"], lite["agent_sequence"])
        self.assertEqual((2, 1, 1), (
            full["role_spawn_counts"]["pm"], standard["role_spawn_counts"]["verifier"],
            lite["role_spawn_counts"]["verifier"],
        ))
        self.assertEqual(["qa", "pm"], standard["disabled_core_roles"])
        self.assertEqual(["reviewer", "qa", "pm"], lite["disabled_core_roles"])
        self.assertEqual(full["specialized_gates"], standard["specialized_gates"])
        self.assertEqual(standard["specialized_gates"], lite["specialized_gates"])

    def test_continue_uses_effective_profile_from_state(self) -> None:
        fixtures = (
            ("L1", "full", "full", None),
            ("L1", "standard", "standard", None),
            ("L1", "lite", "lite", None),
            ("L2", "lite", "standard", "L2 最低允許 standard"),
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, (lane, requested, effective, reason) in enumerate(fixtures):
                with self.subTest(lane=lane, requested=requested, effective=effective):
                    path = Path(directory) / f"state-{index}.json"
                    path.write_text(json.dumps({
                        "task": f"task-{index}",
                        "lane": lane,
                        "requested_profile": requested,
                        "effective_profile": effective,
                        "profile_upgrade_reason": reason,
                        "next_action": "continue gate",
                    }), encoding="utf-8")
                    result = run("plan", "--lane", lane, "--mode", "continue", "--state", str(path))
                    self.assertEqual((requested, effective, "continue gate", True), (
                        result["requested_profile"], result["effective_profile"], result["next_action"],
                        result["continued_from_state"],
                    ))

    def test_verifier_is_an_executable_mechanical_role(self) -> None:
        verifier = run("resolve", "--lane", "L2", "--role", "verifier")
        self.assertEqual(("gpt-5.6-terra", "medium", "none", "none"), (
            verifier["model"], verifier["model_reasoning_effort"], verifier["history_mode"],
            verifier["fork_turns"],
        ))
        config = (ROOT / "config.example.toml").read_text(encoding="utf-8")
        manifest = (ROOT / "manifest.tsv").read_text(encoding="utf-8")
        self.assertIn("[agents.verifier]", config)
        self.assertIn("agent-guides/verifier.md", manifest)
        self.assertIn("agents/verifier.toml", manifest)

    def test_agent_guides_follow_profile_role_boundaries(self) -> None:
        guides = {
            name: (ROOT / f"agent-guides/{name}.md").read_text(encoding="utf-8")
            for name in ("architect", "reviewer", "qa", "pm", "verifier")
        }
        self.assertIn("Full／Standard／Lite", guides["architect"])
        self.assertIn("Lite 明確禁用 reviewer", guides["reviewer"])
        self.assertIn("只在 effective Full", guides["qa"])
        self.assertIn("只在 effective Full", guides["pm"])
        self.assertIn("Standard／Lite", guides["verifier"])

    def test_agent_guides_never_require_full_knowledge_index_load(self) -> None:
        forbidden = "Read `~/.codex/knowledge/INDEX.md`"
        offenders = [path.name for path in (ROOT / "agent-guides").glob("*.md") if forbidden in path.read_text(encoding="utf-8")]
        self.assertEqual([], offenders)

    def test_dev_owner_and_reviewer_modules_are_wired(self) -> None:
        dev_skill = (ROOT / "skills/dev/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Full", dev_skill)
        self.assertIn("Standard", dev_skill)
        self.assertIn("Lite", dev_skill)
        self.assertIn("同一 verifier", dev_skill)
        reviewer = (ROOT / "agent-guides/reviewer.md").read_text(encoding="utf-8")
        modules = (ROOT / "agent-guides/reviewer-modules.md").read_text(encoding="utf-8")
        module_names = [line[3:] for line in modules.splitlines() if line.startswith("## ")]
        self.assertIn("reviewer-modules.md", reviewer)
        self.assertTrue(module_names)
        self.assertEqual([], [name for name in module_names if f"`{name}`" not in reviewer])


if __name__ == "__main__":
    unittest.main(verbosity=2)
