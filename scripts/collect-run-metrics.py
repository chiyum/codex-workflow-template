#!/usr/bin/env python3
"""收集 Codex/legacy transcript 的任務級遙測，解析失敗時 fail closed。"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Iterable

# 匯入同目錄模組時不寫 __pycache__，避免在制度 repo 與安裝目標留下 binary 產物。
sys.dont_write_bytecode = True
from remote_url import sanitize_remote_url  # noqa: E402

USAGE_FIELDS = (
    "input_tokens",
    "uncached_input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
UNKNOWN = "unknown"
STRUCTURAL_DOMAINS = ("agents", "tools", "reuse", "fork")
KNOWN_SUB_AGENT_KINDS = {"started", "interacted", "interrupted"}
KNOWN_TOOL_RESPONSE_TYPES = {
    "custom_tool_call", "function_call", "tool_search_call",
    "custom_tool_call_output", "function_call_output", "tool_search_output",
}


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


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line_number, raw_line in enumerate(handle, 1):
                try:
                    value = json.loads(raw_line)
                except json.JSONDecodeError:
                    errors.append(f"{path}:第 {line_number} 列不是合法 JSON")
                    continue
                if isinstance(value, dict):
                    records.append(value)
                else:
                    errors.append(f"{path}:第 {line_number} 列不是 JSON object")
    except OSError as exc:
        errors.append(f"無法讀取 transcript {path}：{exc}")
    return records, errors


def transcript_session_id(path: Path) -> str | None:
    records, errors = read_jsonl(path)
    if errors:
        return None
    for item in records:
        if item.get("type") != "session_meta":
            continue
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        value = payload.get("id") or payload.get("session_id")
        return value if isinstance(value, str) and value else None
    return None


def find_session_files(
    sessions_root: Path, session_ids: Iterable[str], errors: list[str] | None = None,
) -> list[Path]:
    found: list[Path] = []
    for session_id in session_ids:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", session_id):
            message = f"session id 格式不安全：{session_id}"
            print(f"[warn] {message}", file=sys.stderr)
            if errors is not None:
                errors.append(message)
            continue
        matches = glob.glob(
            str(sessions_root / "**" / f"*{glob.escape(session_id)}*.jsonl"), recursive=True,
        )
        exact = [Path(item) for item in matches if transcript_session_id(Path(item)) == session_id]
        if not exact:
            print(f"[warn] 找不到 session transcript：{session_id}", file=sys.stderr)
            if errors is not None:
                errors.append(f"找不到 session transcript：{session_id}")
        elif len(exact) > 1:
            print(f"[warn] session transcript 不唯一：{session_id}", file=sys.stderr)
            if errors is not None:
                errors.append(f"session transcript 不唯一：{session_id}")
        else:
            found.extend(exact)
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
        "parse_errors": [],
        "schema_recognized": False,
        "domain_errors": {domain: [] for domain in ("tokens", *STRUCTURAL_DOMAINS)},
    }
    latest_v2: dict[str, int] | None = None
    legacy_total = new_usage()
    legacy_count = 0
    records, parse_errors = read_jsonl(path)
    result["parse_errors"] = parse_errors
    saw_session_meta = False

    def mark_domains(domains: Iterable[str], message: str) -> None:
        for domain in domains:
            result["domain_errors"][domain].append(message)

    for item in records:
        timestamp = item.get("timestamp")
        if isinstance(timestamp, str):
            if result["first_ts"] is None or timestamp < result["first_ts"]:
                result["first_ts"] = timestamp
            if result["last_ts"] is None or timestamp > result["last_ts"]:
                result["last_ts"] = timestamp
        item_type = item.get("type")
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if item_type == "session_meta":
            saw_session_meta = True
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
            else:
                mark_domains(("tokens",), "token_count 缺少可解析 usage")
        elif item_type == "event_msg" and payload.get("type") == "sub_agent_activity":
            kind = payload.get("kind") or UNKNOWN
            if kind not in KNOWN_SUB_AGENT_KINDS:
                mark_domains(STRUCTURAL_DOMAINS, f"未知 sub_agent_activity kind：{kind}")
            result["agent_events"].append({
                "event_id": payload.get("event_id") or UNKNOWN,
                "agent_thread_id": payload.get("agent_thread_id") or UNKNOWN,
                "agent_path": payload.get("agent_path") or UNKNOWN,
                "kind": kind,
            })
        elif item_type == "event_msg":
            payload_type = payload.get("type")
            if isinstance(payload_type, str) and payload_type not in {"agent_message", "agent_reasoning"}:
                if "token" in payload_type or "usage" in payload_type:
                    mark_domains(("tokens",), f"未知 token event：{payload_type}")
                if "sub_agent" in payload_type or any(
                    key in payload for key in ("agent_thread_id", "agent_path", "forked_from_id")
                ):
                    mark_domains(STRUCTURAL_DOMAINS, f"未知 agent event：{payload_type}")
        elif item_type == "response_item" and payload.get("type") in {"custom_tool_call", "function_call"}:
            name = payload.get("name") or UNKNOWN
            result["tool_counts"][name] = result["tool_counts"].get(name, 0) + 1
            if "spawn_agent" in name:
                request = parse_tool_input(payload.get("input", payload.get("arguments")))
                if not request:
                    mark_domains(("agents", "reuse", "fork"), "spawn_agent input 無法解析")
                result["spawn_requests"].append({
                    "call_id": payload.get("call_id") or payload.get("id") or UNKNOWN,
                    "role": request.get("agent_type") or UNKNOWN,
                    "requested_model": request.get("model") or UNKNOWN,
                    "requested_effort": request.get("reasoning_effort") or UNKNOWN,
                    "fork_turns": request.get("fork_turns") or "all",
                })
        elif item_type == "response_item" and payload.get("type") == "tool_search_call":
            result["tool_counts"]["tool_search"] = result["tool_counts"].get("tool_search", 0) + 1
        elif item_type == "response_item":
            payload_type = payload.get("type")
            tool_shaped = (
                isinstance(payload_type, str) and ("tool" in payload_type or "function" in payload_type)
            ) or any(key in payload for key in ("name", "arguments", "call_id"))
            if tool_shaped and payload_type not in KNOWN_TOOL_RESPONSE_TYPES:
                mark_domains(("tools",), f"未知 tool response：{payload_type}")
                name = payload.get("name")
                if (
                    isinstance(name, str) and ("spawn_agent" in name or "followup" in name)
                ) or any(key in payload for key in ("agent_thread_id", "agent_path")):
                    mark_domains(("agents", "reuse", "fork"), f"未知 agent tool response：{payload_type}")
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
    result["schema_recognized"] = (
        saw_session_meta and result["session_id"] != UNKNOWN and not parse_errors
    )
    return result


def discover_transcripts(
    initial: list[Path], sessions_root: Path,
) -> tuple[list[dict[str, Any]], list[str]]:
    parsed: dict[Path, dict[str, Any]] = {}
    errors: list[str] = []
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
        for child in find_session_files(sessions_root, child_ids, errors):
            if child not in parsed:
                queue.append(child)
    return list(parsed.values()), errors


def workflow_identity(codex_home: Path) -> dict[str, Any]:
    """只以目前 CODEX_HOME Git repo 為制度版本；Git ignored runtime 不參與 dirty/fingerprint。"""
    unavailable = {
        "repo": str(codex_home), "branch": None, "head": None, "origin": None,
        "managed_dirty": None, "managed_fingerprint": None,
        "unavailable_reason": "git_repository_unavailable",
    }
    try:
        top = subprocess.run(
            ["git", "-C", str(codex_home), "rev-parse", "--show-toplevel"],
            text=True, capture_output=True, timeout=10, check=False,
        )
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != codex_home.resolve():
            return unavailable
        head = subprocess.run(
            ["git", "-C", str(codex_home), "rev-parse", "HEAD"],
            text=True, capture_output=True, timeout=10, check=True,
        ).stdout.strip()
        branch = subprocess.run(
            ["git", "-C", str(codex_home), "branch", "--show-current"],
            text=True, capture_output=True, timeout=10, check=True,
        ).stdout.strip() or "DETACHED"
        remote = subprocess.run(
            ["git", "-C", str(codex_home), "remote", "get-url", "origin"],
            text=True, capture_output=True, timeout=10, check=False,
        )
        if remote.returncode == 0:
            origin, origin_reason = sanitize_remote_url(remote.stdout.strip())
        else:
            origin, origin_reason = None, "origin_unavailable"
        status = subprocess.run(
            ["git", "-C", str(codex_home), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=True,
        ).stdout
        changed: list[str] = []
        entries = [item for item in status.split(b"\0") if item]
        index = 0
        while index < len(entries):
            item = entries[index]
            if len(item) < 4:
                raise ValueError("managed_status_invalid")
            changed.append(item[3:].decode("utf-8", "surrogateescape"))
            code = item[:2].decode("ascii")
            if "R" in code or "C" in code:
                index += 1
            index += 1
        digest = hashlib.sha256()
        digest.update(head.encode("ascii") + b"\0" + status)
        for relative in sorted(set(changed)):
            path = codex_home / relative
            digest.update(relative.encode("utf-8", "surrogateescape") + b"\0")
            if path.is_symlink():
                digest.update(b"symlink\0" + os.readlink(path).encode("utf-8", "surrogateescape"))
            elif path.is_file():
                digest.update(b"file\0" + hashlib.sha256(path.read_bytes()).digest())
            else:
                digest.update(b"missing\0")
        return {
            "repo": str(codex_home), "branch": branch, "head": head, "origin": origin,
            "managed_dirty": bool(status), "managed_fingerprint": digest.hexdigest(),
            "unavailable_reason": None if origin is not None else origin_reason,
        }
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError):
        return unavailable


def config_commit(codex_home: Path) -> str:
    identity = workflow_identity(codex_home)
    head = identity.get("head")
    return str(head)[:7] if isinstance(head, str) else UNKNOWN


def comma_values(raw: str) -> list[str]:
    return [value.strip() for value in raw.split(",") if value.strip()]


def unique_strings(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def state_session_ids(state_path: Path) -> tuple[list[str], list[str]]:
    result: list[str] = []
    warnings: list[str] = []
    if not state_path.exists():
        return result, warnings
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"讀 state 檔失敗：{exc}")
        return result, warnings
    if not isinstance(state, dict):
        warnings.append("state 檔根節點不是 object")
        return result, warnings
    for key in ("session_id", "session_ids", "sessions"):
        value = state.get(key)
        if isinstance(value, str):
            result.append(value)
        elif isinstance(value, list):
            result.extend(item for item in value if isinstance(item, str))
    return unique_strings(result), warnings


def resolve_session_ids(
    args: argparse.Namespace, codex_home: Path,
) -> tuple[list[str], dict[str, Any], list[str]]:
    cli_sessions = unique_strings(comma_values(args.sessions))
    if args.transcript or cli_sessions:
        if args.transcript and cli_sessions:
            source = "explicit_transcript_and_cli"
        elif args.transcript:
            source = "explicit_transcript"
        else:
            source = "cli_session"
        return cli_sessions, {
            "source": source,
            "runtime_key": None,
            "requested_session_ids": cli_sessions,
            "explicit_transcript_count": len(args.transcript),
        }, []

    state_path = codex_home / "state" / f"{args.slug}.json"
    state_sessions, warnings = state_session_ids(state_path)
    if state_sessions:
        return state_sessions, {
            "source": "state",
            "runtime_key": None,
            "requested_session_ids": state_sessions,
            "explicit_transcript_count": 0,
        }, warnings

    runtime_session = os.environ.get("CODEX_THREAD_ID", "").strip()
    if runtime_session:
        return [runtime_session], {
            "source": "runtime_env",
            "runtime_key": "CODEX_THREAD_ID",
            "requested_session_ids": [runtime_session],
            "explicit_transcript_count": 0,
        }, warnings
    return [], {
        "source": "unavailable",
        "runtime_key": None,
        "requested_session_ids": [],
        "explicit_transcript_count": 0,
    }, warnings


def build_run(args: argparse.Namespace) -> dict[str, Any]:
    codex_home = Path(args.codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()
    sessions_root = Path(args.sessions_root).expanduser().resolve() if args.sessions_root else codex_home / "sessions"
    session_ids, session_provenance, provenance_warnings = resolve_session_ids(args, codex_home)
    initial = [Path(item).expanduser().resolve() for item in args.transcript]
    input_resolution_errors: list[str] = []
    initial.extend(find_session_files(sessions_root, session_ids, input_resolution_errors))
    initial = sorted(set(initial))
    scope_errors: list[str] = []
    if args.single_session_only and len(initial) == 1:
        transcripts = [parse_transcript(initial[0])]
        discovery_errors: list[str] = []
        transcript = transcripts[0]
        if transcript["role"] == "main" or transcript["history_mode"] != "none":
            scope_errors.append("--single-session-only 只允許 history_mode=none 的 child transcript")
    elif args.single_session_only:
        transcripts = [parse_transcript(path) for path in initial]
        discovery_errors = []
        scope_errors.append("--single-session-only 必須精確對應一份 transcript")
    else:
        transcripts, discovery_errors = discover_transcripts(initial, sessions_root) if initial else ([], [])

    resolution_errors = input_resolution_errors + discovery_errors
    for warning in provenance_warnings:
        print(f"[warn] {warning}", file=sys.stderr)

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
    successful_started = [
        event for event in started
        if event["event_id"] != UNKNOWN and event["event_id"] in spawn_request_map
    ]
    for event in successful_started:
        role = role_from_agent_path(event["agent_path"])
        agent_calls[role] = agent_calls.get(role, 0) + 1

    depth_values = [max(0, str(item["agent_path"]).strip("/").count("/")) for item in effective_by_session]
    histories = [item["history_mode"] for item in effective_by_session if item["role"] != "main"]
    reused_threads = {event["agent_thread_id"] for event in interacted if event["agent_thread_id"] != UNKNOWN}
    schemas = {transcript["usage_schema"] for transcript in transcripts if transcript["usage_schema"]}
    parse_errors = [error for transcript in transcripts for error in transcript["parse_errors"]]
    domain_errors = {
        domain: list(dict.fromkeys(
            error for transcript in transcripts for error in transcript["domain_errors"][domain]
        ))
        for domain in ("tokens", *STRUCTURAL_DOMAINS)
    }
    session_id_counts: dict[str, int] = {}
    for transcript in transcripts:
        session_id = transcript["session_id"]
        if session_id != UNKNOWN:
            session_id_counts[session_id] = session_id_counts.get(session_id, 0) + 1
    duplicate_session_ids = sorted(
        session_id for session_id, count in session_id_counts.items() if count > 1
    )
    if duplicate_session_ids:
        scope_errors.append(f"transcript session id 重複：{','.join(duplicate_session_ids)}")
    errors = list(dict.fromkeys(
        scope_errors + resolution_errors + parse_errors
        + [f"{domain} schema：{error}" for domain, values in domain_errors.items() for error in values]
    ))
    structural_schema_valid = bool(transcripts) and not (
        scope_errors or input_resolution_errors or parse_errors
    ) and all(transcript["schema_recognized"] for transcript in transcripts)
    schema_valid = structural_schema_valid
    if not transcripts:
        schema_valid = False
        errors.append("找不到任何 transcript")
    elif scope_errors or input_resolution_errors or parse_errors:
        schema_valid = False
    elif not structural_schema_valid:
        schema_valid = False
        errors.append("至少一份 transcript 缺少可識別的 session_meta")
    elif usage_records != len(transcripts):
        schema_valid = False
        missing = [transcript["session_id"] for transcript in transcripts if not transcript["usage_schema"]]
        errors.append(f"discovered transcript 缺少可解析 usage：{','.join(missing)}")
    elif len(schemas) != 1:
        schema_valid = False
        errors.append("同一 run 混用 Codex v2 cumulative 與 legacy message usage")
    elif domain_errors["tokens"]:
        schema_valid = False

    token_observations: list[dict[str, Any]] = []
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
    tree_incomplete = bool(spawn_conflicts or discovery_errors)
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
            tree_incomplete = True
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
        tree_incomplete = True
        warnings.append("至少一個 started child 無法對應 spawn request")

    if not schema_valid:
        status = "unsupported_schema"
    elif runtime_failed:
        status = "incomplete_runtime"
        errors.append("child spawn/runtime 驗證未通過")
    elif any(domain_errors[domain] for domain in STRUCTURAL_DOMAINS):
        status = "unsupported_schema"
    elif len(transcripts) > 1:
        status = "unsupported_token_attribution"
        errors.append("多 transcript counter 無安全的 sum/max 聚合規則；只保留逐 session raw observation")
    else:
        status = "ok"

    exact_domain = {"status": "exact", "reason": None}
    structural_availability: dict[str, dict[str, str | None]] = {}
    for domain in STRUCTURAL_DOMAINS:
        if not structural_schema_valid:
            reason = "unsupported_schema"
        elif tree_incomplete:
            reason = "incomplete_transcript_tree"
        elif domain_errors[domain]:
            reason = f"unsupported_{domain}_schema"
        else:
            reason = ""
        structural_availability[domain] = (
            exact_domain if not reason else {"status": "unavailable", "reason": reason}
        )

    attribution_status = "unavailable"
    if not schema_valid:
        token_availability = {"status": "unavailable", "reason": "unsupported_schema"}
    elif tree_incomplete:
        token_availability = {"status": "unavailable", "reason": "incomplete_transcript_tree"}
    else:
        token_availability = {"status": "unavailable", "reason": "unsupported_token_attribution"}
    if schema_valid and not tree_incomplete and len(transcripts) == 1:
        transcript = transcripts[0]
        total = dict(transcript["usage"])
        if transcript["role"] == "main":
            main_usage = dict(total)
        else:
            tokens_by_agent[transcript["role"]] = dict(total)
        models = {context["model"] for context in transcript["effective_contexts"] if context["model"] != UNKNOWN}
        tokens_by_model[next(iter(models)) if len(models) == 1 else UNKNOWN] = dict(total)
        attribution_status = "exact_single_transcript"
        token_availability = exact_domain
    elif schema_valid and not tree_incomplete and len(transcripts) > 1:
        attribution_status = "unsupported_multi_transcript"
        token_availability = {"status": "unavailable", "reason": "unsupported_token_attribution"}

    recorded_session_ids = session_ids or unique_strings(
        transcript["session_id"] for transcript in transcripts if transcript["session_id"] != UNKNOWN
    )
    session_provenance["resolved_session_ids"] = recorded_session_ids

    agents_exact = structural_availability["agents"]["status"] == "exact"
    tools_exact = structural_availability["tools"]["status"] == "exact"
    reuse_exact = structural_availability["reuse"]["status"] == "exact"
    fork_exact = structural_availability["fork"]["status"] == "exact"
    fork_summary = {
        "max_depth": max(depth_values, default=0),
        "requested": [request["fork_turns"] for request in spawn_requests],
        "bounded_count": sum(1 for value in histories if value in {"none", "bounded"}),
        "full_history_count": sum(1 for value in histories if value in {"all", "full"}),
        "unknown_count": sum(1 for value in histories if value not in {"none", "bounded", "all", "full"}),
    } if fork_exact else None
    reuse_summary = {
        "follow_up_events": len(interacted),
        "reused_agent_threads": len(reused_threads),
    } if reuse_exact else None

    workflow = workflow_identity(codex_home)
    return {
        "schema_version": 3,
        "collector_status": status,
        "collector_errors": errors,
        "slug": args.slug,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "codex_home": str(codex_home),
        "config_commit": str(workflow["head"])[:7] if workflow["head"] else UNKNOWN,
        "workflow_identity": workflow,
        "product": args.product,
        "sessions": recorded_session_ids,
        "session_provenance": session_provenance,
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
            "requested_agents": child_runtime if agents_exact else None,
            "agent_observations": child_runtime if structural_schema_valid else None,
            "effective_by_session": effective_by_session if structural_schema_valid else None,
            "validation_status": (
                "unavailable" if not agents_exact else ("failed" if runtime_failed else "ok")
            ),
            "warnings": provenance_warnings + warnings,
        },
        "availability": {
            "tokens": token_availability,
            **structural_availability,
        },
        "tokens": {"main_loop": main_usage, "total": total},
        "token_attribution": {"status": attribution_status, "transcript_count": len(transcripts)},
        "token_observations": token_observations or None,
        "tokens_by_model": tokens_by_model if token_availability["status"] == "exact" else None,
        "tokens_by_agent": tokens_by_agent if token_availability["status"] == "exact" else None,
        "agent_calls": agent_calls if agents_exact else None,
        "agent_spawn_total": sum(agent_calls.values()) if agents_exact else None,
        "fork": fork_summary,
        "reuse": reuse_summary,
        "tool_counts": tool_counts if tools_exact else None,
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
        spawn_total = run["agent_spawn_total"] if run["agent_spawn_total"] is not None else "unavailable"
        reuse = run["reuse"]["follow_up_events"] if run["reuse"] is not None else "unavailable"
        print(f"agent 派遣={spawn_total} reuse={reuse} risk={run['risk_lane']}")
        for warning in run["runtime"]["warnings"]:
            print(f"[warn] {warning}", file=sys.stderr)
    return 0 if run["collector_status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
