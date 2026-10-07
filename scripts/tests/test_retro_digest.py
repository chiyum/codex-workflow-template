import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

# 載入受測腳本時不寫 __pycache__，避免在制度 repo 留下 binary 產物。
sys.dont_write_bytecode = True


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "retro-digest.py"


class RetroDigestTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home = Path(self.temp_dir.name)
        self.knowledge = self.home / ".codex" / "knowledge"
        self.runs = self.home / ".codex" / "run-metrics" / "runs"
        self.knowledge.mkdir(parents=True)
        self.runs.mkdir(parents=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write(self, relative_path, content="fixture", mtime=200.0):
        path = self.knowledge / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        os.utime(path, (mtime, mtime))
        return path

    def _load_module(self):
        previous_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        try:
            spec = importlib.util.spec_from_file_location("retro_digest_fixture", SCRIPT_PATH)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        finally:
            if previous_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = previous_home

    def _run_cli(self, *args):
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), *args],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_cards_include_root_and_nested_harness_with_relative_paths(self):
        root_card = self._write("same.md")
        nested_card = self._write("harness/nested/same.md")
        self._write("old.md", mtime=99.0)

        module = self._load_module()
        cards = module.new_knowledge_cards(100.0)

        self.assertEqual(cards, [str(nested_card), str(root_card)])
        output = self._run_cli().stdout
        self.assertIn("- same.md", output)
        self.assertIn("- harness/nested/same.md", output)

    def test_cards_exclude_governance_playbooks_and_archive(self):
        included = self._write("harness/proposal.md")
        for relative_path in (
            "INDEX.md",
            "README.md",
            "ROUTER.md",
            "SHARED_CONTEXT_POLICY.md",
            "playbooks/playbook.md",
            "archive/old.md",
            "harness/archive/old.md",
            "harness/playbooks/playbook.md",
            "harness/nested/README.md",
        ):
            self._write(relative_path)

        module = self._load_module()

        self.assertEqual(module.new_knowledge_cards(0.0), [str(included)])

    def test_count_and_mark_keep_existing_behavior_in_temp_home(self):
        (self.runs / "with-verdict.json").write_text(
            json.dumps({"verdict": "pass"}), encoding="utf-8"
        )
        (self.runs / "without-verdict.json").write_text("{}", encoding="utf-8")

        self.assertEqual(self._run_cli("--count").stdout.strip(), "1")
        self.assertFalse((self.home / ".codex" / "run-metrics" / ".last-retro").exists())

        self.assertEqual(self._run_cli("--mark").stdout.strip(), "已更新 .last-retro")
        self.assertTrue((self.home / ".codex" / "run-metrics" / ".last-retro").exists())


if __name__ == "__main__":
    unittest.main()
