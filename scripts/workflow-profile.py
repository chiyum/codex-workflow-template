#!/usr/bin/env python3
"""確定性解析 workflow risk lane、執行 profile 與 agent model/effort。"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys


HIGH_RISK = {
    "auth", "permission", "tenant", "database", "migration", "data-consistency",
    "cross-repo-contract", "infrastructure", "payment", "irreversible",
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
    "ui_designer_and_design_reviewer_when_triggered",
)
MANDATORY_INTERRUPTIONS = (
    "irreversible_delete", "spending", "security", "prod", "contradictory_requirements",
)
REQUIRED_CODEX_VALIDATIONS = ("lint", "build", "test", "pre_review")
RELEASE_TARGETS = ("local", "dev", "prod", "unknown")
PRODUCT_POLICY_STATUSES = ("known", "missing", "unknown")
SECURITY_REVIEW_SURFACES = {"auth", "permission", "tenant", "external-input"}
SECURITY_AUDITOR_SURFACES = {
    "port", "proxy", "container", "pipeline", "database-exposure", "redis-exposure",
}
CHANGE_SURFACES = tuple(sorted({
    "ui", *SECURITY_REVIEW_SURFACES, *SECURITY_AUDITOR_SURFACES, "external-scan",
}))
SHA = re.compile(r"^[0-9a-f]{40,64}$")
BASELINE_FRESHNESS = timedelta(hours=24)


def validation_manifest(lane: str, effective_profile: str, pre_review: str) -> dict:
    source = Path(__file__).resolve()
    manifest = {
        "schema_version": "codex-validation-manifest/v1",
        "policy_version": 3,
        "source_path": "scripts/workflow-profile.py",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "lane": lane,
        "effective_profile": effective_profile,
        "pre_review": pre_review,
        "required_validations": list(REQUIRED_CODEX_VALIDATIONS),
        "test_scope": "diff_acceptance_and_nearest_required_sentinels",
        "full_site_authorized": False,
    }
    manifest["manifest_sha256"] = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return manifest


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
    ):
        lane, reason = "L1", "符合 low-risk 全部必要條件"
    else:
        lane, reason = "L2", "未命中高風險，但不滿足 L1 全部必要條件"
    return {
        "policy_version": 3,
        "lane": lane,
        "change_lane": lane,
        "reason": reason,
        "change_risks": risks,
        "denied": denied,
        "release_risk": release_overlay(args.target, "unknown"),
    }


def release_overlay(target: str, policy_status: str) -> dict:
    gates: list[str] = []
    direct_prod = target == "prod"
    if target in {"dev", "prod"}:
        gates.extend(["release_ancestor", "deployment_version"])
    if direct_prod:
        gates.append("user_direct_prod_confirmation")
    fail_safe = direct_prod and policy_status in {"missing", "unknown"}
    return {
        "target": target,
        "product_policy_status": policy_status,
        "gates": gates,
        "direct_prod_requires_confirmation": direct_prod,
        "fail_safe_full": fail_safe,
        "unavailable_reason": "product_release_policy_unavailable" if fail_safe else None,
    }


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
            "policy_version": 3,
            "entrypoint": "natural_language",
            "mode": "standard",
            "requested_profile": None,
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
            "policy_version": 3,
            "entrypoint": "dev",
            "mode": "continue",
            "requested_profile": None,
            "request": None,
            "continue_slug": slug,
        }

    requested_profile = None
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
            if not seen_profile:
                # `$dev auto` 的舊語義固定為 Standard；一般未指定 profile 則交由 lane floor 決定。
                requested_profile = "standard"
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
        "policy_version": 3,
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


def effective_profile_with_release(
    lane: str, requested: str | None, product_floor: str | None, release: dict,
) -> tuple[str, str, str | None]:
    lane_floor = LANE_PROFILE_FLOOR[lane]
    resolved_requested = requested or lane_floor
    floor_candidates = [lane_floor]
    if product_floor is not None:
        floor_candidates.append(product_floor)
    if release["fail_safe_full"]:
        floor_candidates.append("full")
    effective_floor = max(floor_candidates, key=PROFILE_RANK.__getitem__)
    effective = max((resolved_requested, effective_floor), key=PROFILE_RANK.__getitem__)
    if effective == resolved_requested:
        return resolved_requested, effective, None
    reasons: list[str] = []
    if PROFILE_RANK[lane_floor] > PROFILE_RANK[resolved_requested]:
        reasons.append(f"change lane {lane} floor={lane_floor}")
    if product_floor is not None and PROFILE_RANK[product_floor] > PROFILE_RANK[resolved_requested]:
        reasons.append(f"product release floor={product_floor}")
    if release["fail_safe_full"]:
        reasons.append("direct prod 缺少可用 product release policy，fail-safe Full")
    return resolved_requested, effective, "；".join(reasons)


def canonical_sha256(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def parse_fresh_timestamp(value: object, field: str, now: datetime) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"baseline receipt {field} 無效")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"baseline receipt {field} 無效") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"baseline receipt {field} 無效")
    normalized = parsed.astimezone(timezone.utc)
    if normalized > now + timedelta(minutes=5) or now - normalized > BASELINE_FRESHNESS:
        raise ValueError(f"baseline receipt {field} 不新鮮")
    return normalized


def load_baseline_receipt(path: str, expected_target: str) -> dict:
    receipt_path = Path(path).expanduser().resolve(strict=True)
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("baseline receipt 無法讀取") from exc
    if not isinstance(receipt, dict) or receipt.get("schema_version") != "development-baseline/v2":
        raise ValueError("baseline receipt schema 無效")
    now = datetime.now(timezone.utc)
    queried_at = parse_fresh_timestamp(receipt.get("queried_at"), "queried_at", now)
    if receipt.get("target_environment") != expected_target:
        raise ValueError("baseline receipt target 與 plan 不一致")
    product = receipt.get("product")
    if not isinstance(product, str) or not product.strip():
        raise ValueError("baseline receipt product 無效")
    collector = receipt.get("collector_identity")
    collector_path = Path(__file__).resolve().parent / "development-baseline.py"
    if (
        not isinstance(collector, dict)
        or collector.get("path") != "scripts/development-baseline.py"
        or not collector_path.is_file()
        or collector.get("sha256") != hashlib.sha256(collector_path.read_bytes()).hexdigest()
    ):
        raise ValueError("baseline receipt collector identity 無效")
    expected_receipt_sha = receipt.get("receipt_sha256")
    unsigned = dict(receipt)
    unsigned.pop("confirmation", None)
    unsigned.pop("receipt_sha256", None)
    if not isinstance(expected_receipt_sha, str) or canonical_sha256(unsigned) != expected_receipt_sha:
        raise ValueError("baseline receipt content hash 無效")
    confirmation = receipt.get("confirmation")
    if (
        not isinstance(confirmation, dict)
        or set(confirmation) != {"confirmed_by", "confirmed_at", "receipt_sha256"}
        or confirmation.get("confirmed_by") != "user"
        or confirmation.get("receipt_sha256") != expected_receipt_sha
    ):
        raise ValueError("baseline receipt 缺少 user confirmation")
    confirmed_at = parse_fresh_timestamp(confirmation.get("confirmed_at"), "confirmed_at", now)
    if confirmed_at < queried_at:
        raise ValueError("baseline receipt confirmation 早於查詢")
    repos = receipt.get("repos")
    if not isinstance(repos, list) or not repos:
        raise ValueError("baseline receipt repo identity 無效")
    repo_identities: list[dict] = []
    identity_fields = (
        "path", "branch", "local_head", "remote", "remote_head",
        "proposed_baseline", "proposed_baseline_tree",
    )
    for repo in repos:
        if not isinstance(repo, dict):
            raise ValueError("baseline receipt repo identity 無效")
        identity = {key: repo.get(key) for key in identity_fields}
        if (
            not isinstance(identity["path"], str)
            or not Path(identity["path"]).is_absolute()
            or not isinstance(identity["branch"], str)
            or not isinstance(identity["remote"], str)
            or not SHA.fullmatch(str(identity["local_head"]))
            or (identity["remote_head"] is not None and not SHA.fullmatch(str(identity["remote_head"])))
            or not SHA.fullmatch(str(identity["proposed_baseline"]))
            or not SHA.fullmatch(str(identity["proposed_baseline_tree"]))
            or repo.get("repo_identity_sha256") != canonical_sha256(identity)
        ):
            raise ValueError("baseline receipt repo identity 無效")
        authority = repo.get("behavior_authority")
        sources = authority.get("sources") if isinstance(authority, dict) else None
        if not isinstance(sources, list) or not sources or any(
            not isinstance(source, dict)
            or set(source) != {"path", "blob"}
            or not isinstance(source["path"], str)
            or source["path"].startswith("/")
            or ".." in Path(source["path"]).parts
            or not SHA.fullmatch(str(source["blob"]))
            for source in sources
        ):
            raise ValueError("baseline receipt behavior source identity 無效")
        repo_identities.append({**identity, "repo_identity_sha256": repo["repo_identity_sha256"]})
    identity = {
        "receipt_path": str(receipt_path),
        "receipt_sha256": expected_receipt_sha,
        "collector_sha256": collector["sha256"],
        "queried_at": receipt["queried_at"],
        "confirmed_at": confirmation["confirmed_at"],
        "product": product,
        "target_environment": expected_target,
        "repos": repo_identities,
    }
    identity["identity_sha256"] = canonical_sha256(identity)
    return identity


def load_continue_state(path: str, lane: str) -> tuple[str, str, str | None, dict, dict, str | None, dict]:
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("continue state 無法讀取") from exc
    if not isinstance(state, dict):
        raise ValueError("continue state 必須是 JSON object")
    required = (
        "cwd", "lane", "requested_profile", "effective_profile", "profile_upgrade_reason",
        "next_action", "target", "product_policy_status", "product_release_floor",
        "release_risk", "effective_floor", "baseline_receipt_path", "baseline_receipt_identity",
    )
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
    target = state.get("target")
    policy_status = state.get("product_policy_status")
    product_floor = state.get("product_release_floor")
    if target not in RELEASE_TARGETS or policy_status not in PRODUCT_POLICY_STATUSES:
        raise ValueError("continue state release identity 無效")
    if product_floor is not None and product_floor not in PROFILES:
        raise ValueError("continue state product release floor 無效")
    release = release_overlay(target, policy_status)
    if state.get("release_risk") != release:
        raise ValueError("continue state release gates 與確定性結果不一致")
    requested_expected, expected_effective, expected_reason = effective_profile_with_release(
        lane, requested, product_floor, release,
    )
    if requested_expected != requested:
        raise ValueError("continue state requested profile 無效")
    if effective != expected_effective:
        raise ValueError("continue state effective profile 與完整 plan identity 不一致")
    if reason != expected_reason:
        raise ValueError("continue state profile_upgrade_reason 與確定性結果不一致")
    expected_floor = max(
        [LANE_PROFILE_FLOOR[lane], product_floor or "lite", "full" if release["fail_safe_full"] else "lite"],
        key=PROFILE_RANK.__getitem__,
    )
    if state.get("effective_floor") != expected_floor:
        raise ValueError("continue state effective floor 與確定性結果不一致")
    receipt_path = state.get("baseline_receipt_path")
    if not isinstance(receipt_path, str) or not receipt_path:
        raise ValueError("continue state 缺少 baseline receipt")
    receipt_identity = load_baseline_receipt(receipt_path, target)
    if state.get("baseline_receipt_identity") != receipt_identity:
        raise ValueError("continue state baseline receipt identity 不一致")
    if not isinstance(state.get("next_action"), str) or not state["next_action"].strip():
        raise ValueError("continue state 缺少 next_action")
    return requested, effective, reason, state, release, product_floor, receipt_identity


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
        if any(value is not None for value in (
            args.profile, args.target, args.product_release_floor,
            args.product_policy_status, args.baseline_receipt,
        )):
            raise ValueError("continue mode 不得以 CLI 覆寫 plan identity")
        requested, effective, upgrade_reason, state, release, product_floor, receipt_identity = (
            load_continue_state(args.state, args.lane)
        )
        target = state["target"]
        policy_status = state["product_policy_status"]
    else:
        if args.state:
            raise ValueError("只有 continue mode 可使用 --state")
        target = args.target or "unknown"
        policy_status = args.product_policy_status or "unknown"
        product_floor = args.product_release_floor
        release = release_overlay(target, policy_status)
        requested, effective, upgrade_reason = effective_profile_with_release(
            args.lane, args.profile, product_floor, release,
        )
        state = None
        receipt_identity = load_baseline_receipt(args.baseline_receipt, target) if args.baseline_receipt else None
    pre_review = "targeted"
    surfaces = set(args.surface)
    reviewer_modules = ["security-and-tenancy"] if surfaces & SECURITY_REVIEW_SURFACES else []
    security_auditor = bool(surfaces & SECURITY_AUDITOR_SURFACES)
    external_scan_confirmation = "external-scan" in surfaces
    broad_repo_default = args.repo_test_scope in {"all", "e2e"}
    expensive_unsplittable = broad_repo_default and args.test_filter == "unavailable" and args.test_cost == "material"
    result = {
        "policy_version": 3,
        "lane": args.lane,
        "change_lane": args.lane,
        "mode": args.mode,
        "requested_profile": requested,
        "effective_profile": effective,
        "profile_upgrade_reason": upgrade_reason,
        "confirmation_required": args.mode != "continue",
        "pre_review": pre_review,
        "test_scope": "diff_acceptance_and_nearest_required_sentinels",
        "full_site_authorized": False,
        "baseline_gate": {
            "status": "confirmed" if receipt_identity is not None else "missing",
            "fresh_receipt_required": True,
            "user_confirmation_required": receipt_identity is None,
            "code_write_blocked": receipt_identity is None,
            "writer_selection_blocked": receipt_identity is None,
            "receipt_identity": receipt_identity,
        },
        "test_execution": {
            "repo_default_scope": args.repo_test_scope,
            "filter": args.test_filter,
            "cost": args.test_cost,
            "scope": "diff_acceptance_and_nearest_required_sentinels",
            "requires_user_confirmation": expensive_unsplittable,
            "blocked_reason": "unsplittable_broad_test_has_material_cost" if expensive_unsplittable else None,
        },
        "validation_manifest": validation_manifest(args.lane, effective, pre_review),
        "specialized_gates": list(SPECIALIZED_GATES),
        "reviewer_modules": reviewer_modules,
        "security_auditor": {
            "triggered": security_auditor,
            "scope": sorted(surfaces & SECURITY_AUDITOR_SURFACES),
        },
        "external_scan_requires_confirmation": external_scan_confirmation,
        "release_risk": release,
        "effective_floor": max(
            [LANE_PROFILE_FLOOR[args.lane], product_floor or "lite", "full" if release["fail_safe_full"] else "lite"],
            key=PROFILE_RANK.__getitem__,
        ),
        "mandatory_interruptions": list(MANDATORY_INTERRUPTIONS),
        **gates_for(effective),
    }
    if state is not None:
        result.update({
            "continued_from_state": True,
            "state_task": state.get("task"),
            "next_action": state["next_action"],
            "target": target,
            "product_policy_status": policy_status,
            "product_release_floor": product_floor,
        })
    else:
        result["continued_from_state"] = False
        result.update({
            "target": target,
            "product_policy_status": policy_status,
            "product_release_floor": product_floor,
        })
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
    plan_parser.add_argument("--profile", choices=PROFILES)
    plan_parser.add_argument("--state")
    plan_parser.add_argument("--target", choices=RELEASE_TARGETS)
    plan_parser.add_argument("--product-release-floor", choices=PROFILES)
    plan_parser.add_argument("--product-policy-status", choices=PRODUCT_POLICY_STATUSES)
    plan_parser.add_argument("--surface", action="append", default=[], choices=CHANGE_SURFACES)
    plan_parser.add_argument("--baseline-receipt")
    plan_parser.add_argument("--repo-test-scope", choices=("targeted", "all", "e2e"), default="targeted")
    plan_parser.add_argument("--test-filter", choices=("available", "unavailable"), default="available")
    plan_parser.add_argument("--test-cost", choices=("low", "material"), default="low")
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
