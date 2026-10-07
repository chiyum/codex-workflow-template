#!/usr/bin/env python3
"""在 writer selection 前產生 fresh 產品／repo／部署基準收據。

產品配置傳入 repo／remote／account／env／version routing；repo Git 與規格仍是行為真相。
本工具不代替使用者凍結；只要是 code 任務，輸出一律保持 writer_selection_blocked。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

# 匯入同目錄模組時不寫 __pycache__，避免在制度 repo 與安裝目標留下 binary 產物。
sys.dont_write_bytecode = True
from remote_url import sanitize_remote_url  # noqa: E402


SHA = re.compile(r"^[0-9a-f]{40,64}$")
PRODUCT_ROUTE_FIELDS = {"source", "account", "environment", "version_routing", "release_policy"}
SCRIPT_PATH = Path(__file__).resolve()


def git(repo: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, timeout=20,
    )
    if check and completed.returncode != 0:
        raise ValueError("git_query_failed")
    return completed.stdout.strip()


def status(repo: Path) -> tuple[list[str], list[str], list[str]]:
    raw = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=20,
    ).stdout
    tracked: list[str] = []
    untracked: list[str] = []
    entries = [item for item in raw.split(b"\0") if item]
    index = 0
    while index < len(entries):
        item = entries[index]
        if len(item) < 4:
            raise ValueError("git_status_invalid")
        code = item[:2].decode("ascii")
        path = item[3:].decode("utf-8", "surrogateescape")
        if code == "??":
            untracked.append(path)
        else:
            tracked.append(path)
        if "R" in code or "C" in code:
            index += 1
        index += 1
    return sorted(set(tracked)), sorted(set(untracked)), sorted(set(tracked + untracked))


def remote_head(repo: Path, remote: str, branch: str) -> tuple[str | None, str | None]:
    completed = subprocess.run(
        ["git", "-C", str(repo), "ls-remote", remote, f"refs/heads/{branch}"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=20,
    )
    if completed.returncode != 0:
        return None, "remote_query_failed"
    rows = [line.split() for line in completed.stdout.splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) != 2 or not SHA.fullmatch(rows[0][0]):
        return None, "remote_branch_unavailable"
    return rows[0][0], None


def deployed(value: Any, now: str) -> dict[str, str | None]:
    if not isinstance(value, dict) or set(value) - {"version", "queried_at", "unavailable_reason"}:
        raise ValueError("deployed_observation_invalid")
    version = value.get("version")
    reason = value.get("unavailable_reason")
    queried_at = value.get("queried_at") or now
    if not isinstance(queried_at, str) or not queried_at.strip():
        raise ValueError("deployed_observation_invalid")
    if version is not None and (not isinstance(version, str) or not version.strip() or reason is not None):
        raise ValueError("deployed_observation_invalid")
    if version is None and (not isinstance(reason, str) or not reason.strip()):
        raise ValueError("deployed_observation_invalid")
    return {"version": version, "queried_at": queried_at, "unavailable_reason": reason}


def resolve_baseline(repo: Path, local_head: str, remote: str | None, proposed: str) -> str:
    if proposed == "local_head":
        baseline = local_head
    elif proposed == "remote_head":
        if remote is None:
            raise ValueError("proposed_remote_baseline_unavailable")
        baseline = remote
    elif SHA.fullmatch(proposed):
        baseline = proposed
    else:
        raise ValueError("proposed_baseline_invalid")
    completed = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{baseline}^{{commit}}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=20,
    )
    if completed.returncode != 0:
        raise ValueError("proposed_baseline_commit_missing")
    return baseline


def receipt_digest(receipt: dict[str, Any]) -> str:
    canonical = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def collect(order: dict[str, Any]) -> dict[str, Any]:
    required = {"product", "target_environment", "product_route", "repos"}
    if not required <= set(order) or order.get("target_environment") not in {"local", "dev", "prod"}:
        raise ValueError("baseline_order_invalid")
    product_route = order.get("product_route")
    repos = order.get("repos")
    if (
        not isinstance(product_route, dict)
        or not PRODUCT_ROUTE_FIELDS <= set(product_route)
        or any(not isinstance(product_route[field], str) or not product_route[field].strip() for field in PRODUCT_ROUTE_FIELDS)
        or not isinstance(repos, list) or not repos
    ):
        raise ValueError("baseline_order_invalid")
    conflicts = order.get("authority_conflicts", [])
    if not isinstance(conflicts, list):
        raise ValueError("baseline_order_invalid")
    normalized_conflicts: list[dict[str, str]] = []
    for conflict in conflicts:
        required_conflict = {"subject", "product_route_value", "repo_behavior_value"}
        if (
            not isinstance(conflict, dict) or set(conflict) != required_conflict
            or any(not isinstance(conflict[field], str) or not conflict[field].strip() for field in required_conflict)
        ):
            raise ValueError("authority_conflict_invalid")
        normalized_conflicts.append({field: conflict[field] for field in sorted(required_conflict)})
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    observations: list[dict[str, Any]] = []
    differences: list[dict[str, str]] = []
    for item in repos:
        if not isinstance(item, dict):
            raise ValueError("repo_route_invalid")
        path = Path(str(item.get("path", ""))).expanduser().resolve(strict=True)
        root = Path(git(path, "rev-parse", "--show-toplevel")).resolve(strict=True)
        if root != path:
            raise ValueError("repo_route_invalid")
        branch = git(path, "branch", "--show-current") or "DETACHED"
        local_head = git(path, "rev-parse", "HEAD")
        remote_name = str(item.get("remote", "origin"))
        remote_raw = git(path, "remote", "get-url", remote_name)
        remote_url, remote_url_unavailable = sanitize_remote_url(remote_raw)
        head, unavailable = remote_head(path, remote_name, branch) if branch != "DETACHED" else (None, "detached_head")
        tracked, untracked, dirty = status(path)
        proposed = str(item.get("proposed_baseline", ""))
        baseline = resolve_baseline(path, local_head, head, proposed)
        baseline_tree = git(path, "rev-parse", f"{baseline}^{{tree}}")
        deployment = deployed(item.get("deployed"), now)
        behavior_sources = item.get("behavior_sources")
        behavior_summary = item.get("behavior_summary")
        if (
            not isinstance(behavior_sources, list) or not behavior_sources
            or not isinstance(behavior_summary, str) or not behavior_summary.strip()
        ):
            raise ValueError("repo_behavior_authority_invalid")
        normalized_sources: list[dict[str, str]] = []
        for source in behavior_sources:
            candidate = Path(str(source))
            relative = candidate.as_posix()
            if candidate.is_absolute() or ".." in candidate.parts or relative in {"", "."}:
                raise ValueError("repo_behavior_authority_invalid")
            completed = subprocess.run(
                ["git", "-C", str(path), "cat-file", "-e", f"{baseline}:{relative}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=20,
            )
            if completed.returncode != 0:
                raise ValueError("repo_behavior_authority_invalid")
            normalized_sources.append({"path": relative, "blob": git(path, "rev-parse", f"{baseline}:{relative}")})
        if dirty:
            differences.append({"repo": str(path), "kind": "dirty_worktree"})
        if head is not None and local_head != head:
            differences.append({"repo": str(path), "kind": "local_remote_head_mismatch"})
        if deployment["version"] is not None and deployment["version"] != baseline:
            differences.append({"repo": str(path), "kind": "baseline_deployed_version_mismatch"})
        observation = {
            "path": str(path), "branch": branch, "local_head": local_head,
            "dirty": bool(dirty), "dirty_paths": dirty, "tracked_dirty_paths": tracked,
            "untracked_paths": untracked, "remote": remote_name, "remote_url": remote_url,
            "remote_url_unavailable_reason": remote_url_unavailable,
            "remote_head": head, "remote_head_unavailable_reason": unavailable,
            "proposed_baseline": baseline, "proposed_baseline_source": proposed,
            "proposed_baseline_tree": baseline_tree,
            "deployed": deployment,
            "behavior_authority": {
                "sources": sorted(normalized_sources, key=lambda item: item["path"]),
                "summary": behavior_summary.strip(),
            },
        }
        identity_fields = {
            key: observation[key]
            for key in (
                "path", "branch", "local_head", "remote", "remote_head",
                "proposed_baseline", "proposed_baseline_tree",
            )
        }
        observation["repo_identity_sha256"] = receipt_digest(identity_fields)
        observations.append(observation)
    differences.extend({"repo": "product_vs_repo", "kind": "authority_conflict"} for _ in normalized_conflicts)
    receipt = {
        "schema_version": "development-baseline/v2",
        "product": order["product"],
        "target_environment": order["target_environment"],
        "queried_at": now,
        "authority": {
            "routing": "product_config",
            "behavior": "selected_baseline_repo_code_spec_adr",
            "acceptance_gate": "frozen_acceptance",
            "product_route": product_route,
        },
        "repos": observations,
        "differences": differences,
        "authority_conflicts": normalized_conflicts,
        "requires_user_confirmation": True,
        "writer_selection_blocked": True,
        "collector_identity": {
            "path": "scripts/development-baseline.py",
            "sha256": hashlib.sha256(SCRIPT_PATH.read_bytes()).hexdigest(),
        },
    }
    receipt["receipt_sha256"] = receipt_digest(receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("collect", nargs="?")
    parser.parse_args()
    try:
        order = json.load(sys.stdin)
        if not isinstance(order, dict):
            raise ValueError("baseline_order_invalid")
        print(json.dumps(collect(order), ensure_ascii=False, sort_keys=True))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
        print(json.dumps({"status": "blocked", "reason": "baseline_collection_failed"}, sort_keys=True))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
