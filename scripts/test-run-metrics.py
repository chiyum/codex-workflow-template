#!/usr/bin/env python3
"""metrics v2 單 transcript 精確計數、多 transcript fail-closed 與 runtime 回歸測試。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "scripts/collect-run-metrics.py"
REPORTER = ROOT / "scripts/report-runs.py"


def write_jsonl(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")


def usage(input_tokens: int, output_tokens: int = 1, cached: int = 0, reasoning: int = 0) -> dict:
    return {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {"total_token_usage": {
                "input_tokens": input_tokens,
                "cached_input_tokens": cached,
                "output_tokens": output_tokens,
                "reasoning_output_tokens": reasoning,
            }},
        },
    }


def spawn(call_id: str, child_id: str | None, model: str = "gpt-5.6-terra", effort: str = "medium") -> list[dict]:
    result = [{
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "name": "spawn_agent",
            "call_id": call_id,
            "input": json.dumps({
                "agent_type": "qa", "model": model,
                "reasoning_effort": effort, "fork_turns": "none",
            }),
        },
    }]
    if child_id is not None:
        result.append({
            "type": "event_msg",
            "payload": {
                "type": "sub_agent_activity", "event_id": call_id,
                "agent_thread_id": child_id, "agent_path": "/root/qa", "kind": "started",
            },
        })
    return result


class RunMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = tempfile.TemporaryDirectory(prefix="run-metrics-v2.")
        self.home = Path(self.context.name) / "codex-home"
        self.sessions = self.home / "sessions/2026/08/02"
        self.runs = self.home / "run-metrics/runs"

    def tearDown(self) -> None:
        self.context.cleanup()

    def collect(self, slug: str, session: str, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3", str(COLLECTOR), "--slug", slug, "--sessions", session,
                "--codex-home", str(self.home), "--runs-dir", str(self.runs), *extra,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def read_run(self, slug: str) -> dict:
        return json.loads((self.runs / f"20260802-{slug}.json").read_text(encoding="utf-8"))

    def write_main(self, session_id: str, values: list[dict]) -> None:
        write_jsonl(self.sessions / f"rollout-{session_id}.jsonl", [
            {"type": "session_meta", "payload": {"id": session_id, "agent_path": "/root", "history_mode": "all"}},
            {"type": "turn_context", "payload": {"model": "gpt-5.6-sol", "effort": "medium"}},
            *values,
        ])

    def write_child(
        self, session_id: str, values: list[dict], model: str = "gpt-5.6-terra",
        effort: str = "medium", parent_id: str | None = None,
    ) -> None:
        write_jsonl(self.sessions / f"rollout-{session_id}.jsonl", [
            {"type": "session_meta", "payload": {
                "id": session_id, "agent_role": "qa", "agent_path": "/root/qa",
                "history_mode": "none", "parent_thread_id": parent_id,
            }},
            {"type": "turn_context", "payload": {"model": model, "effort": effort}},
            *values,
        ])

    def test_multi_transcript_tree_keeps_raw_counters_without_sum_or_max(self) -> None:
        main_id, child_a, child_b = "main-session", "child-a", "child-b"
        self.write_main(main_id, [
            usage(1_000, 10, 400, 4),
            *spawn("spawn-a", child_a),
            *spawn("spawn-b", child_b),
            usage(18_856_221, 15, 500, 5),
            {"type": "event_msg", "payload": {
                "type": "sub_agent_activity", "event_id": "follow-1",
                "agent_thread_id": child_a, "agent_path": "/root/qa", "kind": "interacted",
            }},
        ])
        # 真 transcript 的各 counter 初值與增長互不構成安全的 sum/max 關係。
        self.write_child(child_a, [
            *spawn("spawn-a", child_a), *spawn("spawn-b", child_b),
            usage(33_285, 3), usage(7_872_441, 7),
        ], parent_id=main_id)
        self.write_child(child_b, [usage(12_069_836, 6), usage(15_000_000, 9)], parent_id=main_id)
        completed = self.collect(
            "v2-tree", main_id, "--risk-lane", "L1", "--requested-model", "gpt-5.6-sol",
            "--requested-effort", "medium", "--experiment", "router", "--variant", "after",
        )
        self.assertEqual(2, completed.returncode, completed.stderr)
        run = self.read_run("v2-tree")
        self.assertEqual("unsupported_token_attribution", run["collector_status"])
        self.assertIsNone(run["tokens"]["total"])
        self.assertIsNone(run["tokens"]["main_loop"])
        self.assertEqual({}, run["tokens_by_agent"])
        self.assertEqual({}, run["tokens_by_model"])
        self.assertEqual("unsupported_multi_transcript", run["token_attribution"]["status"])
        observations = {item["session_id"]: item for item in run["token_observations"]}
        self.assertEqual(1_000, observations[main_id]["first_usage"]["input_tokens"])
        self.assertEqual(18_856_221, observations[main_id]["final_usage"]["input_tokens"])
        self.assertEqual(33_285, observations[child_a]["first_usage"]["input_tokens"])
        self.assertEqual(7_872_441, observations[child_a]["final_usage"]["input_tokens"])
        self.assertEqual(12_069_836, observations[child_b]["first_usage"]["input_tokens"])
        self.assertEqual(15_000_000, observations[child_b]["final_usage"]["input_tokens"])
        self.assertEqual(main_id, observations[child_a]["forked_from_id"])
        self.assertEqual(["matched", "matched"], [item["validation_status"] for item in run["runtime"]["requested_agents"]])
        self.assertEqual(2, len(run["runtime"]["requested_agents"]))
        self.assertEqual("ok", run["runtime"]["validation_status"])
        self.assertEqual({"qa": 2}, run["agent_calls"])
        self.assertEqual(1, run["reuse"]["follow_up_events"])
        self.assertEqual(["none", "none"], run["fork"]["requested"])
        report = subprocess.run(
            ["python3", str(REPORTER), "--runs-dir", str(self.runs)],
            text=True, capture_output=True, check=True,
        ).stdout
        self.assertIn("—", report)
        self.assertIn("排除 1 筆", report)

    def test_single_no_history_child_session_has_exact_nonzero_tokens(self) -> None:
        child_id = "single-child"
        self.write_child(child_id, [
            # Codex child transcript 可觀察到 parent 的全域 started event，但本 session 沒有 spawn call。
            {"type": "event_msg", "payload": {
                "type": "sub_agent_activity", "event_id": "parent-call",
                "agent_thread_id": child_id, "agent_path": "/root/qa", "kind": "started",
            }},
            usage(33_285, 3), usage(7_872_441, 7),
        ])
        completed = self.collect("single-child", child_id, "--single-session-only")
        self.assertEqual(0, completed.returncode, completed.stderr)
        run = self.read_run("single-child")
        self.assertEqual("ok", run["collector_status"])
        self.assertEqual("exact_single_transcript", run["token_attribution"]["status"])
        self.assertEqual(7_872_441, run["tokens"]["total"]["input_tokens"])
        self.assertEqual(33_285, run["token_observations"][0]["first_usage"]["input_tokens"])
        self.assertEqual(7_872_441, run["token_observations"][0]["final_usage"]["input_tokens"])
        self.assertEqual({}, run["agent_calls"])
        self.assertEqual("ok", run["runtime"]["validation_status"])

    def test_explicit_multiple_transcripts_are_also_unsupported_for_tokens(self) -> None:
        child_a, child_b = "explicit-a", "explicit-b"
        self.write_child(child_a, [usage(10), usage(20)])
        self.write_child(child_b, [usage(30), usage(50)])
        completed = subprocess.run([
            "python3", str(COLLECTOR), "--slug", "explicit-multi",
            "--transcript", str(self.sessions / f"rollout-{child_a}.jsonl"),
            "--transcript", str(self.sessions / f"rollout-{child_b}.jsonl"),
            "--codex-home", str(self.home), "--runs-dir", str(self.runs),
        ], text=True, capture_output=True, check=False)
        self.assertEqual(2, completed.returncode, completed.stderr)
        run = self.read_run("explicit-multi")
        self.assertEqual("unsupported_token_attribution", run["collector_status"])
        self.assertIsNone(run["tokens"]["total"])
        self.assertEqual(2, len(run["token_observations"]))

    def test_single_session_override_rejects_main_or_inherited_history(self) -> None:
        main_id = "not-an-independent-child"
        self.write_main(main_id, [usage(10)])
        completed = self.collect("invalid-single", main_id, "--single-session-only")
        self.assertEqual(2, completed.returncode)
        run = self.read_run("invalid-single")
        self.assertEqual("unsupported_schema", run["collector_status"])
        self.assertIsNone(run["tokens"]["total"])

    def test_child_requested_effective_mismatch_fails_runtime(self) -> None:
        main_id, child_id = "mismatch-main", "mismatch-child"
        self.write_main(main_id, [*spawn("spawn-mismatch", child_id), usage(20)])
        self.write_child(child_id, [usage(20)], model="gpt-5.6-sol", effort="high")
        completed = self.collect("mismatch", main_id)
        self.assertEqual(2, completed.returncode)
        run = self.read_run("mismatch")
        self.assertEqual("unsupported_token_attribution", run["collector_status"])
        self.assertEqual("mismatch", run["runtime"]["requested_agents"][0]["validation_status"])

    def test_started_child_missing_transcript_fails_runtime(self) -> None:
        main_id = "missing-main"
        self.write_main(main_id, [*spawn("spawn-missing", "missing-child"), usage(20)])
        completed = self.collect("missing-child", main_id)
        self.assertEqual(2, completed.returncode)
        run = self.read_run("missing-child")
        self.assertEqual("incomplete_runtime", run["collector_status"])
        self.assertEqual("missing_child", run["runtime"]["requested_agents"][0]["validation_status"])

    def test_spawn_without_started_event_fails_runtime(self) -> None:
        main_id = "failed-spawn-main"
        self.write_main(main_id, [*spawn("spawn-failed", None), usage(20)])
        completed = self.collect("failed-spawn", main_id)
        self.assertEqual(2, completed.returncode)
        run = self.read_run("failed-spawn")
        self.assertEqual("incomplete_runtime", run["collector_status"])
        self.assertEqual("failed_spawn", run["runtime"]["requested_agents"][0]["validation_status"])

    def test_discovered_child_without_usage_fails_whole_run(self) -> None:
        main_id, child_id = "no-usage-main", "no-usage-child"
        self.write_main(main_id, [*spawn("spawn-no-usage", child_id), usage(20)])
        self.write_child(child_id, [])
        completed = self.collect("no-usage", main_id)
        self.assertEqual(2, completed.returncode)
        run = self.read_run("no-usage")
        self.assertEqual("unsupported_schema", run["collector_status"])
        self.assertIn(child_id, " ".join(run["collector_errors"]))

    def test_unsupported_and_legacy_unverified_are_excluded_from_report(self) -> None:
        session_id = "unsupported-session"
        self.write_main(session_id, [{"type": "event_msg", "payload": {"type": "agent_message"}}])
        completed = self.collect("unsupported", session_id)
        self.assertEqual(2, completed.returncode)
        self.runs.mkdir(parents=True, exist_ok=True)
        (self.runs / "20260801-legacy.json").write_text(json.dumps({
            "date": "2026-08-01", "slug": "legacy", "tokens": {"total": {"input_tokens": 999}},
        }), encoding="utf-8")
        report = subprocess.run(
            ["python3", str(REPORTER), "--runs-dir", str(self.runs)],
            text=True, capture_output=True, check=True,
        ).stdout
        self.assertIn("v1/legacy_unverified", report)
        self.assertIn("排除 2 筆", report)

    def test_config_commit_uses_configured_workflow_source(self) -> None:
        source = Path(self.context.name) / "source"
        source.mkdir()
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        (source / "AGENTS.md").write_text("source\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(source), "add", "AGENTS.md"], check=True)
        subprocess.run([
            "git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@example.com",
            "commit", "-qm", "source",
        ], check=True)
        expected = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "--short", "HEAD"],
            text=True, capture_output=True, check=True,
        ).stdout.strip()
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "workflow-source.toml").write_text(f'repository = "{source}"\n', encoding="utf-8")
        self.write_main("pointer-session", [usage(2)])
        completed = self.collect("pointer", "pointer-session")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(expected, self.read_run("pointer")["config_commit"])

    def test_codex_home_environment_selects_profile_sessions(self) -> None:
        session_id = "profile-session"
        self.write_main(session_id, [usage(2)])
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(self.home)
        completed = subprocess.run(
            ["python3", str(COLLECTOR), "--slug", "profile", "--sessions", session_id],
            text=True, capture_output=True, check=False, env=environment,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertTrue((self.runs / "20260802-profile.json").exists())

    def test_requested_effort_accepts_max_and_ultra_without_routing_them(self) -> None:
        session_id = "extended-effort-session"
        self.write_main(session_id, [usage(2)])
        for effort in ("max", "ultra"):
            with self.subTest(effort=effort):
                completed = self.collect(f"effort-{effort}", session_id, "--requested-effort", effort)
                self.assertEqual(0, completed.returncode, completed.stderr)
                run = self.read_run(f"effort-{effort}")
                self.assertEqual(effort, run["runtime"]["requested"]["effort"])
                self.assertIn("requested effort 與至少一個 effective effort 不一致", run["runtime"]["warnings"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
