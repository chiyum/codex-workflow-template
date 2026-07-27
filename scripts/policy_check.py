#!/usr/bin/env python3
"""Validate repository paths against the shared deny-path policy."""
from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import re
import sys


def load_rules(path: Path) -> list[tuple[str, re.Pattern[str], str]]:
    rules = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw or raw.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) != 3:
            raise ValueError(f"invalid deny-path row {number}")
        name, pattern, ignore_entry = parts
        rules.append((name, re.compile(pattern), ignore_entry))
    if not rules:
        raise ValueError("deny-path policy is empty")
    return rules


def normalize(raw: str) -> str:
    path = PurePosixPath(raw)
    if path.is_absolute() or not raw or raw != path.as_posix() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("non-canonical repository path")
    return raw


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--paths-file", required=True)
    parser.add_argument("--check-gitignore", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve(strict=True)
    rules = load_rules(root / "policy/deny-paths.tsv")
    failed = False
    for raw in Path(args.paths_file).read_text(encoding="utf-8").splitlines():
        try:
            rel = normalize(raw)
        except ValueError:
            print("禁止路徑：<invalid-path>（規則：canonical-path）")
            failed = True
            continue
        for name, pattern, _ in rules:
            if pattern.search(rel):
                print(f"禁止路徑：{rel}（規則：{name}）")
                failed = True
    if args.check_gitignore:
        entries = set((root / ".gitignore").read_text(encoding="utf-8").splitlines())
        for name, _, required in rules:
            if required != "-" and required not in entries:
                print(f".gitignore 缺少 deny-path 規則：{name}")
                failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"路徑政策檢查故障：{type(exc).__name__}", file=sys.stderr)
        raise SystemExit(2)

