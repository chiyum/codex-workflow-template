#!/usr/bin/env python3
"""驗證公開版 aiuse 的 managed surfaces 與帳號狀態隔離。"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
AIUSE = ROOT / "scripts/aiuse"
FAKE_CODEX = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["AIUSE_TEST_TRACE"], "a", encoding="utf-8") as handle:
    codex_home = os.environ.get("CODEX_HOME", "")
    handle.write(json.dumps({"argv": sys.argv[1:], "codex_home": codex_home, "auth_is_symlink": os.path.islink(os.path.join(codex_home, "auth.json"))}) + "\\n")
"""


class AiuseProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = tempfile.TemporaryDirectory(prefix="public-aiuse-test.")
        self.root = Path(self.context.name).resolve()
        self.home = self.root / "home"
        self.source = self.home / ".codex"
        self.bin = self.root / "bin"
        self.trace = self.root / "trace.jsonl"
        self.source.mkdir(parents=True)
        for item in ("skills", "agents", "agent-guides", "products", "knowledge", "scripts", "workflows", "rules"):
            (self.source / item).mkdir()
        for item in ("AGENTS.md", "config.toml", "workflow-source.toml", "manifest.tsv"):
            (self.source / item).write_text(f"{item}\n", encoding="utf-8")
        self.bin.mkdir()
        codex = self.bin / "codex"
        codex.write_text(FAKE_CODEX, encoding="utf-8")
        codex.chmod(0o755)
        self.environment = os.environ.copy()
        self.environment.update({
            "HOME": str(self.home),
            "PATH": f"{self.bin}:{self.environment['PATH']}",
            "AIUSE_TEST_TRACE": str(self.trace),
        })

    def tearDown(self) -> None:
        self.context.cleanup()

    def run_aiuse(self, *args: str) -> subprocess.CompletedProcess[str]:
        self.trace.unlink(missing_ok=True)
        return subprocess.run(
            [str(AIUSE), *args],
            check=False,
            capture_output=True,
            text=True,
            env=self.environment,
            timeout=5,
        )

    def assert_rejected(self, *args: str) -> subprocess.CompletedProcess[str]:
        completed = self.run_aiuse(*args)
        self.assertNotEqual(0, completed.returncode)
        self.assertFalse(self.trace.exists())
        return completed

    def assert_runtime_replace_race_rejected(self, phase: str) -> None:
        self.trace.unlink(missing_ok=True)
        profile = self.home / ".ai-profiles/codex/work"
        runtime = profile / "future-runtime"
        runtime.mkdir(parents=True)
        target = runtime / "000-target"
        target.write_text("safe\n", encoding="utf-8")
        external = self.root / f"external-{phase}"
        replacement = self.root / f"replacement-{phase}"
        external.write_text("shared\n", encoding="utf-8")
        os.link(external, replacement)

        ready_read, ready_write = os.pipe()
        continue_read, continue_write = os.pipe()
        environment = self.environment.copy()
        environment.update(
            {
                "AIUSE_TEST_RUNTIME_RACE_PHASE": phase,
                "AIUSE_TEST_RUNTIME_RACE_TARGET": "future-runtime/000-target",
                "AIUSE_TEST_RUNTIME_RACE_READY_FD": str(ready_write),
                "AIUSE_TEST_RUNTIME_RACE_CONTINUE_FD": str(continue_read),
            }
        )
        process = subprocess.Popen(
            [str(AIUSE), "codex", "work", "status"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
            pass_fds=(ready_write, continue_read),
        )
        os.close(ready_write)
        os.close(continue_read)
        try:
            ready, _, _ = select.select([ready_read], [], [], 5)
            if not ready or os.read(ready_read, 1) != b"1":
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
                self.fail(f"runtime race hook 未就緒：stdout={stdout} stderr={stderr}")
            os.replace(replacement, target)
            os.write(continue_write, b"1")
            stdout, stderr = process.communicate(timeout=5)
        finally:
            os.close(ready_read)
            os.close(continue_write)
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

        self.assertNotEqual(0, process.returncode, msg=f"stdout={stdout}\nstderr={stderr}")
        self.assertEqual(2, os.lstat(target).st_nlink)
        self.assertFalse(self.trace.exists())

    def assert_auth_replace_at_launch_rejected(self) -> None:
        self.trace.unlink(missing_ok=True)
        profile = self.home / ".ai-profiles/codex/work"
        profile.mkdir(parents=True)
        auth = profile / "auth.json"
        auth.write_text('{"profile":"work"}\n', encoding="utf-8")
        external = self.root / "external-auth-race.json"
        external.write_text('{"profile":"external"}\n', encoding="utf-8")
        external_digest = hashlib.sha256(external.read_bytes()).digest()
        replacement = self.root / "auth-replacement"
        replacement.symlink_to(external)

        ready_read, ready_write = os.pipe()
        continue_read, continue_write = os.pipe()
        environment = self.environment.copy()
        environment.update(
            {
                "AIUSE_TEST_AUTH_RACE_PHASE": "launch",
                "AIUSE_TEST_AUTH_RACE_READY_FD": str(ready_write),
                "AIUSE_TEST_AUTH_RACE_CONTINUE_FD": str(continue_read),
            }
        )
        process = subprocess.Popen(
            [str(AIUSE), "codex", "work", "status"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
            pass_fds=(ready_write, continue_read),
        )
        os.close(ready_write)
        os.close(continue_read)
        try:
            ready, _, _ = select.select([ready_read], [], [], 5)
            if not ready or os.read(ready_read, 1) != b"1":
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
                self.fail(f"auth race hook 未就緒：stdout={stdout} stderr={stderr}")
            os.replace(replacement, auth)
            os.write(continue_write, b"1")
            stdout, stderr = process.communicate(timeout=5)
        finally:
            os.close(ready_read)
            os.close(continue_write)
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

        self.assertNotEqual(0, process.returncode, msg=f"stdout={stdout}\nstderr={stderr}")
        self.assertTrue(auth.is_symlink())
        self.assertEqual(external_digest, hashlib.sha256(external.read_bytes()).digest())
        self.assertFalse(self.trace.exists())

    def test_new_profile_gets_complete_managed_surfaces_and_forwards_yolo(self) -> None:
        completed = self.run_aiuse("codex", "work", "--yolo")
        self.assertEqual(0, completed.returncode, completed.stderr)
        profile = self.home / ".ai-profiles/codex/work"
        for item in ("AGENTS.md", "config.toml", "agents", "agent-guides", "knowledge", "scripts", "workflows", "workflow-source.toml", "manifest.tsv"):
            with self.subTest(item=item):
                target = profile / item
                self.assertTrue(target.is_symlink())
                self.assertTrue(target.samefile(self.source / item))
        record = json.loads(self.trace.read_text(encoding="utf-8").strip())
        self.assertEqual(str(profile), record["codex_home"])
        self.assertEqual(["-c", 'cli_auth_credentials_store="file"', "--yolo"], record["argv"])

    def test_local_conflict_fails_before_any_managed_link(self) -> None:
        profile = self.home / ".ai-profiles/codex/work"
        (profile / "scripts").mkdir(parents=True)
        marker = profile / "scripts/owned"
        marker.write_text("keep\n", encoding="utf-8")
        self.assert_rejected("codex", "work", "status")
        self.assertEqual("keep\n", marker.read_text(encoding="utf-8"))
        self.assertFalse((profile / "AGENTS.md").exists())

    def test_auth_symlink_hardlink_and_runtime_symlink_are_rejected(self) -> None:
        profile = self.home / ".ai-profiles/codex/work"
        profile.mkdir(parents=True)
        external = self.root / "external-auth.json"
        external.write_text("{}\n", encoding="utf-8")
        (profile / "auth.json").symlink_to(external)
        self.assert_rejected("codex", "work", "status")
        (profile / "auth.json").unlink()
        os.link(external, profile / "auth.json")
        self.assert_rejected("codex", "work", "status")
        (profile / "auth.json").unlink()
        sessions = self.root / "external-sessions"
        sessions.mkdir()
        (profile / "sessions").symlink_to(sessions)
        self.assert_rejected("codex", "work", "status")
        (profile / "sessions").unlink()
        external_history = self.root / "external-history.jsonl"
        external_history.write_text("state\n", encoding="utf-8")
        os.link(external_history, profile / "history.jsonl")
        self.assert_rejected("codex", "work", "status")

    def test_nested_runtime_links_and_special_items_are_rejected(self) -> None:
        profile = self.home / ".ai-profiles/codex/work"
        nested = profile / "sessions/2026/08"
        nested.mkdir(parents=True)
        external_runtime = self.root / "external-runtime.jsonl"
        external_runtime.write_text("state\n", encoding="utf-8")

        nested_hard_link = nested / "rollout.jsonl"
        os.link(external_runtime, nested_hard_link)
        self.assert_rejected("codex", "work", "status")
        nested_hard_link.unlink()

        nested_symlink = nested / "latest.jsonl"
        nested_symlink.symlink_to(external_runtime)
        self.assert_rejected("codex", "work", "status")

        nested_symlink.unlink()
        unknown_runtime = profile / "future-runtime/nested"
        unknown_runtime.mkdir(parents=True)
        unknown_link = unknown_runtime / "shared-state"
        unknown_link.symlink_to(external_runtime)
        self.assert_rejected("codex", "work", "status")

        unknown_link.unlink()
        unknown_socket = unknown_runtime / "runtime.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as runtime_socket:
            original_directory = Path.cwd()
            try:
                os.chdir(profile)
                runtime_socket.bind("future-runtime/nested/runtime.sock")
            finally:
                os.chdir(original_directory)
            self.assert_rejected("codex", "work", "status")
        unknown_socket.unlink()

        os.mkfifo(unknown_runtime / "runtime.pipe")
        self.assert_rejected("codex", "work", "status")

    def test_unlisted_runtime_hard_links_are_rejected(self) -> None:
        profile = self.home / ".ai-profiles/codex/work"
        profile.mkdir(parents=True)
        external_runtime = self.root / "external-runtime.sqlite"
        external_runtime.write_text("state\n", encoding="utf-8")

        for name in ("memories_1.sqlite", "models_cache.json"):
            with self.subTest(name=name):
                linked_runtime = profile / name
                os.link(external_runtime, linked_runtime)
                self.assert_rejected("codex", "work", "status")
                linked_runtime.unlink()

    def test_regular_runtime_replace_during_pinned_open_is_rejected(self) -> None:
        self.assert_runtime_replace_race_rejected("before-regular-open")

    def test_regular_runtime_replace_between_bounded_scans_is_rejected(self) -> None:
        self.assert_runtime_replace_race_rejected("between-scans")

    def test_nested_regular_runtime_tree_is_allowed(self) -> None:
        profile = self.home / ".ai-profiles/codex/work"
        nested = profile / "sessions/2026/08"
        nested.mkdir(parents=True)
        (nested / "rollout.jsonl").write_text("state\n", encoding="utf-8")
        (profile / "memories_1.sqlite").write_text("state\n", encoding="utf-8")
        (profile / "models_cache.json").write_text("{}\n", encoding="utf-8")

        completed = self.run_aiuse("codex", "work", "status")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertTrue(self.trace.exists())

    def test_exact_profile_control_socket_is_allowed_but_wrong_type_is_rejected(self) -> None:
        profile = self.home / ".ai-profiles/codex/work"
        control_directory = profile / "app-server-control"
        control_directory.mkdir(parents=True)
        control_socket = control_directory / "app-server-control.sock"

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            original_directory = Path.cwd()
            try:
                os.chdir(profile)
                server.bind("app-server-control/app-server-control.sock")
            finally:
                os.chdir(original_directory)
            completed = self.run_aiuse("codex", "work", "status")
            self.assertEqual(0, completed.returncode, completed.stderr)

        control_socket.unlink()
        control_socket.write_text("not a socket\n", encoding="utf-8")
        self.assert_rejected("codex", "work", "status")

    def test_profile_and_source_ancestor_symlinks_fail_before_changes(self) -> None:
        profiles_parent = self.home / ".ai-profiles"
        profiles_parent.mkdir()
        external_profiles = self.root / "external-profiles"
        external_profiles.mkdir()
        (profiles_parent / "codex").symlink_to(external_profiles, target_is_directory=True)
        self.assert_rejected("codex", "work", "status")
        self.assertEqual([], list(external_profiles.iterdir()))

        (profiles_parent / "codex").unlink()
        profiles_parent.rmdir()
        external_scripts = self.root / "external-scripts"
        external_scripts.mkdir()
        (self.source / "scripts").rmdir()
        (self.source / "scripts").symlink_to(external_scripts, target_is_directory=True)
        self.assert_rejected("codex", "work", "status")
        self.assertFalse(profiles_parent.exists())

    def test_link_failure_rolls_back_every_new_profile_surface(self) -> None:
        counter = self.root / "ln-count"
        wrapper = self.bin / "ln"
        wrapper.write_text(
            "#!/bin/sh\n"
            "count=0\n"
            "[ ! -f \"$AIUSE_LN_COUNTER\" ] || count=$(cat \"$AIUSE_LN_COUNTER\")\n"
            "count=$((count+1))\n"
            "printf '%s' \"$count\" > \"$AIUSE_LN_COUNTER\"\n"
            "[ \"$count\" -ne 2 ] || exit 99\n"
            "exec /bin/ln \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        self.environment["AIUSE_LN_COUNTER"] = str(counter)
        completed = self.assert_rejected("codex", "work", "status")
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("2", counter.read_text(encoding="utf-8"))
        self.assertFalse((self.home / ".ai-profiles").exists())

    def test_storage_override_is_rejected_but_prompt_after_boundary_is_forwarded(self) -> None:
        cases = {
            "separated-short-quoted": ("-c", '"cli_auth_credentials_store"="keyring"'),
            "separated-long-literal": ("--config", "'cli_auth_credentials_store' = \"keyring\""),
            "equals-short-escaped": (r'-c="cli_auth_credentials_\u0073tore"="keyring"',),
            "equals-long-whitespace": ('--config= \t"cli_auth_credentials_store" \t= "keyring"',),
            "sticky-short-bare": ('-ccli_auth_credentials_store="keyring"',),
        }
        for label, arguments in cases.items():
            with self.subTest(label=label):
                rejected = self.assert_rejected("codex", "work", *arguments)
                self.assertIn("不可覆寫 cli_auth_credentials_store", rejected.stderr)

        completed = self.run_aiuse("codex", "work", "-c", "features.example=true", "status")
        self.assertEqual(0, completed.returncode, completed.stderr)
        record = json.loads(self.trace.read_text(encoding="utf-8").strip())
        self.assertIn("features.example=true", record["argv"])

        completed = self.run_aiuse("codex", "work", "--", 'cli_auth_credentials_store="example"')
        self.assertEqual(0, completed.returncode, completed.stderr)
        record = json.loads(self.trace.read_text(encoding="utf-8").strip())
        self.assertEqual("--", record["argv"][2])

    def test_auth_replace_at_launch_barrier_is_rejected(self) -> None:
        self.assert_auth_replace_at_launch_rejected()

    def test_regular_auth_and_profile_cooperative_lock(self) -> None:
        profile = self.home / ".ai-profiles/codex/work"
        profile.mkdir(parents=True)
        auth = profile / "auth.json"
        original = b'{"profile":"work"}\n'
        auth.write_bytes(original)
        completed = self.run_aiuse("codex", "work", "status")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(original, auth.read_bytes())
        self.assertFalse(auth.is_symlink())
        self.assertEqual(1, os.lstat(auth).st_nlink)

        directory_fd = os.open(profile, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            fcntl.flock(directory_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assert_rejected("codex", "work", "status")
        finally:
            os.close(directory_fd)

    def test_managed_link_race_cannot_silently_install_wrong_target(self) -> None:
        wrapper = self.bin / "ln"
        wrapper.write_text(
            "#!/bin/sh\n"
            "target=\n"
            "for argument in \"$@\"; do target=$argument; done\n"
            "case \"$AIUSE_LINK_RACE_KIND\" in\n"
            "  file) printf 'race\\n' > \"$target\" ;;\n"
            "  symlink) /bin/ln -s \"$AIUSE_LINK_RACE_SOURCE\" \"$target\" ;;\n"
            "esac\n"
            "exit 0\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        wrong_source = self.root / "wrong-source"
        wrong_source.write_text("wrong\n", encoding="utf-8")
        self.environment["AIUSE_LINK_RACE_SOURCE"] = str(wrong_source)
        for kind in ("file", "symlink"):
            with self.subTest(kind=kind):
                self.environment["AIUSE_LINK_RACE_KIND"] = kind
                profile_name = f"race-{kind}"
                self.assert_rejected("codex", profile_name, "status")
                target = self.home / f".ai-profiles/codex/{profile_name}/AGENTS.md"
                self.assertTrue(target.exists())
                expected = (self.source / "AGENTS.md").resolve()
                self.assertFalse(target.is_symlink() and target.resolve() == expected)

    def test_danger_alias_is_deduplicated(self) -> None:
        completed = self.run_aiuse(
            "codex",
            "work",
            "--dangerously-skip-permissions",
            "--dangerously-bypass-approvals-and-sandbox",
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        record = json.loads(self.trace.read_text(encoding="utf-8").strip())
        self.assertEqual(1, record["argv"].count("--dangerously-bypass-approvals-and-sandbox"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
