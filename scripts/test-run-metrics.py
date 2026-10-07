#!/usr/bin/env python3
"""metrics v3 provenance、逐 domain availability 與 v1/v2 相容回歸測試。"""
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
        matches = list(self.runs.glob(f"*-{slug}.json"))
        self.assertEqual(1, len(matches), f"run 檔數量錯誤：{matches}")
        return json.loads(matches[0].read_text(encoding="utf-8"))

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
        self.assertEqual(3, run["schema_version"])
        self.assertIsNone(run["tokens"]["total"])
        self.assertIsNone(run["tokens"]["main_loop"])
        self.assertIsNone(run["tokens_by_agent"])
        self.assertIsNone(run["tokens_by_model"])
        self.assertEqual("unsupported_multi_transcript", run["token_attribution"]["status"])
        self.assertEqual("unavailable", run["availability"]["tokens"]["status"])
        self.assertEqual("exact", run["availability"]["agents"]["status"])
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
        self.assertIn("—(n=0)/—(n=0)/—(n=0)/—(n=0)", report)
        self.assertIn("2.0(n=1)/1.0(n=1)/0.0(n=1)", report)

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
        self.assertEqual(0, run["agent_spawn_total"])
        self.assertEqual({}, run["tool_counts"])
        self.assertEqual(0, run["reuse"]["follow_up_events"])
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
        self.assertEqual("explicit_transcript", run["session_provenance"]["source"])
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
        self.assertEqual("incomplete_runtime", run["collector_status"])
        self.assertEqual("mismatch", run["runtime"]["requested_agents"][0]["validation_status"])

    def test_started_child_missing_transcript_fails_runtime(self) -> None:
        main_id = "missing-main"
        self.write_main(main_id, [*spawn("spawn-missing", "missing-child"), usage(20)])
        completed = self.collect("missing-child", main_id)
        self.assertEqual(2, completed.returncode)
        run = self.read_run("missing-child")
        self.assertEqual("incomplete_runtime", run["collector_status"])
        self.assertIsNone(run["runtime"]["requested_agents"])
        self.assertEqual("missing_child", run["runtime"]["agent_observations"][0]["validation_status"])
        self.assertIsNone(run["agent_calls"])
        self.assertIsNone(run["agent_spawn_total"])
        self.assertIsNone(run["tool_counts"])
        self.assertIsNone(run["reuse"])
        self.assertIsNone(run["fork"])

    def test_spawn_without_started_event_fails_runtime(self) -> None:
        main_id = "failed-spawn-main"
        self.write_main(main_id, [*spawn("spawn-failed", None), usage(20)])
        completed = self.collect("failed-spawn", main_id)
        self.assertEqual(2, completed.returncode)
        run = self.read_run("failed-spawn")
        self.assertEqual("incomplete_runtime", run["collector_status"])
        self.assertEqual("failed_spawn", run["runtime"]["requested_agents"][0]["validation_status"])
        self.assertEqual("exact", run["availability"]["agents"]["status"])
        self.assertEqual({}, run["agent_calls"])
        self.assertEqual(0, run["agent_spawn_total"])

    def test_discovered_child_without_usage_fails_whole_run(self) -> None:
        main_id, child_id = "no-usage-main", "no-usage-child"
        self.write_main(main_id, [*spawn("spawn-no-usage", child_id), usage(20)])
        self.write_child(child_id, [])
        completed = self.collect("no-usage", main_id)
        self.assertEqual(2, completed.returncode)
        run = self.read_run("no-usage")
        self.assertEqual("unsupported_schema", run["collector_status"])
        self.assertIn(child_id, " ".join(run["collector_errors"]))
        self.assertIsNone(run["tokens"]["total"])
        self.assertEqual("unavailable", run["availability"]["tokens"]["status"])
        self.assertEqual("exact", run["availability"]["agents"]["status"])
        self.assertEqual({"qa": 1}, run["agent_calls"])
        self.assertEqual({"spawn_agent": 1}, run["tool_counts"])
        self.assertEqual(0, run["reuse"]["follow_up_events"])

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

    def test_workflow_identity_uses_current_codex_home_only(self) -> None:
        self.home.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.home)], check=True)
        (self.home / ".gitignore").write_text("/sessions/\n/run-metrics/\n/runtime/\n", encoding="utf-8")
        (self.home / "AGENTS.md").write_text("source\n", encoding="utf-8")
        (self.home / "workflow-source.toml").write_text(
            'repository = "/srv/example/.codex"\n', encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(self.home), "add", ".gitignore", "AGENTS.md", "workflow-source.toml"], check=True)
        subprocess.run([
            "git", "-C", str(self.home), "-c", "user.name=Test", "-c", "user.email=test@example.com",
            "commit", "-qm", "source",
        ], check=True)
        expected = subprocess.run(
            ["git", "-C", str(self.home), "rev-parse", "HEAD"],
            text=True, capture_output=True, check=True,
        ).stdout.strip()
        remote = self.root if hasattr(self, "root") else Path(self.context.name) / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        subprocess.run(["git", "-C", str(self.home), "remote", "add", "origin", str(remote)], check=True)
        self.write_main("pointer-session", [usage(2)])
        completed = self.collect("pointer", "pointer-session")
        self.assertEqual(0, completed.returncode, completed.stderr)
        run = self.read_run("pointer")
        self.assertEqual(expected[:7], run["config_commit"])
        self.assertEqual(expected, run["workflow_identity"]["head"])
        self.assertEqual(str(self.home.resolve()), run["workflow_identity"]["repo"])

    def test_ignored_runtime_does_not_change_managed_fingerprint(self) -> None:
        self.home.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.home)], check=True)
        (self.home / ".gitignore").write_text("/sessions/\n/run-metrics/\n/runtime/\n", encoding="utf-8")
        (self.home / "AGENTS.md").write_text("source\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.home), "add", ".gitignore", "AGENTS.md"], check=True)
        subprocess.run([
            "git", "-C", str(self.home), "-c", "user.name=Test", "-c", "user.email=test@example.com",
            "commit", "-qm", "source",
        ], check=True)
        self.write_main("identity-a", [usage(2)])
        first = self.collect("identity-a", "identity-a")
        self.assertEqual(0, first.returncode, first.stderr)
        before = self.read_run("identity-a")["workflow_identity"]
        runtime = self.home / "runtime"
        runtime.mkdir()
        (runtime / "ignored.txt").write_text("ignored\n", encoding="utf-8")
        self.write_main("identity-b", [usage(2)])
        second = self.collect("identity-b", "identity-b")
        self.assertEqual(0, second.returncode, second.stderr)
        after = self.read_run("identity-b")["workflow_identity"]
        self.assertEqual(before["managed_fingerprint"], after["managed_fingerprint"])
        self.assertFalse(after["managed_dirty"])

    def test_workflow_identity_drops_credential_bearing_origin_without_echo(self) -> None:
        unsafe_origins = (
            "https://user:password@host.example.com/repo.git",
            "ssh://user:password@host.example.com/repo.git",
            "https://host.example.com/repo.git?token=value",
            "ssh://host.example.com/repo.git#credential",
        )
        for index, unsafe in enumerate(unsafe_origins):
            with self.subTest(origin=index):
                home = Path(self.context.name) / f"unsafe-home-{index}"
                subprocess.run(["git", "init", "-q", "-b", "main", str(home)], check=True)
                (home / "AGENTS.md").write_text("source\n", encoding="utf-8")
                subprocess.run(["git", "-C", str(home), "add", "AGENTS.md"], check=True)
                subprocess.run([
                    "git", "-C", str(home), "-c", "user.name=Test",
                    "-c", "user.email=test@example.com", "commit", "-qm", "source",
                ], check=True)
                subprocess.run(["git", "-C", str(home), "remote", "add", "origin", unsafe], check=True)
                completed = subprocess.run(
                    ["python3", "-B", "-c", (
                        "import importlib.util,json,pathlib,sys;"
                        f"p=pathlib.Path({str(COLLECTOR)!r});"
                        "sys.path.insert(0,str(p.parent));"
                        "s=importlib.util.spec_from_file_location('metrics_fixture',p);"
                        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
                        f"print(json.dumps(m.workflow_identity(pathlib.Path({str(home)!r})),sort_keys=True))"
                    )],
                    text=True, capture_output=True, check=True,
                )
                self.assertNotIn(unsafe, completed.stdout)
                self.assertNotIn("password", completed.stdout)
                self.assertNotIn("token=value", completed.stdout)
                identity = json.loads(completed.stdout)
                self.assertIsNone(identity["origin"])
                self.assertEqual("remote_url_unsafe", identity["unavailable_reason"])

    def test_codex_home_environment_selects_profile_sessions(self) -> None:
        session_id = "profile-session"
        self.write_main(session_id, [usage(2)])
        self.write_main("state-shadow", [usage(50)])
        self.write_main("runtime-shadow", [usage(90)])
        (self.home / "state").mkdir(parents=True)
        (self.home / "state/profile.json").write_text(
            json.dumps({"session_id": "state-shadow"}), encoding="utf-8",
        )
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(self.home)
        environment["CODEX_THREAD_ID"] = "runtime-shadow"
        completed = subprocess.run(
            ["python3", str(COLLECTOR), "--slug", "profile", "--sessions", session_id],
            text=True, capture_output=True, check=False, env=environment,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(1, len(list(self.runs.glob("*-profile.json"))))
        run = self.read_run("profile")
        self.assertEqual("cli_session", run["session_provenance"]["source"])
        self.assertEqual(2, run["tokens"]["total"]["input_tokens"])

    def test_runtime_thread_id_is_stable_fallback_without_cli_or_state(self) -> None:
        session_id = "runtime-thread-session"
        self.write_main(session_id, [usage(8)])
        environment = os.environ.copy()
        environment["CODEX_THREAD_ID"] = session_id
        completed = subprocess.run([
            "python3", str(COLLECTOR), "--slug", "runtime-fallback",
            "--codex-home", str(self.home), "--runs-dir", str(self.runs),
        ], text=True, capture_output=True, check=False, env=environment)
        self.assertEqual(0, completed.returncode, completed.stderr)
        run = self.read_run("runtime-fallback")
        self.assertEqual("runtime_env", run["session_provenance"]["source"])
        self.assertEqual("CODEX_THREAD_ID", run["session_provenance"]["runtime_key"])
        self.assertEqual([session_id], run["sessions"])
        self.assertEqual(8, run["tokens"]["total"]["input_tokens"])

    def test_state_session_precedes_runtime_thread_id(self) -> None:
        state_id, runtime_id = "state-session", "runtime-session"
        self.write_main(state_id, [usage(11)])
        self.write_main(runtime_id, [usage(99)])
        state_dir = self.home / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "state-priority.json").write_text(
            json.dumps({"session_id": state_id}), encoding="utf-8",
        )
        environment = os.environ.copy()
        environment["CODEX_THREAD_ID"] = runtime_id
        completed = subprocess.run([
            "python3", str(COLLECTOR), "--slug", "state-priority",
            "--codex-home", str(self.home), "--runs-dir", str(self.runs),
        ], text=True, capture_output=True, check=False, env=environment)
        self.assertEqual(0, completed.returncode, completed.stderr)
        run = self.read_run("state-priority")
        self.assertEqual("state", run["session_provenance"]["source"])
        self.assertEqual([state_id], run["sessions"])
        self.assertEqual(11, run["tokens"]["total"]["input_tokens"])

    def test_missing_runtime_transcript_still_persists_auditable_run(self) -> None:
        environment = os.environ.copy()
        environment["CODEX_THREAD_ID"] = "missing-runtime-session"
        completed = subprocess.run([
            "python3", str(COLLECTOR), "--slug", "missing-runtime",
            "--codex-home", str(self.home), "--runs-dir", str(self.runs),
        ], text=True, capture_output=True, check=False, env=environment)
        self.assertEqual(2, completed.returncode)
        run = self.read_run("missing-runtime")
        self.assertEqual("unsupported_schema", run["collector_status"])
        self.assertEqual("runtime_env", run["session_provenance"]["source"])
        self.assertIsNone(run["token_observations"])
        self.assertIsNone(run["agent_spawn_total"])
        self.assertIsNone(run["tool_counts"])
        self.assertIsNone(run["reuse"])

    def test_no_session_source_still_persists_unavailable_run(self) -> None:
        environment = os.environ.copy()
        environment.pop("CODEX_THREAD_ID", None)
        completed = subprocess.run([
            "python3", str(COLLECTOR), "--slug", "no-source",
            "--codex-home", str(self.home), "--runs-dir", str(self.runs),
        ], text=True, capture_output=True, check=False, env=environment)
        self.assertEqual(2, completed.returncode)
        run = self.read_run("no-source")
        self.assertEqual("unavailable", run["session_provenance"]["source"])
        self.assertEqual("unsupported_schema", run["collector_status"])
        self.assertIsNone(run["tokens"]["total"])
        self.assertIsNone(run["agent_calls"])

    def test_unknown_jsonl_schema_uses_null_summaries(self) -> None:
        transcript = self.sessions / "rollout-unknown.jsonl"
        write_jsonl(transcript, [{"type": "future_schema", "payload": {"value": 1}}])
        completed = subprocess.run([
            "python3", str(COLLECTOR), "--slug", "unknown-schema",
            "--transcript", str(transcript), "--codex-home", str(self.home),
            "--runs-dir", str(self.runs),
        ], text=True, capture_output=True, check=False)
        self.assertEqual(2, completed.returncode)
        run = self.read_run("unknown-schema")
        self.assertEqual("unsupported_schema", run["collector_status"])
        self.assertIsNone(run["agent_calls"])
        self.assertIsNone(run["fork"])
        self.assertIsNone(run["tool_counts"])
        self.assertEqual("unavailable", run["availability"]["agents"]["status"])

    def test_unknown_agent_shaped_event_invalidates_structural_domains_only(self) -> None:
        session_id = "future-agent-event"
        self.write_main(session_id, [
            {"type": "event_msg", "payload": {
                "type": "sub_agent_activity_v2", "agent_thread_id": "future-child",
                "agent_path": "/root/future",
            }},
            usage(31),
        ])
        completed = self.collect("future-agent", session_id)
        self.assertEqual(2, completed.returncode, completed.stderr)
        run = self.read_run("future-agent")
        self.assertEqual("unsupported_schema", run["collector_status"])
        self.assertEqual("exact", run["availability"]["tokens"]["status"])
        for domain in ("agents", "tools", "reuse", "fork"):
            self.assertEqual("unavailable", run["availability"][domain]["status"])
        self.assertIsNone(run["agent_calls"])
        self.assertIsNone(run["tool_counts"])
        self.assertIsNone(run["reuse"])
        self.assertIsNone(run["fork"])

    def test_unknown_tool_shaped_response_invalidates_only_tool_domain(self) -> None:
        session_id = "future-tool-response"
        self.write_main(session_id, [
            {"type": "response_item", "payload": {
                "type": "future_tool_call", "name": "future_tool", "call_id": "future-call",
            }},
            usage(32),
        ])
        completed = self.collect("future-tool", session_id)
        self.assertEqual(2, completed.returncode, completed.stderr)
        run = self.read_run("future-tool")
        self.assertEqual("unsupported_schema", run["collector_status"])
        self.assertEqual("unavailable", run["availability"]["tools"]["status"])
        self.assertIsNone(run["tool_counts"])
        self.assertEqual("exact", run["availability"]["agents"]["status"])
        self.assertEqual({}, run["agent_calls"])
        self.assertEqual(0, run["agent_spawn_total"])
        self.assertEqual(0, run["reuse"]["follow_up_events"])

    def test_session_lookup_validates_exact_meta_and_never_uses_mtime(self) -> None:
        session_id = "exact-session"
        self.write_main(session_id, [usage(17)])
        write_jsonl(self.sessions / "rollout-exact-session-newer-shadow.jsonl", [
            {"type": "session_meta", "payload": {"id": "shadow-session"}}, usage(999),
        ])
        completed = self.collect("exact-meta", session_id)
        self.assertEqual(0, completed.returncode, completed.stderr)
        run = self.read_run("exact-meta")
        self.assertEqual(1, run["transcript_count"])
        self.assertEqual(17, run["tokens"]["total"]["input_tokens"])

    def test_ambiguous_exact_session_fails_instead_of_picking_newest(self) -> None:
        session_id = "ambiguous-session"
        for suffix in ("a", "b"):
            write_jsonl(self.sessions / f"rollout-{session_id}-{suffix}.jsonl", [
                {"type": "session_meta", "payload": {"id": session_id}},
                {"type": "turn_context", "payload": {"model": "gpt-5.6-sol", "effort": "medium"}},
                usage(20),
            ])
        completed = self.collect("ambiguous", session_id)
        self.assertEqual(2, completed.returncode)
        run = self.read_run("ambiguous")
        self.assertIn("session transcript 不唯一", " ".join(run["collector_errors"]))
        self.assertIsNone(run["tokens"]["total"])

    def test_reporter_excludes_each_unavailable_process_domain(self) -> None:
        session_id = "report-domain-session"
        self.write_main(session_id, [usage(25, output_tokens=5)])
        completed = self.collect("report-domain", session_id)
        self.assertEqual(0, completed.returncode, completed.stderr)
        path = next(self.runs.glob("*-report-domain.json"))
        run = json.loads(path.read_text(encoding="utf-8"))
        run["availability"]["agents"] = {"status": "unavailable", "reason": "mutation_fixture"}
        run["availability"]["reuse"] = {"status": "unavailable", "reason": "mutation_fixture"}
        run["agent_calls"] = None
        run["agent_spawn_total"] = None
        run["reuse"] = None
        del run["tokens"]["total"]["cached_input_tokens"]
        path.write_text(json.dumps(run), encoding="utf-8")
        report = subprocess.run(
            ["python3", str(REPORTER), "--runs-dir", str(self.runs)],
            text=True, capture_output=True, check=True,
        ).stdout
        self.assertIn("—/—", report)
        self.assertIn("—(n=0)/—(n=0)/0.0(n=1)", report)
        self.assertIn("25.0(n=1)/—(n=0)/5.0(n=1)/0.0(n=1)", report)
        self.assertIn("| 5 | 0 | — | —/— |", report)
        self.assertIn("25", report)

    def test_reporter_keeps_v2_exact_run_compatible(self) -> None:
        self.runs.mkdir(parents=True, exist_ok=True)
        (self.runs / "20260801-v2-compatible.json").write_text(json.dumps({
            "schema_version": 2,
            "collector_status": "ok",
            "date": "2026-08-01",
            "slug": "v2-compatible",
            "token_attribution": {"status": "exact_single_transcript"},
            "tokens": {"total": {
                "input_tokens": 40, "cached_input_tokens": 4,
                "output_tokens": 3, "reasoning_output_tokens": 2,
            }},
            "agent_calls": {}, "agent_spawn_total": 0,
            "reuse": {"follow_up_events": 0}, "repair_count": 0,
            "config_commit": "v2", "risk_lane": "L1", "outcome": "done",
        }), encoding="utf-8")
        report = subprocess.run(
            ["python3", str(REPORTER), "--runs-dir", str(self.runs)],
            text=True, capture_output=True, check=True,
        ).stdout
        self.assertIn("v2/ok/exact_single_transcript", report)
        self.assertIn("0/0", report)
        self.assertIn("40.0(n=1)/4.0(n=1)/3.0(n=1)/2.0(n=1)", report)

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
