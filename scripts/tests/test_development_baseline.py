#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/development-baseline.py"


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], text=True, capture_output=True, check=True,
    ).stdout.strip()


class DevelopmentBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = tempfile.TemporaryDirectory(prefix="development-baseline.")
        self.root = Path(self.context.name)

    def tearDown(self) -> None:
        self.context.cleanup()

    def repo(self, name: str) -> tuple[Path, Path]:
        remote = self.root / f"{name}.git"
        work = self.root / name
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
        (work / "README.md").write_text(name + "\n", encoding="utf-8")
        git(work, "add", "README.md")
        subprocess.run([
            "git", "-C", str(work), "-c", "user.name=Test", "-c",
            "user.email=test@example.com", "commit", "-qm", "init",
        ], check=True)
        git(work, "remote", "add", "origin", str(remote))
        git(work, "push", "-q", "-u", "origin", "main")
        return work, remote

    def collect(self, repos: list[dict]) -> tuple[int, dict]:
        order = {
            "product": "fixture", "target_environment": "dev",
            "product_route": {
                "source": "products/fixture.md", "account": "fixture",
                "environment": "dev", "version_routing": "provided_fixture",
                "release_policy": "fixture-release-policy",
            },
            "repos": repos,
        }
        completed = subprocess.run(
            ["python3", str(SCRIPT), "collect"], input=json.dumps(order), text=True,
            capture_output=True, check=False,
        )
        return completed.returncode, json.loads(completed.stdout)

    def test_multi_repo_receipt_records_dirty_remote_and_deployed_unavailable(self) -> None:
        first, _ = self.repo("first")
        second, _ = self.repo("second")
        (first / "README.md").write_text("dirty\n", encoding="utf-8")
        (first / "untracked.txt").write_text("new\n", encoding="utf-8")
        first_remote = git(first, "rev-parse", "origin/main")
        second_remote = git(second, "rev-parse", "origin/main")
        rc, receipt = self.collect([
            {
                "path": str(first), "remote": "origin", "proposed_baseline": "remote_head",
                "behavior_sources": ["README.md"], "behavior_summary": "first repo behavior",
                "deployed": {"version": first_remote, "queried_at": "2026-08-23T20:00:00+08:00"},
            },
            {
                "path": str(second), "remote": "origin", "proposed_baseline": "local_head",
                "behavior_sources": ["README.md"], "behavior_summary": "second repo behavior",
                "deployed": {"version": None, "unavailable_reason": "no version endpoint"},
            },
        ])
        self.assertEqual(0, rc)
        self.assertTrue(receipt["writer_selection_blocked"])
        self.assertTrue(receipt["requires_user_confirmation"])
        self.assertEqual([first_remote, second_remote], [item["remote_head"] for item in receipt["repos"]])
        self.assertEqual(["README.md"], receipt["repos"][0]["tracked_dirty_paths"])
        self.assertEqual(["untracked.txt"], receipt["repos"][0]["untracked_paths"])
        self.assertEqual("no version endpoint", receipt["repos"][1]["deployed"]["unavailable_reason"])
        self.assertIn("dirty_worktree", [item["kind"] for item in receipt["differences"]])
        self.assertEqual("development-baseline/v2", receipt["schema_version"])
        payload = dict(receipt)
        digest = payload.pop("receipt_sha256")
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(hashlib.sha256(canonical.encode("utf-8")).hexdigest(), digest)

    def test_local_remote_divergence_is_explicit_and_never_silently_selects(self) -> None:
        work, _ = self.repo("ahead")
        (work / "ahead.txt").write_text("ahead\n", encoding="utf-8")
        git(work, "add", "ahead.txt")
        subprocess.run([
            "git", "-C", str(work), "-c", "user.name=Test", "-c",
            "user.email=test@example.com", "commit", "-qm", "ahead",
        ], check=True)
        rc, receipt = self.collect([{
            "path": str(work), "remote": "origin", "proposed_baseline": "remote_head",
            "behavior_sources": ["README.md"], "behavior_summary": "repo behavior",
            "deployed": {"version": None, "unavailable_reason": "probe offline"},
        }])
        self.assertEqual(0, rc)
        self.assertNotEqual(receipt["repos"][0]["local_head"], receipt["repos"][0]["remote_head"])
        self.assertEqual(receipt["repos"][0]["remote_head"], receipt["repos"][0]["proposed_baseline"])
        self.assertIn("local_remote_head_mismatch", [item["kind"] for item in receipt["differences"]])

    def test_missing_proposed_baseline_fails_closed(self) -> None:
        work, _ = self.repo("missing")
        rc, receipt = self.collect([{
            "path": str(work), "remote": "origin",
            "behavior_sources": ["README.md"], "behavior_summary": "repo behavior",
            "deployed": {"version": None, "unavailable_reason": "not configured"},
        }])
        self.assertEqual(2, rc)
        self.assertEqual("blocked", receipt["status"])

    def test_nonexistent_explicit_commit_fails_closed(self) -> None:
        work, _ = self.repo("missing-commit")
        rc, receipt = self.collect([{
            "path": str(work), "remote": "origin", "proposed_baseline": "f" * 40,
            "behavior_sources": ["README.md"], "behavior_summary": "repo behavior",
            "deployed": {"version": None, "unavailable_reason": "not configured"},
        }])
        self.assertEqual(2, rc)
        self.assertEqual("blocked", receipt["status"])

    def test_behavior_sources_are_bound_to_selected_tree_not_dirty_worktree(self) -> None:
        work, _ = self.repo("tree-source")
        selected = git(work, "rev-parse", "HEAD")
        selected_blob = git(work, "rev-parse", f"{selected}:README.md")
        (work / "README.md").write_text("dirty replacement\n", encoding="utf-8")
        rc, receipt = self.collect([{
            "path": str(work), "remote": "origin", "proposed_baseline": selected,
            "behavior_sources": ["README.md"], "behavior_summary": "repo behavior",
            "deployed": {"version": None, "unavailable_reason": "not configured"},
        }])
        self.assertEqual(0, rc)
        observation = receipt["repos"][0]
        self.assertEqual(selected_blob, observation["behavior_authority"]["sources"][0]["blob"])
        self.assertEqual(git(work, "rev-parse", f"{selected}^{{tree}}"), observation["proposed_baseline_tree"])

    def test_credential_bearing_remote_is_never_persisted(self) -> None:
        unsafe_remotes = (
            "https://user:password@host.example.com/repo.git",
            "ssh://user:password@host.example.com/repo.git",
            "https://host.example.com/repo.git?token=value",
            "ssh://host.example.com/repo.git#credential",
        )
        for index, unsafe in enumerate(unsafe_remotes):
            with self.subTest(remote=index):
                work, _ = self.repo(f"unsafe-{index}")
                git(work, "checkout", "--detach")
                git(work, "remote", "set-url", "origin", unsafe)
                rc, receipt = self.collect([{
                    "path": str(work), "remote": "origin", "proposed_baseline": "local_head",
                    "behavior_sources": ["README.md"], "behavior_summary": "repo behavior",
                    "deployed": {"version": None, "unavailable_reason": "not configured"},
                }])
                serialized = json.dumps(receipt, sort_keys=True)
                self.assertEqual(0, rc)
                self.assertIsNone(receipt["repos"][0]["remote_url"])
                self.assertEqual("remote_url_unsafe", receipt["repos"][0]["remote_url_unavailable_reason"])
                self.assertNotIn(unsafe, serialized)
                self.assertNotIn("password", serialized)
                self.assertNotIn("token=value", serialized)

    def test_product_route_and_repo_behavior_conflict_is_explicit(self) -> None:
        work, _ = self.repo("conflict")
        order = {
            "product": "fixture", "target_environment": "prod",
            "product_route": {
                "source": "products/fixture.md", "account": "release-account",
                "environment": "prod", "version_routing": "release-endpoint",
                "release_policy": "full-with-confirmation",
            },
            "authority_conflicts": [{
                "subject": "release route versus repository behavior",
                "product_route_value": "prod release via account route",
                "repo_behavior_value": "repository spec says feature remains local-only",
            }],
            "repos": [{
                "path": str(work), "remote": "origin", "proposed_baseline": "local_head",
                "behavior_sources": ["README.md"], "behavior_summary": "feature remains local-only",
                "deployed": {"version": None, "unavailable_reason": "no prod deployment"},
            }],
        }
        completed = subprocess.run(
            ["python3", str(SCRIPT), "collect"], input=json.dumps(order), text=True,
            capture_output=True, check=False,
        )
        receipt = json.loads(completed.stdout)
        self.assertEqual(0, completed.returncode)
        self.assertEqual("product_config", receipt["authority"]["routing"])
        self.assertEqual("selected_baseline_repo_code_spec_adr", receipt["authority"]["behavior"])
        self.assertEqual("authority_conflict", receipt["differences"][0]["kind"])
        self.assertTrue(receipt["writer_selection_blocked"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
