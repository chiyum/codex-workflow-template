#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/cleanup-task.sh"


class CleanupTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = tempfile.TemporaryDirectory(prefix="cleanup-task-test-")
        self.temp = Path(self.context.name)
        self.home = self.temp / "home"
        self.root = self.temp / "codex-home"
        for directory in (self.home, self.root / "acceptance", self.root / "state", self.root / "worktrees"):
            directory.mkdir(parents=True, exist_ok=True)
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "CODEX_HOME": str(self.root),
            "CLEANUP_ROOTS": str(self.root / "unused-legacy-root"),
        }

    def tearDown(self) -> None:
        self.context.cleanup()

    def write_state(self, slug: str, status: str) -> None:
        (self.root / "state" / f"{slug}.json").write_text(
            json.dumps({"task": slug, "status": status}), encoding="utf-8",
        )

    def run_cleanup(self, slug: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT), slug, *args],
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def snapshot(self) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        for path in sorted(self.root.rglob("*")):
            relative = path.relative_to(self.root).as_posix()
            if path.is_file():
                rows.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
            elif path.is_dir():
                rows.append((relative + "/", "directory"))
        return rows

    def make_targets(self, slug: str) -> tuple[Path, Path]:
        evidence = self.root / "acceptance" / slug / "evidence"
        runtime = self.root / "state" / slug / "runtime"
        for directory in (evidence, runtime):
            directory.mkdir(parents=True)
            (directory / "artifact.txt").write_text(directory.name, encoding="utf-8")
        return evidence, runtime

    def test_active_state_rejects_without_changes(self) -> None:
        slug = "active-task"
        self.write_state(slug, "running")
        self.make_targets(slug)
        before = self.snapshot()
        completed = self.run_cleanup(slug, "--apply")
        self.assertEqual(1, completed.returncode)
        self.assertIn("status=running", completed.stderr)
        self.assertEqual(before, self.snapshot())

    def test_missing_state_rejects_without_changes(self) -> None:
        slug = "missing-state-task"
        self.make_targets(slug)
        before = self.snapshot()
        completed = self.run_cleanup(slug, "--apply")
        self.assertEqual(1, completed.returncode)
        self.assertIn("目標不存在", completed.stderr)
        self.assertEqual(before, self.snapshot())

    def test_invalid_state_rejects_without_changes(self) -> None:
        slug = "invalid-state-task"
        (self.root / "state" / f"{slug}.json").write_text("{invalid", encoding="utf-8")
        self.make_targets(slug)
        before = self.snapshot()
        completed = self.run_cleanup(slug, "--apply")
        self.assertEqual(1, completed.returncode)
        self.assertIn("不是有效 JSON", completed.stderr)
        self.assertEqual(before, self.snapshot())

    def test_dry_run_never_writes_manifest_log_or_deletes(self) -> None:
        slug = "dry-run-task"
        self.write_state(slug, "done")
        self.make_targets(slug)
        before = self.snapshot()
        completed = self.run_cleanup(slug)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("[dry-run]", completed.stdout)
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.root / "state/cleanup.log").exists())

    def test_done_removes_only_allowed_targets_and_keeps_manifest_log_and_sentinels(self) -> None:
        slug = "done-task"
        self.write_state(slug, "done")
        evidence, runtime = self.make_targets(slug)
        expected_hash = hashlib.sha256((evidence / "artifact.txt").read_bytes()).hexdigest()
        state_keep = self.root / "state" / slug / "keep.txt"
        state_keep.write_text("keep", encoding="utf-8")
        outside = self.root / "outside-sentinel.txt"
        outside.write_text("outside", encoding="utf-8")
        completed = self.run_cleanup(slug, "--apply")
        self.assertEqual(0, completed.returncode, completed.stderr)
        for target in (evidence, runtime):
            self.assertFalse(target.exists())
        manifest = self.root / "acceptance" / slug / "evidence-manifest.txt"
        self.assertIn(expected_hash, manifest.read_text(encoding="utf-8"))
        log = (self.root / "state/cleanup.log").read_text(encoding="utf-8")
        self.assertIn("acceptance/done-task/evidence", log)
        self.assertIn("state/done-task/runtime", log)
        self.assertEqual("keep", state_keep.read_text(encoding="utf-8"))
        self.assertEqual("outside", outside.read_text(encoding="utf-8"))

    def test_plain_same_name_directory_is_preserved(self) -> None:
        slug = "plain-directory-task"
        self.write_state(slug, "done")
        worktree = self.root / "worktrees" / slug
        worktree.mkdir(parents=True)
        artifact = worktree / "artifact.txt"
        artifact.write_text("keep", encoding="utf-8")
        completed = self.run_cleanup(slug, "--apply")
        self.assertEqual(1, completed.returncode)
        self.assertIn("缺少可驗證的 .git", completed.stderr)
        self.assertEqual("keep", artifact.read_text(encoding="utf-8"))

    def test_output_symlink_is_rejected_without_touching_outside_sentinel(self) -> None:
        slug = "output-boundary-task"
        self.write_state(slug, "done")
        evidence = self.root / "acceptance" / slug / "evidence"
        evidence.mkdir(parents=True)
        (evidence / "artifact.txt").write_text("artifact", encoding="utf-8")
        outside = self.temp / "outside-sentinel.txt"
        outside.write_text("outside", encoding="utf-8")
        (evidence.parent / "evidence-manifest.txt").symlink_to(outside)
        completed = self.run_cleanup(slug, "--apply")
        self.assertEqual(1, completed.returncode)
        self.assertIn("輸出不是單一 regular file", completed.stderr)
        self.assertTrue(evidence.exists())
        self.assertEqual("outside", outside.read_text(encoding="utf-8"))

    def init_worktree(self, slug: str, primary_branch: str = "main") -> tuple[Path, Path]:
        repo = self.temp / f"repo-{slug}"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", primary_branch], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Workflow Test"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "maintainer@example.com"], cwd=repo, check=True)
        (repo / "tracked.txt").write_text("main", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=repo, check=True)
        worktree = self.root / "worktrees" / slug
        subprocess.run(["git", "worktree", "add", "-q", "-b", slug, str(worktree)], cwd=repo, check=True)
        return repo, worktree

    def test_dirty_worktree_is_preserved(self) -> None:
        slug = "dirty-task"
        self.write_state(slug, "done")
        _repo, worktree = self.init_worktree(slug)
        (worktree / "tracked.txt").write_text("dirty", encoding="utf-8")
        completed = self.run_cleanup(slug, "--apply")
        self.assertEqual(1, completed.returncode)
        self.assertIn("未 commit", completed.stderr)
        self.assertTrue(worktree.exists())

    def test_unmerged_worktree_is_preserved(self) -> None:
        slug = "unmerged-task"
        self.write_state(slug, "done")
        _repo, worktree = self.init_worktree(slug)
        (worktree / "branch.txt").write_text("branch", encoding="utf-8")
        subprocess.run(["git", "add", "branch.txt"], cwd=worktree, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "branch work"], cwd=worktree, check=True)
        completed = self.run_cleanup(slug, "--apply")
        self.assertEqual(1, completed.returncode)
        self.assertIn("尚未合進 main", completed.stderr)
        self.assertTrue(worktree.exists())

    def test_detached_worktree_is_preserved(self) -> None:
        slug = "detached-task"
        self.write_state(slug, "done")
        _repo, worktree = self.init_worktree(slug)
        subprocess.run(["git", "checkout", "-q", "--detach"], cwd=worktree, check=True)
        completed = self.run_cleanup(slug, "--apply")
        self.assertEqual(1, completed.returncode)
        self.assertIn("不在具名 branch", completed.stderr)
        self.assertTrue(worktree.exists())

    def test_worktree_without_main_is_preserved(self) -> None:
        slug = "no-main-task"
        self.write_state(slug, "done")
        _repo, worktree = self.init_worktree(slug, primary_branch="trunk")
        completed = self.run_cleanup(slug, "--apply")
        self.assertEqual(1, completed.returncode)
        self.assertIn("沒有可驗證的 main branch", completed.stderr)
        self.assertTrue(worktree.exists())

    def test_merged_git_worktree_is_removed_but_branch_is_retained(self) -> None:
        slug = "merged-task"
        self.write_state(slug, "done")
        repo, worktree = self.init_worktree(slug)
        real_git = shutil.which("git")
        self.assertIsNotNone(real_git)
        bin_dir = self.temp / "bin"
        bin_dir.mkdir()
        git_log = self.temp / "git-calls.log"
        fake_git = bin_dir / "git"
        fake_git.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> {shlex.quote(str(git_log))}\n"
            "case \" $* \" in *\" worktree prune \"*) exit 91 ;; esac\n"
            f"exec {shlex.quote(real_git or 'git')} \"$@\"\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o755)
        self.env["PATH"] = f"{bin_dir}{os.pathsep}{self.env.get('PATH', '')}"
        completed = self.run_cleanup(slug, "--apply")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertFalse(worktree.exists())
        calls = git_log.read_text(encoding="utf-8")
        self.assertIn("worktree remove", calls)
        self.assertNotIn("worktree prune", calls)
        branch = subprocess.run(
            ["git", "show-ref", "--verify", f"refs/heads/{slug}"],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, branch.returncode)
        self.assertIn("branch 保留", (self.root / "state/cleanup.log").read_text(encoding="utf-8"))

    def test_busy_target_is_preserved(self) -> None:
        slug = "busy-task"
        self.write_state(slug, "done")
        evidence = self.root / "acceptance" / slug / "evidence"
        evidence.mkdir(parents=True)
        held = evidence / "held.txt"
        held.write_text("busy", encoding="utf-8")
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys,time; f=open(sys.argv[1]); print('ready', flush=True); time.sleep(30)",
                str(held),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual("ready", process.stdout.readline().strip())
            time.sleep(0.1)
            completed = self.run_cleanup(slug, "--apply")
            self.assertEqual(1, completed.returncode)
            if shutil.which("lsof") is None:
                self.assertIn("找不到 lsof", completed.stderr)
            else:
                self.assertIn("程序開啟", completed.stderr)
            self.assertTrue(evidence.exists())
            self.assertEqual("busy", held.read_text(encoding="utf-8"))
        finally:
            process.terminate()
            process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
