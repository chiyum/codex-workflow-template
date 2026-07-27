#!/usr/bin/env python3
"""收集 Codex/legacy transcript 的任務級遙測，解析失敗時 fail closed。"""
from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from typing import Any, Iterable

USAGE_FIELDS = (
    "input_tokens",
    "uncached_input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
UNKNOWN = "unknown"


def new_usage() -> dict[str, int]:
    return {key: 0 for key in USAGE_FIELDS}


def add_usage(accumulator: dict[str, int], usage: dict[str, Any]) -> None:
    for key in USAGE_FIELDS:
        value = usage.get(key)
        if isinstance(value, (int, float)):
            accumulator[key] += int(value)


def normalize_usage(raw: dict[str, Any]) -> dict[str, int] | None:
    """把 Codex v2 與 legacy Claude usage 正規化成單一欄位集合。"""
    input_tokens = raw.get("input_tokens")
    output_tokens = raw.get("output_tokens")
    if not isinstance(input_tokens, (int, float)) or not isinstance(output_tokens, (int, float)):
        return None
    cached = raw.get("cached_input_tokens", raw.get("cache_read_input_tokens", 0))
    cache_write = raw.get("cache_write_input_tokens", raw.get("cache_creation_input_tokens", 0))
    reasoning = raw.get("reasoning_output_tokens", raw.get("reasoning_tokens", 0))
    cached = int(cached) if isinstance(cached, (int, float)) else 0
    return {
        "input_tokens": int(input_tokens),
        "uncached_input_tokens": max(0, int(input_tokens) - cached),
        "cached_input_tokens": cached,
        "cache_write_input_tokens": int(cache_write) if isinstance(cache_write, (int, float)) else 0,
        "output_tokens": int(output_tokens),
        "reasoning_output_tokens": int(reasoning) if isinstance(reasoning, (int, float)) else 0,
    }


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                try:
                    value = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def find_session_files(sessions_root: Path, session_ids: Iterable[str]) -> list[Path]:
    found: list[Path] = []
    for session_id in session_ids:
        matches = glob.glob(str(sessions_root / "**" / f"*{session_id}*.jsonl"), recursive=True)
        if not matches:
            print(f"[warn] 找不到 session transcript：{session_id}", file=sys.stderr)
        found.extend(Path(item) for item in matches)
    return sorted(set(found))


def role_from_agent_path(agent_path: Any) -> str:
    if not isinstance(agent_path, str) or agent_path.rstrip("/") in {"", "/root"}:
        return "main"
    leaf = agent_path.rstrip("/").split("/")[-1]
    return leaf.rsplit("_", 1)[0] if re.search(r"_\d+$", leaf) else leaf


def parse_tool_input(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def parse_transcript(path: Path) -> dict[str, Any]:
    """解析單份 transcript；跨 transcript 的 tree 級去重在 build_run 完成。"""
    result: dict[str, Any] = {
        "path": str(path),
        "session_id": UNKNOWN,
        "role": "main",
        "agent_path": "/root",
        "forked_from_id": None,
        "history_mode": UNKNOWN,
        "usage": new_usage(),
        "usage_schema": None,
        "usage_snapshots": [],
        "effective_contexts": [],
        "tool_counts": {},
        "spawn_requests": [],
        "agent_events": [],
        "first_ts": None,
        "last_ts": None,
    }
    latest_v2: dict[str, int] | None = None
    legacy_total = new_usage()
    legacy_count = 0
    for item in iter_jsonl(path):
        timestamp = item.get("timestamp")
        if isinstance(timestamp, str):
            if result["first_ts"] is None or timestamp < result["first_ts"]:
                result["first_ts"] = timestamp
            if result["last_ts"] is None or timestamp > result["last_ts"]:
                result["last_ts"] = timestamp
        item_type = item.get("type")
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if item_type == "session_meta":
            result["session_id"] = payload.get("id") or payload.get("session_id") or result["session_id"]
            result["agent_path"] = payload.get("agent_path") or result["agent_path"]
            result["role"] = payload.get("agent_role") or role_from_agent_path(result["agent_path"])
            result["forked_from_id"] = payload.get("forked_from_id") or payload.get("parent_thread_id")
            result["history_mode"] = payload.get("history_mode") or UNKNOWN
        elif item_type == "turn_context":
            context = {
                "model": payload.get("model") or UNKNOWN,
                "effort": payload.get("effort") or payload.get("reasoning_effort") or UNKNOWN,
            }
            if not result["effective_contexts"] or result["effective_contexts"][-1] != context:
                result["effective_contexts"].append(context)
        elif item_type == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
            raw_usage = info.get("total_token_usage") if isinstance(info.get("total_token_usage"), dict) else {}
            normalized = normalize_usage(raw_usage)
            if normalized is not None:
                latest_v2 = normalized
                result["usage_snapshots"].append(normalized)
        elif item_type == "event_msg" and payload.get("type") == "sub_agent_activity":
            result["agent_events"].append({
                "event_id": payload.get("event_id") or UNKNOWN,
                "agent_thread_id": payload.get("agent_thread_id") or UNKNOWN,
                "agent_path": payload.get("agent_path") or UNKNOWN,
                "kind": payload.get("kind") or UNKNOWN,
            })
        elif item_type == "response_item" and payload.get("type") in {"custom_tool_call", "function_call"}:
            name = payload.get("name") or UNKNOWN
            result["tool_counts"][name] = result["tool_counts"].get(name, 0) + 1
            if "spawn_agent" in name:
                request = parse_tool_input(payload.get("input", payload.get("arguments")))
                result["spawn_requests"].append({
                    "call_id": payload.get("call_id") or payload.get("id") or UNKNOWN,
                    "role": request.get("agent_type") or UNKNOWN,
                    "requested_model": request.get("model") or UNKNOWN,
                    "requested_effort": request.get("reasoning_effort") or UNKNOWN,
                    "fork_turns": request.get("fork_turns") or "all",
                })
        message = item.get("message") if isinstance(item.get("message"), dict) else {}
        legacy = message.get("usage") if isinstance(message.get("usage"), dict) else None
        if legacy is not None:
            normalized = normalize_usage(legacy)
            if normalized is not None:
                add_usage(legacy_total, normalized)
                legacy_count += 1
    if latest_v2 is not None:
        result["usage"] = latest_v2
        result["usage_schema"] = "codex_v2"
    elif legacy_count:
        result["usage"] = legacy_total
        result["usage_schema"] = "legacy_message_usage"
    return result


def discover_transcripts(initial: list[Path], sessions_root: Path) -> list[dict[str, Any]]:
    parsed: dict[Path, dict[str, Any]] = {}
    queue = list(initial)
    while queue:
        path = queue.pop(0)
        if path in parsed:
            continue
        record = parse_transcript(path)
        parsed[path] = record
        child_ids = {
            event["agent_thread_id"]
            for event in record["agent_events"]
            if event["kind"] == "started" and event["agent_thread_id"] != UNKNOWN
        }
        for child in find_session_files(sessions_root, child_ids):
            if child not in parsed:
                queue.append(child)
    return list(parsed.values())


INSTITUTION_PATHS = ["AGENTS.md", "workflow-source.toml", "agents", "agent-guides", "skills", "scripts", "workflows"]


def config_commit(codex_home: Path) -> str:
    source_repo = os.environ.get("CODEX_WORKFLOW_SOURCE")
    candidates = [Path(source_repo).expanduser()] if source_repo else []
    pointer = codex_home / "workflow-source.toml"
    if pointer.is_file():
        try:
            source = tomllib.loads(pointer.read_text(encoding="utf-8")).get("repository")
            if isinstance(source, str) and source.strip():
                candidates.append(Path(source.strip()).expanduser())
        except (OSError, tomllib.TOMLDecodeError):
            pass
    candidates.append(codex_home)
    for candidate in dict.fromkeys(candidates):
        try:
            completed = subprocess.run(
                ["git", "-C", str(candidate), "log", "-1", "--format=%h", "--", *INSTITUTION_PATHS],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if completed.returncode == 0 and completed.stdout.strip():
                return completed.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
    return UNKNOWN


def comma_values(raw: str) -> list[str]:
    return [value.strip() for value in raw.split(",") if value.strip()]


def resolve_session_ids(args: argparse.Namespace, codex_home: Path) -> list[str]:
    result = comma_values(args.sessions)
    state_path = codex_home / "state" / f"{args.slug}.json"
    if result or not state_path.exists():
        return result
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[warn] 讀 state 檔失敗：{exc}", file=sys.stderr)
        return result
    for key in ("session_id", "session_ids", "sessions"):
        value = state.get(key)
        if isinstance(value, str):
            result.append(value)
        elif isinstance(value, list):
            result.extend(item for item in value if isinstance(item, str))
    return result


def build_run(args: argparse.Namespace) -> dict[str, Any]:
    codex_home = Path(args.codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()
    sessions_root = Path(args.sessions_root).expanduser().resolve() if args.sessions_root else codex_home / "sessions"
    session_ids = resolve_session_ids(args, codex_home)
    initial = [Path(item).expanduser().resolve() for item in args.transcript]
    initial.extend(find_session_files(sessions_root, session_ids))
    initial = sorted(set(initial))
    scope_errors: list[str] = []
    if args.single_session_only and len(initial) == 1:
        transcripts = [parse_transcript(initial[0])]
        transcript = transcripts[0]
        if transcript["role"] == "main" or transcript["history_mode"] != "none":
            scope_errors.append("--single-session-only 只允許 history_mode=none 的 child transcript")
    elif args.single_session_only:
        transcripts = [parse_transcript(path) for path in initial]
        scope_errors.append("--single-session-only 必須精確對應一份 transcript")
    else:
        transcripts = discover_transcripts(initial, sessions_root) if initial else []

    total: dict[str, int] | None = None
    main_usage: dict[str, int] | None = None
    tokens_by_agent: dict[str, dict[str, int]] = {}
    tokens_by_model: dict[str, dict[str, int]] = {}
    tool_counts: dict[str, int] = {}
    effective_by_session: list[dict[str, Any]] = []
    spawn_request_map: dict[str, dict[str, Any]] = {}
    spawn_conflicts: set[str] = set()
    agent_events: dict[tuple[Any, ...], dict[str, Any]] = {}
    usage_records = 0
    first_timestamps: list[str] = []
    last_timestamps: list[str] = []

    for transcript in transcripts:
        if transcript["usage_schema"]:
            usage_records += 1
        for name, count in transcript["tool_counts"].items():
            tool_counts[name] = tool_counts.get(name, 0) + count
        for index, request in enumerate(transcript["spawn_requests"]):
            call_id = request["call_id"]
            key = call_id if call_id != UNKNOWN else f"{UNKNOWN}:{transcript['session_id']}:{index}"
            previous = spawn_request_map.get(key)
            if previous is None:
                spawn_request_map[key] = request
            elif previous != request:
                spawn_conflicts.add(key)
        for event in transcript["agent_events"]:
            key = (event["event_id"], event["agent_thread_id"], event["kind"])
            agent_events[key] = event
        effective_by_session.append({
            "session_id": transcript["session_id"],
            "role": transcript["role"],
            "agent_path": transcript["agent_path"],
            "history_mode": transcript["history_mode"],
            "contexts": transcript["effective_contexts"] or [{"model": UNKNOWN, "effort": UNKNOWN}],
        })
        if transcript["first_ts"]:
            first_timestamps.append(transcript["first_ts"])
        if transcript["last_ts"]:
            last_timestamps.append(transcript["last_ts"])

    spawn_requests = list(spawn_request_map.values())
    started = [event for event in agent_events.values() if event["kind"] == "started"]
    interacted = [event for event in agent_events.values() if event["kind"] == "interacted"]
    agent_calls: dict[str, int] = {}
    if args.single_session_only or not started:
        for request in spawn_requests:
            role = request["role"]
            agent_calls[role] = agent_calls.get(role, 0) + 1
    else:
        for event in started:
            role = role_from_agent_path(event["agent_path"])
            agent_calls[role] = agent_calls.get(role, 0) + 1

    depth_values = [max(0, str(item["agent_path"]).strip("/").count("/")) for item in effective_by_session]
    histories = [item["history_mode"] for item in effective_by_session if item["role"] != "main"]
    reused_threads = {event["agent_thread_id"] for event in interacted if event["agent_thread_id"] != UNKNOWN}
    schemas = {transcript["usage_schema"] for transcript in transcripts if transcript["usage_schema"]}
    status = "ok"
    errors = list(scope_errors)
    if not transcripts:
        status = "unsupported_schema"
        errors.append("找不到任何 transcript")
    elif scope_errors:
        status = "unsupported_schema"
    elif usage_records != len(transcripts):
        status = "unsupported_schema"
        missing = [transcript["session_id"] for transcript in transcripts if not transcript["usage_schema"]]
        errors.append(f"discovered transcript 缺少可解析 usage：{','.join(missing)}")
    elif len(schemas) != 1:
        status = "unsupported_schema"
        errors.append("同一 run 混用 Codex v2 cumulative 與 legacy message usage")

    token_observations = []
    for transcript in transcripts:
        snapshots = transcript["usage_snapshots"]
        token_observations.append({
            "transcript_path": transcript["path"],
            "session_id": transcript["session_id"],
            "role": transcript["role"],
            "agent_path": transcript["agent_path"],
            "forked_from_id": transcript["forked_from_id"],
            "history_mode": transcript["history_mode"],
            "effective_contexts": transcript["effective_contexts"],
            "first_ts": transcript["first_ts"],
            "last_ts": transcript["last_ts"],
            "usage_schema": transcript["usage_schema"] or UNKNOWN,
            "first_usage": snapshots[0] if snapshots else None,
            "final_usage": snapshots[-1] if snapshots else (transcript["usage"] if transcript["usage_schema"] else None),
        })

    attribution_status = "unavailable"
    if status == "ok" and len(transcripts) == 1:
        transcript = transcripts[0]
        total = dict(transcript["usage"])
        if transcript["role"] == "main":
            main_usage = dict(total)
        else:
            tokens_by_agent[transcript["role"]] = dict(total)
        models = {context["model"] for context in transcript["effective_contexts"] if context["model"] != UNKNOWN}
        tokens_by_model[next(iter(models)) if len(models) == 1 else UNKNOWN] = dict(total)
        attribution_status = "exact_single_transcript"
    elif status == "ok" and len(transcripts) > 1:
        status = "unsupported_token_attribution"
        errors.append("多 transcript counter 無安全的 sum/max 聚合規則；只保留逐 session raw observation")
        attribution_status = "unsupported_multi_transcript"

    requested = {
        "model": args.requested_model or UNKNOWN,
        "effort": args.requested_effort or UNKNOWN,
        "source": "collector_args" if args.requested_model or args.requested_effort else UNKNOWN,
    }
    warnings = []
    if spawn_conflicts:
        warnings.append(f"同一 spawn call 出現衝突 requested 參數：{','.join(sorted(spawn_conflicts))}")
    main_sessions = [session for session in effective_by_session if session["role"] == "main"]
    comparison_sessions = main_sessions or effective_by_session
    effective_pairs = {
        (context["model"], context["effort"])
        for session in comparison_sessions
        for context in session["contexts"]
    }
    if requested["model"] != UNKNOWN and any(model != requested["model"] for model, _ in effective_pairs):
        warnings.append("requested model 與至少一個 effective model 不一致")
    if requested["effort"] != UNKNOWN and any(effort != requested["effort"] for _, effort in effective_pairs):
        warnings.append("requested effort 與至少一個 effective effort 不一致")

    transcripts_by_session = {
        transcript["session_id"]: transcript
        for transcript in transcripts
        if transcript["session_id"] != UNKNOWN
    }
    started_by_event = {
        event["event_id"]: event
        for event in started
        if event["event_id"] != UNKNOWN
    }
    child_runtime = []
    matched_event_ids: set[str] = set()
    runtime_failed = bool(spawn_conflicts)
    for request in spawn_requests:
        record = dict(request)
        event = started_by_event.get(request["call_id"])
        if event is None:
            record.update({
                "child_thread_id": None,
                "child_status": "failed_spawn",
                "effective_model": UNKNOWN,
                "effective_effort": UNKNOWN,
                "validation_status": "failed_spawn",
            })
            runtime_failed = True
            warnings.append(f"spawn request 未對應 started event：{request['call_id']}")
            child_runtime.append(record)
            continue
        matched_event_ids.add(event["event_id"])
        child_id = event["agent_thread_id"]
        child = transcripts_by_session.get(child_id)
        if child is None:
            record.update({
                "child_thread_id": child_id,
                "child_status": "missing_transcript",
                "effective_model": UNKNOWN,
                "effective_effort": UNKNOWN,
                "validation_status": "missing_child",
            })
            runtime_failed = True
            warnings.append(f"started child 缺少 transcript：{child_id}")
            child_runtime.append(record)
            continue
        models = {context["model"] for context in child["effective_contexts"] if context["model"] != UNKNOWN}
        efforts = {context["effort"] for context in child["effective_contexts"] if context["effort"] != UNKNOWN}
        effective_model = next(iter(models)) if len(models) == 1 else UNKNOWN
        effective_effort = next(iter(efforts)) if len(efforts) == 1 else UNKNOWN
        matches = (
            request["requested_model"] != UNKNOWN
            and request["requested_effort"] != UNKNOWN
            and models == {request["requested_model"]}
            and efforts == {request["requested_effort"]}
        )
        validation = "matched" if matches else "mismatch"
        if not matches:
            runtime_failed = True
            warnings.append(f"child requested/effective 不一致：{child_id}")
        record.update({
            "child_thread_id": child_id,
            "child_status": "started",
            "effective_model": effective_model,
            "effective_effort": effective_effort,
            "validation_status": validation,
        })
        child_runtime.append(record)

    unmatched_started = [] if args.single_session_only else [
        event for event in started
        if event["event_id"] == UNKNOWN or event["event_id"] not in matched_event_ids
    ]
    if unmatched_started:
        runtime_failed = True
        warnings.append("至少一個 started child 無法對應 spawn request")
    if runtime_failed and status == "ok":
        status = "incomplete_runtime"
        errors.append("child spawn/runtime 驗證未通過")

    return {
        "schema_version": 2,
        "collector_status": status,
        "collector_errors": errors,
        "slug": args.slug,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "codex_home": str(codex_home),
        "config_commit": config_commit(codex_home),
        "product": args.product,
        "sessions": session_ids,
        "transcript_count": len(transcripts),
        "outcome": args.outcome,
        "notes": args.notes,
        "verdict": None,
        "verdict_comment": None,
        "risk_lane": args.risk_lane or UNKNOWN,
        "acceptance_result": args.acceptance_result,
        "repair_count": args.repair_count,
        "experiment": {"name": args.experiment or UNKNOWN, "variant": args.variant},
        "runtime": {
            "requested": requested,
            "requested_agents": child_runtime or UNKNOWN,
            "effective_by_session": effective_by_session,
            "validation_status": "failed" if runtime_failed else "ok",
            "warnings": warnings,
        },
        "tokens": {"main_loop": main_usage, "total": total},
        "token_attribution": {"status": attribution_status, "transcript_count": len(transcripts)},
        "token_observations": token_observations,
        "tokens_by_model": tokens_by_model,
        "tokens_by_agent": tokens_by_agent,
        "agent_calls": agent_calls,
        "agent_spawn_total": sum(agent_calls.values()),
        "fork": {
            "max_depth": max(depth_values, default=0),
            "requested": [request["fork_turns"] for request in spawn_requests] or UNKNOWN,
            "bounded_count": sum(1 for value in histories if value in {"none", "bounded"}),
            "full_history_count": sum(1 for value in histories if value in {"all", "full"}),
            "unknown_count": sum(1 for value in histories if value not in {"none", "bounded", "all", "full"}),
        },
        "reuse": {
            "follow_up_events": len(interacted),
            "reused_agent_threads": len(reused_threads),
        },
        "tool_counts": tool_counts,
        "duration": {
            "first_ts": min(first_timestamps) if first_timestamps else None,
            "last_ts": max(last_timestamps) if last_timestamps else None,
        },
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--slug", required=True)
    result.add_argument("--sessions", default="")
    result.add_argument("--transcript", action="append", default=[])
    result.add_argument("--codex-home", default="")
    result.add_argument("--sessions-root", default="")
    result.add_argument("--single-session-only", action="store_true")
    result.add_argument("--runs-dir", default="")
    result.add_argument("--product", default="")
    result.add_argument("--outcome", choices=("done", "blocked", "aborted"), default="done")
    result.add_argument("--notes", default="")
    result.add_argument("--risk-lane", choices=("L1", "L2", "L3"), default="")
    result.add_argument("--requested-model", default="")
    result.add_argument("--requested-effort", choices=("", "low", "medium", "high", "xhigh", "max", "ultra"), default="")
    result.add_argument("--experiment", default="")
    result.add_argument("--variant", choices=("unknown", "before", "after"), default="unknown")
    result.add_argument("--repair-count", type=int, default=0)
    result.add_argument("--acceptance-result", choices=("unknown", "pass", "fail"), default="unknown")
    result.add_argument("--print-json", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    if not args.sessions and not args.transcript:
        codex_home = Path(args.codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
        if not (codex_home / "state" / f"{args.slug}.json").exists():
            print("錯誤：無 session id、transcript 或可用 state", file=sys.stderr)
            return 1
    run = build_run(args)
    codex_home = Path(run["codex_home"])
    runs_dir = Path(args.runs_dir).expanduser().resolve() if args.runs_dir else codex_home / "run-metrics" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    filename = args.slug if re.match(r"^\d{8}-", args.slug) else f"{run['date'].replace('-', '')}-{args.slug}"
    output = runs_dir / f"{filename}.json"
    output.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.print_json:
        print(json.dumps(run, ensure_ascii=False, indent=2))
    else:
        usage = run["tokens"]["total"]
        print(f"run 記錄已寫入 {output}")
        if usage is None:
            print(f"collector={run['collector_status']} tokens=unsupported")
        else:
            print(f"collector={run['collector_status']} in={usage['input_tokens']:,} cached={usage['cached_input_tokens']:,} out={usage['output_tokens']:,} reasoning={usage['reasoning_output_tokens']:,}")
        print(f"agent 派遣={run['agent_spawn_total']} reuse={run['reuse']['follow_up_events']} risk={run['risk_lane']}")
        for warning in run["runtime"]["warnings"]:
            print(f"[warn] {warning}", file=sys.stderr)
    return 0 if run["collector_status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())

