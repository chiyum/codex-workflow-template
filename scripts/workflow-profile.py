#!/usr/bin/env python3
"""確定性解析 workflow risk lane、執行 profile 與 agent model/effort。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


HIGH_RISK = {
    "auth", "permission", "tenant", "database", "migration", "data-consistency",
    "cross-repo-contract", "infrastructure", "payment", "irreversible", "prod",
    "major-architecture", "new-visual",
}
ROLES = {
    "main", "architect", "reviewer", "qa", "test", "pm", "verifier", "evidence",
    "ui_designer", "design_reviewer", "security_auditor",
}
PROFILES = ("lite", "standard", "full")
PROFILE_RANK = {profile: rank for rank, profile in enumerate(PROFILES)}
LANE_PROFILE_FLOOR = {"L1": "lite", "L2": "standard", "L3": "full"}
SPECIALIZED_GATES = (
    "security_auditor_when_triggered",
    "ui_designer_and_design_reviewer_when_triggered",
)
MANDATORY_INTERRUPTIONS = (
    "irreversible_delete", "spending", "security", "prod", "contradictory_requirements",
)


def classify(args: argparse.Namespace) -> dict:
    risks = sorted(set(args.risk))
    denied = sorted(set(risks) & HIGH_RISK)
    if denied:
        lane, reason = "L3", f"命中高風險：{','.join(denied)}"
    elif (
        args.l1_candidate
        and 1 <= args.acceptance_count <= 3
        and args.single_repo
        and args.rollbackable
        and args.target in {"local", "dev"}
    ):
        lane, reason = "L1", "符合 low-risk 全部必要條件"
    else:
        lane, reason = "L2", "未命中高風險，但不滿足 L1 全部必要條件"
    return {"policy_version": 2, "lane": lane, "reason": reason, "risks": risks, "denied": denied}


def next_word(raw: str, cursor: int) -> tuple[str | None, int, int]:
    """只辨識空白分隔的前導字；需求本體不做 shell tokenization。"""
    length = len(raw)
    while cursor < length and raw[cursor].isspace():
        cursor += 1
    if cursor == length:
        return None, cursor, cursor
    end = cursor
    while end < length and not raw[end].isspace():
        end += 1
    return raw[cursor:end], cursor, end


def parse_dev(args: argparse.Namespace) -> dict:
    """解析 `$dev` 前導 modifiers，並逐字保留剩餘需求。"""
    source = sys.stdin.read() if args.stdin else args.input
    if source is None or not source.strip():
        raise ValueError("開發需求不得為空")
    raw = source.lstrip()

    explicit_dev = raw.startswith("$dev") and (len(raw) == len("$dev") or raw[len("$dev")].isspace())
    if not explicit_dev:
        return {
            "policy_version": 2,
            "entrypoint": "natural_language",
            "mode": "standard",
            "requested_profile": "standard",
            "request": source.strip(),
            "continue_slug": None,
        }

    cursor = len("$dev")
    first, _, first_end = next_word(raw, cursor)
    if first == "繼續":
        slug, _, slug_end = next_word(raw, first_end)
        extra, _, _ = next_word(raw, slug_end)
        if slug is None or extra is not None:
            raise ValueError("接續語法必須是 `$dev 繼續 <slug>`")
        return {
            "policy_version": 2,
            "entrypoint": "dev",
            "mode": "continue",
            "requested_profile": None,
            "request": None,
            "continue_slug": slug,
        }

    requested_profile = "standard"
    mode = "standard"
    seen_profile = False
    seen_auto = False
    request_start = len(raw)
    while True:
        modifier, token_start, token_end = next_word(raw, cursor)
        if modifier not in {*PROFILES, "auto"}:
            request_start = token_start
            break
        if modifier == "auto":
            if seen_auto:
                raise ValueError("auto modifier 不得重複")
            seen_auto = True
            mode = "auto"
        else:
            if seen_profile:
                raise ValueError("workflow profile 不得重複")
            seen_profile = True
            requested_profile = modifier
        cursor = token_end
    request = raw[request_start:]
    if not request.strip():
        raise ValueError("`$dev` 後必須提供需求文字")
    return {
        "policy_version": 2,
        "entrypoint": "dev",
        "mode": mode,
        "requested_profile": requested_profile,
        "request": request,
        "continue_slug": None,
    }


def profile_for(lane: str, role: str) -> tuple[str, str, str]:
    if role == "main":
        return "gpt-5.6-sol", "medium", "parent"
    if role == "evidence":
        return "gpt-5.6-terra", "low", "none"
    if role in {"qa", "test", "verifier"}:
        return "gpt-5.6-terra", "medium", "none"
    if role == "security_auditor":
        return "gpt-5.6-sol", "high", "bounded"
    if lane == "L3":
        return "gpt-5.6-sol", "high", "bounded"
    if role in {"architect", "reviewer", "ui_designer", "design_reviewer"}:
        effort = "medium" if lane == "L1" else "high"
        return "gpt-5.6-sol", effort, "bounded"
    if lane == "L1" and role == "pm":
        return "gpt-5.6-terra", "low", "none"
    return "gpt-5.6-terra", "medium", "none"


def resolve(args: argparse.Namespace) -> dict:
    model, effort, history_mode = profile_for(args.lane, args.role)
    xhigh_reason = args.xhigh_reason.strip() if args.xhigh_reason is not None else None
    if args.xhigh_reason is not None and not xhigh_reason:
        raise ValueError("--xhigh-reason 去除空白後不可為空")
    if xhigh_reason:
        if args.role not in {"architect", "reviewer"}:
            raise ValueError("xhigh 只允許 architect/reviewer 單一葉")
        if args.lane == "L1":
            raise ValueError("L1 不允許 xhigh；請重新分類風險")
        effort = "xhigh"
    action = "followup" if args.reuse else "spawn"
    fork_turns = None if action == "followup" or args.role == "main" else ("none" if history_mode == "none" else "3")
    return {
        "policy_version": 2,
        "lane": args.lane,
        "role": args.role,
        "action": action,
        "model": model,
        "model_reasoning_effort": effort,
        "fork_turns": fork_turns,
        "history_mode": history_mode,
        "reuse": args.reuse,
        "xhigh_reason": xhigh_reason,
    }


def effective_profile(lane: str, requested: str) -> tuple[str, str | None]:
    floor = LANE_PROFILE_FLOOR[lane]
    if PROFILE_RANK[requested] >= PROFILE_RANK[floor]:
        return requested, None
    return floor, f"{lane} 最低允許 {floor}；已由 requested {requested} 升級"


def load_continue_state(path: str, lane: str) -> tuple[str, str, str | None, dict]:
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("continue state 無法讀取") from exc
    if not isinstance(state, dict):
        raise ValueError("continue state 必須是 JSON object")
    required = ("cwd", "lane", "requested_profile", "effective_profile", "profile_upgrade_reason", "next_action")
    missing = [field for field in required if field not in state]
    if missing:
        raise ValueError(f"continue state 缺少必要欄位：{','.join(missing)}")
    if not isinstance(state["cwd"], str) or not state["cwd"].strip():
        raise ValueError("continue state cwd 不得為空")
    if state.get("lane") != lane:
        raise ValueError("continue state lane 與 --lane 不一致")
    requested = state.get("requested_profile")
    effective = state.get("effective_profile")
    reason = state.get("profile_upgrade_reason")
    if requested not in PROFILES or effective not in PROFILES:
        raise ValueError("continue state 缺少合法 requested/effective profile")
    if PROFILE_RANK[effective] < PROFILE_RANK[requested]:
        raise ValueError("continue state 不得把 requested profile 降級")
    if PROFILE_RANK[effective] < PROFILE_RANK[LANE_PROFILE_FLOOR[lane]]:
        raise ValueError("continue state effective profile 低於 lane 下限")
    expected_effective, _ = effective_profile(lane, requested)
    if effective != expected_effective:
        raise ValueError("continue state effective profile 與 lane/requested 的確定性結果不一致")
    if requested != effective and (not isinstance(reason, str) or not reason.strip()):
        raise ValueError("continue state 發生升級時必須保存原因")
    if requested == effective and reason is not None:
        raise ValueError("continue state 未升級時 profile_upgrade_reason 必須為 null")
    if not isinstance(state.get("next_action"), str) or not state["next_action"].strip():
        raise ValueError("continue state 缺少 next_action")
    return requested, effective, reason, state


def gates_for(profile: str) -> dict:
    if profile == "full":
        return {
            "acceptance_owner": "pm",
            "acceptance_pm_spawn_count": 1,
            "agent_sequence": ["pm", "architect", "reviewer", "qa", "pm"],
            "ordered_role_gates": [
                "pm:acceptance", "architect:implementation", "reviewer:code_review",
                "qa:verification", "pm:independent_acceptance",
            ],
            "role_spawn_counts": {"architect": 1, "reviewer": 1, "qa": 1, "pm": 2, "verifier": 0},
            "disabled_core_roles": [],
            "pm_acceptance": "independent_pm",
            "architect_self_test": True,
            "verifier_reuse": False,
        }
    if profile == "standard":
        return {
            "acceptance_owner": "verifier",
            "acceptance_pm_spawn_count": 0,
            "agent_sequence": ["verifier", "architect", "reviewer", "verifier"],
            "ordered_role_gates": [
                "verifier:acceptance", "architect:implementation", "reviewer:code_review",
                "verifier:verification_and_acceptance",
            ],
            "role_spawn_counts": {"architect": 1, "reviewer": 1, "qa": 0, "pm": 0, "verifier": 1},
            "disabled_core_roles": ["qa", "pm"],
            "pm_acceptance": "verifier",
            "architect_self_test": True,
            "verifier_reuse": True,
        }
    return {
        "acceptance_owner": "main",
        "acceptance_pm_spawn_count": 0,
        "agent_sequence": ["architect", "verifier"],
        "ordered_role_gates": [
            "architect:implementation_and_self_test", "verifier:verification_and_acceptance",
        ],
        "role_spawn_counts": {"architect": 1, "reviewer": 0, "qa": 0, "pm": 0, "verifier": 1},
        "disabled_core_roles": ["reviewer", "qa", "pm"],
        "pm_acceptance": "verifier",
        "architect_self_test": True,
        "verifier_reuse": False,
    }


def plan(args: argparse.Namespace) -> dict:
    """把 lane 與 profile 展開成可測試 gate 計畫，避免文字流程各自解讀。"""
    if args.mode == "continue":
        if not args.state:
            raise ValueError("continue mode 必須提供 --state")
        requested, effective, upgrade_reason, state = load_continue_state(args.state, args.lane)
    else:
        if args.state:
            raise ValueError("只有 continue mode 可使用 --state")
        requested = args.profile
        effective, upgrade_reason = effective_profile(args.lane, requested)
        state = None
    result = {
        "policy_version": 2,
        "lane": args.lane,
        "mode": args.mode,
        "requested_profile": requested,
        "effective_profile": effective,
        "profile_upgrade_reason": upgrade_reason,
        "confirmation_required": args.mode != "continue",
        "pre_review": "targeted" if args.lane == "L1" else "full",
        "specialized_gates": list(SPECIALIZED_GATES),
        "mandatory_interruptions": list(MANDATORY_INTERRUPTIONS),
        **gates_for(effective),
    }
    if state is not None:
        result.update({
            "continued_from_state": True,
            "state_task": state.get("task"),
            "next_action": state["next_action"],
        })
    else:
        result["continued_from_state"] = False
    return result


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    subparsers = root.add_subparsers(dest="command", required=True)
    lane = subparsers.add_parser("lane")
    lane.add_argument("--risk", action="append", default=[], choices=sorted(HIGH_RISK))
    lane.add_argument("--l1-candidate", action="store_true")
    lane.add_argument("--acceptance-count", type=int, default=0)
    lane.add_argument("--single-repo", action="store_true")
    lane.add_argument("--rollbackable", action="store_true")
    lane.add_argument("--target", choices=("local", "dev", "prod", "unknown"), default="unknown")
    parse_parser = subparsers.add_parser("parse")
    parse_input = parse_parser.add_mutually_exclusive_group(required=True)
    parse_input.add_argument("--input", help="以單一 argv 傳入原始需求")
    parse_input.add_argument("--stdin", action="store_true", help="由標準輸入讀取原始需求")
    resolve_parser = subparsers.add_parser("resolve")
    resolve_parser.add_argument("--lane", choices=("L1", "L2", "L3"), required=True)
    resolve_parser.add_argument("--role", choices=sorted(ROLES), required=True)
    resolve_parser.add_argument("--reuse", action="store_true")
    resolve_parser.add_argument("--xhigh-reason", default=None)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--lane", choices=("L1", "L2", "L3"), required=True)
    plan_parser.add_argument("--mode", choices=("auto", "standard", "continue"), default="standard")
    plan_parser.add_argument("--profile", choices=PROFILES, default="standard")
    plan_parser.add_argument("--state")
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "lane":
            result = classify(args)
        elif args.command == "parse":
            result = parse_dev(args)
        elif args.command == "resolve":
            result = resolve(args)
        else:
            result = plan(args)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
