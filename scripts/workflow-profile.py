#!/usr/bin/env python3
"""確定性解析 workflow risk lane 與 agent model/effort profile。"""
from __future__ import annotations

import argparse
import json
import sys


HIGH_RISK = {
    "auth", "permission", "tenant", "database", "migration", "data-consistency",
    "cross-repo-contract", "infrastructure", "payment", "irreversible", "prod",
    "major-architecture", "new-visual",
}
ROLES = {
    "main", "architect", "reviewer", "qa", "test", "pm", "evidence",
    "ui_designer", "design_reviewer", "security_auditor",
}


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
    return {"policy_version": 1, "lane": lane, "reason": reason, "risks": risks, "denied": denied}


def profile_for(lane: str, role: str) -> tuple[str, str, str]:
    if role == "main":
        return "gpt-5.6-sol", "medium", "parent"
    if role == "evidence":
        return "gpt-5.6-terra", "low", "none"
    if role in {"qa", "test"}:
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
        "policy_version": 1,
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


def plan(args: argparse.Namespace) -> dict:
    """把 lane 直接展開成可測試的 gate 計畫，避免文字流程各自解讀。"""
    if args.lane == "L1":
        return {
            "policy_version": 1,
            "lane": args.lane,
            "mode": args.mode,
            "acceptance_owner": "main",
            "acceptance_pm_spawn_count": 0,
            "pre_review": "targeted",
            "pm_acceptance": "main_evidence_audit",
        }
    return {
        "policy_version": 1,
        "lane": args.lane,
        "mode": args.mode,
        "acceptance_owner": "pm",
        "acceptance_pm_spawn_count": 1,
        "pre_review": "full",
        "pm_acceptance": "independent_pm",
    }


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
    resolve_parser = subparsers.add_parser("resolve")
    resolve_parser.add_argument("--lane", choices=("L1", "L2", "L3"), required=True)
    resolve_parser.add_argument("--role", choices=sorted(ROLES), required=True)
    resolve_parser.add_argument("--reuse", action="store_true")
    resolve_parser.add_argument("--xhigh-reason", default=None)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--lane", choices=("L1", "L2", "L3"), required=True)
    plan_parser.add_argument("--mode", choices=("auto", "standard", "continue"), default="standard")
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "lane":
            result = classify(args)
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
