#!/usr/bin/env python3
"""Deterministic repository and installed-target validation."""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import py_compile
import re
import stat
import subprocess
import sys
import tomllib

sys.dont_write_bytecode = True


def load_installer(root: Path):
    path = root / "scripts/install_impl.py"
    spec = importlib.util.spec_from_file_location("workflow_install_impl", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load installer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inventory(root: Path) -> set[str]:
    result = set()
    def onerror(error: OSError) -> None:
        raise error
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False, onerror=onerror):
        directory = Path(dirpath)
        if directory == root and ".git" in dirnames:
            dirnames.remove(".git")
        for name in list(dirnames):
            path = directory / name
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode):
                raise ValueError(f"repository contains symlink: {path.relative_to(root)}")
        for name in filenames:
            path = directory / name
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise ValueError(f"repository contains non-regular file: {path.relative_to(root)}")
            result.add(path.relative_to(root).as_posix())
    return result


def manifest_sources(root: Path) -> tuple[set[str], set[str]]:
    sources = set()
    destinations = set()
    for number, raw in enumerate((root / "manifest.tsv").read_text(encoding="utf-8").splitlines(), 1):
        if not raw or raw.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) != 3:
            raise ValueError(f"manifest row {number} must have three fields")
        kind, source, destination = parts
        if source in sources:
            raise ValueError("duplicate manifest source")
        sources.add(source)
        if kind == "install":
            if destination in destinations:
                raise ValueError("duplicate manifest destination")
            destinations.add(destination)
        elif kind != "support" or destination != "-":
            raise ValueError("invalid manifest row")
    return sources, destinations


def assert_memories(path: Path) -> None:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if data.get("features", {}).get("memories") is not True:
        raise ValueError(f"Memories is not enabled in {path.name}")
    if path.read_text(encoding="utf-8").count("[features]") != 1:
        raise ValueError(f"features table is not unique in {path.name}")


def validate_workflow_contracts(root: Path) -> None:
    reviewer = (root / "agent-guides/reviewer.md").read_text(encoding="utf-8")
    modules = (root / "agent-guides/reviewer-modules.md").read_text(encoding="utf-8")
    module_names = re.findall(r"^## ([a-z0-9-]+)$", modules, flags=re.MULTILINE)
    if not module_names or "reviewer-modules.md" not in reviewer:
        raise ValueError("reviewer module router is missing")
    orphaned = [name for name in module_names if f"`{name}`" not in reviewer]
    if orphaned:
        raise ValueError(f"reviewer modules are orphaned: {orphaned}")

    dev_skill = (root / "skills/dev/SKILL.md").read_text(encoding="utf-8")
    required_fragments = (
        "workflow-profile.py plan",
        "L1 由主 Codex",
        "L2/L3 才由 PM",
        "L1 由主 Codex audit QA evidence",
    )
    if any(fragment not in dev_skill for fragment in required_fragments):
        raise ValueError("dev skill does not follow lane-based plan ownership")


def validate_installed(root: Path, target: Path) -> None:
    _, expected = manifest_sources(root)
    actual = inventory(target)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"installed inventory mismatch; missing={missing[:3]} extra={extra[:3]}")
    assert_memories(target / "config.toml")
    validate_workflow_contracts(target)
    for required in (
        "AGENTS.md",
        "manifest.tsv",
        "workflow-source.toml",
        "workflows/development-workflow.md",
        "workflows/effort-routing.md",
        "workflows/rapid-change-workflow.md",
        "agents/architect.toml",
        "scripts/aiuse",
        "scripts/collect-run-metrics.py",
        "scripts/workflow-profile.py",
    ):
        if required not in actual:
            raise ValueError(f"installed target missing {required}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--installed")
    parser.add_argument("--list-shell")
    parser.add_argument("--list-mjs")
    args = parser.parse_args()
    root = Path(args.root).resolve(strict=True)
    installer = load_installer(root)
    installer.parse_manifest(root)

    if args.installed:
        target = Path(args.installed).resolve(strict=True)
        validate_installed(root, target)
        print(f"安裝 smoke test 通過：{target}")
        return 0

    required = {
        "README.md", "manifest.tsv", ".gitignore", "config.example.toml", "scripts/install.sh",
        "scripts/install_impl.py", "scripts/validate.sh", "scripts/scan-secrets.sh", "scripts/policy_check.py",
        "policy/deny-paths.tsv", "policy/secret-rules.tsv", "AGENTS.md", "workflows/development-workflow.md",
        "agents/architect.toml", "agent-guides/architect.md", "products/INDEX.md", "knowledge/INDEX.md",
        "acceptance/README.md", "state/README.md", "run-metrics/README.md",
        "workflow-source.toml", "workflows/effort-routing.md", "workflows/rapid-change-workflow.md",
        "scripts/aiuse", "scripts/collect-run-metrics.py", "scripts/workflow-profile.py",
        "scripts/test-aiuse-profile.py", "scripts/test-run-metrics.py", "scripts/test-workflow-profile.py",
    }
    actual = inventory(root)
    sources, _ = manifest_sources(root)
    if actual != sources:
        missing = sorted(sources - actual)
        extra = sorted(actual - sources)
        raise ValueError(f"manifest inventory mismatch; missing={missing[:3]} extra={extra[:3]}")
    if not required.issubset(actual):
        raise ValueError(f"required files missing: {sorted(required-actual)}")

    for rel in actual:
        path = root / rel
        if path.suffix == ".toml":
            with path.open("rb") as handle:
                tomllib.load(handle)
    assert_memories(root / "config.example.toml")
    validate_workflow_contracts(root)
    for rel in actual:
        path = root / rel
        if path.suffix == ".py":
            source = path.read_text(encoding="utf-8")
            compile(source, str(path), "exec")

    if os.path.lexists(root / ".git"):
        completed = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=False, capture_output=True)
        if completed.returncode != 0:
            raise RuntimeError("git ls-files failed")
        tracked = {item.decode("utf-8") for item in completed.stdout.split(b"\x00") if item}
        if tracked != sources:
            raise ValueError("tracked files do not match manifest")

    if args.list_shell:
        Path(args.list_shell).write_text("\n".join(sorted(p for p in actual if p.endswith(".sh"))) + "\n", encoding="utf-8")
    if args.list_mjs:
        Path(args.list_mjs).write_text("\n".join(sorted(p for p in actual if p.endswith(".mjs"))) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"repo 驗證故障：{exc}", file=sys.stderr)
        raise SystemExit(1)
