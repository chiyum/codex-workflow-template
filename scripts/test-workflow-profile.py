#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/workflow-profile.py"
BASELINE_COLLECTOR = ROOT / "scripts/development-baseline.py"


def canonical_sha256(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def write_confirmed_receipt(path: Path, target: str = "local", *, collector_path: str = "scripts/development-baseline.py") -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    repo_identity = {
        "path": "/tmp/example-repo", "branch": "main", "local_head": "a" * 40,
        "remote": "origin", "remote_head": "a" * 40,
        "proposed_baseline": "a" * 40, "proposed_baseline_tree": "b" * 40,
    }
    receipt = {
        "schema_version": "development-baseline/v2",
        "product": "fixture",
        "target_environment": target,
        "queried_at": now,
        "repos": [{
            **repo_identity,
            "repo_identity_sha256": canonical_sha256(repo_identity),
            "behavior_authority": {
                "sources": [{"path": "README.md", "blob": "c" * 40}],
                "summary": "fixture behavior",
            },
        }],
        "collector_identity": {
            "path": collector_path,
            "sha256": hashlib.sha256(BASELINE_COLLECTOR.read_bytes()).hexdigest(),
        },
        "requires_user_confirmation": True,
        "writer_selection_blocked": True,
    }
    digest = canonical_sha256(receipt)
    receipt["receipt_sha256"] = digest
    receipt["confirmation"] = {
        "confirmed_by": "user", "confirmed_at": now, "receipt_sha256": digest,
    }
    path.write_text(json.dumps(receipt), encoding="utf-8")


def run(*args: str, expected: int = 0) -> dict:
    completed = subprocess.run(["python3", str(SCRIPT), *args], text=True, capture_output=True, check=False)
    if completed.returncode != expected:
        raise AssertionError(f"rc={completed.returncode} stdout={completed.stdout} stderr={completed.stderr}")
    return json.loads(completed.stdout)


def run_stdin(source: str, expected: int = 0) -> dict:
    completed = subprocess.run(
        ["python3", str(SCRIPT), "parse", "--stdin"],
        input=source,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != expected:
        raise AssertionError(f"rc={completed.returncode} stdout={completed.stdout} stderr={completed.stderr}")
    return json.loads(completed.stdout)


def state_from_plan(receipt: Path, lane: str, requested: str, *, product_floor: str | None = None) -> dict:
    args = [
        "plan", "--lane", lane, "--profile", requested, "--target", "local",
        "--product-policy-status", "known", "--baseline-receipt", str(receipt),
    ]
    if product_floor is not None:
        args.extend(("--product-release-floor", product_floor))
    planned = run(*args)
    return {
        "task": "fixture-task", "cwd": "/tmp/example-repo", "lane": lane,
        "mode": "standard", "requested_profile": planned["requested_profile"],
        "effective_profile": planned["effective_profile"],
        "profile_upgrade_reason": planned["profile_upgrade_reason"],
        "next_action": "continue gate", "target": planned["target"],
        "product_policy_status": planned["product_policy_status"],
        "product_release_floor": planned["product_release_floor"],
        "release_risk": planned["release_risk"], "effective_floor": planned["effective_floor"],
        "baseline_receipt_path": str(receipt.resolve()),
        "baseline_receipt_identity": planned["baseline_gate"]["receipt_identity"],
    }


class WorkflowProfileTests(unittest.TestCase):
    def test_l1_requires_every_eligibility_predicate(self) -> None:
        result = run("lane", "--l1-candidate", "--acceptance-count", "2", "--single-repo", "--rollbackable", "--target", "dev")
        self.assertEqual("L1", result["lane"])
        missing = run("lane", "--l1-candidate", "--acceptance-count", "2", "--single-repo", "--target", "dev")
        self.assertEqual("L2", missing["lane"])

    def test_every_frozen_high_risk_category_forces_l3(self) -> None:
        risks = (
            "auth", "permission", "tenant", "database", "migration", "data-consistency",
            "cross-repo-contract", "infrastructure", "payment", "irreversible",
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
        self.assertIsNone(default_dev["requested_profile"])
        self.assertEqual(("natural_language", None), (natural["entrypoint"], natural["requested_profile"]))
        continuation = run("parse", "--input", "$dev 繼續 task-slug")
        self.assertEqual(("continue", "task-slug", None), (
            continuation["mode"], continuation["continue_slug"], continuation["requested_profile"],
        ))

    def test_dev_parser_preserves_request_and_never_shell_parses_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "不應建立"
            requirement = f"don't normalize  \"unterminated $(touch {marker}) ; keep 'quotes'  "
            raw = f"$dev lite auto {requirement}"
            for parsed in (run("parse", "--input", raw), run_stdin(raw)):
                with self.subTest(entry=parsed):
                    self.assertEqual(("lite", "auto", requirement), (
                        parsed["requested_profile"], parsed["mode"], parsed["request"],
                    ))
            self.assertFalse(marker.exists())
        natural = run_stdin("don't reject an unmatched ' or \" quote")
        self.assertEqual("don't reject an unmatched ' or \" quote", natural["request"])

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

    def test_omitted_profile_uses_change_lane_floor(self) -> None:
        expected = {"L1": "lite", "L2": "standard", "L3": "full"}
        for lane, profile in expected.items():
            with self.subTest(lane=lane):
                result = run("plan", "--lane", lane)
                self.assertEqual((profile, profile), (
                    result["requested_profile"], result["effective_profile"],
                ))

    def test_release_target_does_not_reclassify_change_lane(self) -> None:
        lanes = {
            target: run(
                "lane", "--l1-candidate", "--acceptance-count", "2", "--single-repo",
                "--rollbackable", "--target", target,
            )["lane"]
            for target in ("local", "dev", "prod")
        }
        self.assertEqual({"local": "L1", "dev": "L1", "prod": "L1"}, lanes)
        local = run("plan", "--lane", "L1", "--target", "local")
        prod = run("plan", "--lane", "L1", "--target", "prod")
        self.assertEqual(local["change_lane"], prod["change_lane"])
        self.assertTrue(prod["release_risk"]["direct_prod_requires_confirmation"])
        self.assertIn("user_direct_prod_confirmation", prod["release_risk"]["gates"])

    def test_product_release_floor_and_unknown_prod_policy_only_raise_profile(self) -> None:
        floored = run(
            "plan", "--lane", "L1", "--profile", "lite",
            "--product-release-floor", "standard", "--target", "dev",
        )
        self.assertEqual(("L1", "standard"), (floored["change_lane"], floored["effective_profile"]))
        fail_safe = run(
            "plan", "--lane", "L1", "--profile", "lite", "--target", "prod",
            "--product-policy-status", "missing",
        )
        self.assertEqual(("L1", "full"), (fail_safe["change_lane"], fail_safe["effective_profile"]))
        self.assertTrue(fail_safe["release_risk"]["fail_safe_full"])
        self.assertTrue(fail_safe["confirmation_required"])
        omitted_policy = run("plan", "--lane", "L1", "--profile", "lite", "--target", "prod")
        self.assertEqual("unknown", omitted_policy["product_policy_status"])
        self.assertEqual("full", omitted_policy["effective_profile"])
        self.assertTrue(omitted_policy["release_risk"]["fail_safe_full"])

    def test_security_gate_is_surface_triggered_not_release_triggered(self) -> None:
        ui_prod = run("plan", "--lane", "L1", "--target", "prod", "--surface", "ui")
        self.assertFalse(ui_prod["security_auditor"]["triggered"])
        self.assertEqual([], ui_prod["reviewer_modules"])
        auth = run("plan", "--lane", "L3", "--surface", "auth")
        self.assertFalse(auth["security_auditor"]["triggered"])
        self.assertEqual(["security-and-tenancy"], auth["reviewer_modules"])
        infra = run(
            "plan", "--lane", "L3", "--surface", "port", "--surface", "pipeline",
        )
        self.assertTrue(infra["security_auditor"]["triggered"])
        self.assertEqual(["pipeline", "port"], infra["security_auditor"]["scope"])
        scan = run("plan", "--lane", "L2", "--surface", "external-scan")
        self.assertTrue(scan["external_scan_requires_confirmation"])

    def test_every_profile_is_targeted_without_dropping_hard_validations(self) -> None:
        for lane in ("L1", "L2", "L3"):
            for profile in ("lite", "standard", "full"):
                with self.subTest(lane=lane, profile=profile):
                    result = run("plan", "--lane", lane, "--profile", profile)
                    self.assertEqual("targeted", result["pre_review"])
                    self.assertFalse(result["full_site_authorized"])
                    self.assertEqual(
                        ["lint", "build", "test", "pre_review"],
                        result["validation_manifest"]["required_validations"],
                    )

    def test_fresh_baseline_blocks_code_and_writer_selection_until_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt.json"
            write_confirmed_receipt(receipt)
            for raw in ("自然語言開發需求", "$dev lite 開發需求"):
                parsed = run("parse", "--input", raw)
                self.assertIn(parsed["entrypoint"], {"natural_language", "dev"})
                blocked = run("plan", "--lane", "L1", "--mode", "standard")
                self.assertTrue(blocked["baseline_gate"]["code_write_blocked"])
                confirmed = run(
                    "plan", "--lane", "L1", "--mode", "standard",
                    "--target", "local", "--baseline-receipt", str(receipt),
                )
                self.assertFalse(confirmed["baseline_gate"]["writer_selection_blocked"])
                self.assertRegex(
                    confirmed["baseline_gate"]["receipt_identity"]["identity_sha256"],
                    r"^[0-9a-f]{64}$",
                )
            self_declared = subprocess.run(
                ["python3", str(SCRIPT), "plan", "--lane", "L1", "--baseline-status", "confirmed"],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(2, self_declared.returncode)

            invalid = Path(directory) / "invalid-collector.json"
            write_confirmed_receipt(invalid, collector_path="scripts/missing-collector.py")
            result = run(
                "plan", "--lane", "L1", "--target", "local",
                "--baseline-receipt", str(invalid), expected=2,
            )
            self.assertIn("collector identity", result["error"])

            isolated_resolver = Path(directory) / "workflow-profile.py"
            isolated_resolver.write_bytes(SCRIPT.read_bytes())
            missing_collector = subprocess.run(
                [
                    "python3", str(isolated_resolver), "plan", "--lane", "L1",
                    "--target", "local", "--baseline-receipt", str(receipt),
                ],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(2, missing_collector.returncode)
            self.assertIn("collector identity", missing_collector.stdout)

    def test_unsplittable_material_all_or_e2e_requires_confirmation(self) -> None:
        for scope in ("all", "e2e"):
            result = run(
                "plan", "--lane", "L3", "--mode", "standard", "--repo-test-scope", scope,
                "--test-filter", "unavailable", "--test-cost", "material",
            )
            self.assertEqual(
                "diff_acceptance_and_nearest_required_sentinels", result["test_execution"]["scope"],
            )
            self.assertTrue(result["test_execution"]["requires_user_confirmation"])
            self.assertEqual(
                "unsplittable_broad_test_has_material_cost", result["test_execution"]["blocked_reason"],
            )

    def test_baseline_receipt_hash_confirmation_repo_and_freshness_mutations_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "valid.json"
            write_confirmed_receipt(valid)
            original = json.loads(valid.read_text(encoding="utf-8"))

            tampered = root / "tampered.json"
            tampered_value = json.loads(json.dumps(original))
            tampered_value["product"] = "changed-without-resigning"
            tampered.write_text(json.dumps(tampered_value), encoding="utf-8")

            missing_confirmation = root / "missing-confirmation.json"
            missing_value = json.loads(json.dumps(original))
            missing_value.pop("confirmation")
            missing_confirmation.write_text(json.dumps(missing_value), encoding="utf-8")

            invalid_repo = root / "invalid-repo.json"
            invalid_repo_value = json.loads(json.dumps(original))
            invalid_repo_value["repos"][0]["repo_identity_sha256"] = "d" * 64
            invalid_repo_value.pop("confirmation")
            invalid_repo_value.pop("receipt_sha256")
            invalid_repo_digest = canonical_sha256(invalid_repo_value)
            invalid_repo_value["receipt_sha256"] = invalid_repo_digest
            invalid_repo_value["confirmation"] = {
                "confirmed_by": "user",
                "confirmed_at": original["confirmation"]["confirmed_at"],
                "receipt_sha256": invalid_repo_digest,
            }
            invalid_repo.write_text(json.dumps(invalid_repo_value), encoding="utf-8")

            stale = root / "stale.json"
            stale_value = json.loads(json.dumps(original))
            stale_value["queried_at"] = "2020-01-01T00:00:00+00:00"
            stale_value.pop("confirmation")
            stale_value.pop("receipt_sha256")
            stale_digest = canonical_sha256(stale_value)
            stale_value["receipt_sha256"] = stale_digest
            stale_value["confirmation"] = {
                "confirmed_by": "user", "confirmed_at": "2020-01-01T00:00:01+00:00",
                "receipt_sha256": stale_digest,
            }
            stale.write_text(json.dumps(stale_value), encoding="utf-8")

            for path, error in (
                (tampered, "content hash"),
                (missing_confirmation, "user confirmation"),
                (invalid_repo, "repo identity"),
                (stale, "不新鮮"),
            ):
                with self.subTest(path=path.name):
                    result = run(
                        "plan", "--lane", "L1", "--target", "local",
                        "--baseline-receipt", str(path), expected=2,
                    )
                    self.assertIn(error, result["error"])

    def test_auto_and_standard_require_confirmation_with_identical_contract(self) -> None:
        ignored = {"mode"}
        for lane in ("L1", "L2", "L3"):
            for profile in ("lite", "standard", "full"):
                with self.subTest(lane=lane, profile=profile):
                    standard = run("plan", "--lane", lane, "--mode", "standard", "--profile", profile)
                    autonomous = run("plan", "--lane", lane, "--mode", "auto", "--profile", profile)
                    self.assertTrue(standard["confirmation_required"])
                    self.assertTrue(autonomous["confirmation_required"])
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
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt.json"
            write_confirmed_receipt(receipt)
            fixtures = (
                ("L1", "lite", None),
                ("L2", "lite", None),
                ("L3", "standard", None),
                ("L1", "lite", "standard"),
            )
            for origin_mode in ("standard", "auto"):
                for index, (lane, requested, product_floor) in enumerate(fixtures):
                    state = state_from_plan(receipt, lane, requested, product_floor=product_floor)
                    state["mode"] = origin_mode
                    with self.subTest(origin_mode=origin_mode, lane=lane, product_floor=product_floor):
                        path = Path(directory) / f"state-{origin_mode}-{index}.json"
                        path.write_text(json.dumps(state), encoding="utf-8")
                        result = run("plan", "--lane", lane, "--mode", "continue", "--state", str(path))
                        self.assertEqual((requested, state["effective_profile"], "continue gate", True, False), (
                            result["requested_profile"], result["effective_profile"], result["next_action"],
                            result["continued_from_state"], result["confirmation_required"],
                        ))
                        self.assertEqual(state["release_risk"], result["release_risk"])
                        self.assertEqual(
                            state["baseline_receipt_identity"], result["baseline_gate"]["receipt_identity"],
                        )

    def test_continue_state_rejects_incomplete_or_inconsistent_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt.json"
            write_confirmed_receipt(receipt)
            base = state_from_plan(receipt, "L2", "lite")
            cases = (
                ("missing", {key: value for key, value in base.items() if key != "effective_profile"}, "L2", "缺少必要欄位"),
                ("lane-mismatch", {**base, "lane": "L1"}, "L2", "lane 與 --lane 不一致"),
                ("downgrade", {**base, "requested_profile": "full", "effective_profile": "standard"}, "L2", "不得把 requested profile 降級"),
                ("below-floor", {**base, "effective_profile": "lite"}, "L2", "低於 lane 下限"),
                ("reason-drift", {**base, "profile_upgrade_reason": "free text"}, "L2", "profile_upgrade_reason"),
                ("release-drift", {**base, "release_risk": {**base["release_risk"], "gates": ["unexpected"]}}, "L2", "release gates"),
                ("receipt-drift", {**base, "baseline_receipt_identity": {**base["baseline_receipt_identity"], "receipt_sha256": "d" * 64}}, "L2", "baseline receipt identity"),
                ("empty-next", {**base, "next_action": "  "}, "L2", "缺少 next_action"),
            )
            for name, state, lane, error in cases:
                with self.subTest(name=name):
                    path = Path(directory) / f"{name}.json"
                    path.write_text(json.dumps(state), encoding="utf-8")
                    result = run("plan", "--lane", lane, "--mode", "continue", "--state", str(path), expected=2)
                    self.assertIn(error, result["error"])

            path = Path(directory) / "override.json"
            path.write_text(json.dumps(base), encoding="utf-8")
            override = run(
                "plan", "--lane", "L2", "--mode", "continue", "--state", str(path),
                "--target", "prod", expected=2,
            )
            self.assertIn("不得以 CLI 覆寫", override["error"])

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
        for marker in ("Lite → verifier", "Standard → reviewer", "Full → reviewer"):
            self.assertIn(marker, guides["architect"])
        for marker in ("mcp__playwright__browser_take_screenshot", "playwright-lock.sh acquire", "Local → dev", "evidence/"):
            self.assertIn(marker, guides["verifier"])

    def test_state_contract_documents_profile_and_verifier_handoff(self) -> None:
        state_readme = (ROOT / "state/README.md").read_text(encoding="utf-8")
        for marker in ("cwd", "lane", "requested_profile", "effective_profile", "profile_upgrade_reason"):
            self.assertIn(f'"{marker}"', state_readme)
        self.assertIn('"next_action": "verifier 本地驗證', state_readme)

    def test_agent_guides_never_require_full_knowledge_index_load(self) -> None:
        forbidden = "Read `~/.codex/knowledge/INDEX.md`"
        offenders = [path.name for path in (ROOT / "agent-guides").glob("*.md") if forbidden in path.read_text(encoding="utf-8")]
        self.assertEqual([], offenders)

    def test_state_template_contains_continue_profile_contract(self) -> None:
        state = json.loads((ROOT / "state/TEMPLATE.json").read_text(encoding="utf-8"))
        required = {"cwd", "lane", "requested_profile", "effective_profile", "profile_upgrade_reason", "next_action"}
        self.assertEqual(set(), required - state.keys())
        self.assertEqual("run verifier", state["next_action"])

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
