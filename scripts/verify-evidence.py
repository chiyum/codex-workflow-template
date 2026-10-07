#!/usr/bin/env python3
"""驗證每條 acceptance 的證據與不可替代驗法 receipt。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any


# Acceptance 清單可依產品領域使用 A1、L1、O1 等單字母流水號。
# 保持格式受限，避免把一般章節標題誤判成驗收項目。
HEADING = re.compile(r"^### ([A-Z][0-9]+)\s+", re.MULTILINE)
CONSTRAINT = re.compile(r"^- 不可替代驗法:\s*(.+)$", re.MULTILINE)
ROLES = {"qa", "pm", "verifier"}
FIELDS = {"environment", "viewport", "interaction", "data", "execution_path"}


def parse_constraints(body: str) -> dict[str, str]:
    match = CONSTRAINT.search(body)
    if match is None:
        return {}
    raw = match.group(1).strip()
    # 早期凍結清單以完整自然語言描述不可替代路徑；保留整句作為
    # execution_path 精確契約，避免格式差異繞過 receipt gate。
    if "=" not in raw:
        if not raw:
            raise ValueError("constraint_invalid")
        return {"execution_path": raw}
    result: dict[str, str] = {}
    for part in raw.split(";"):
        key, separator, value = part.strip().partition("=")
        if not separator or key not in FIELDS or not value.strip() or key in result:
            raise ValueError("constraint_invalid")
        result[key] = value.strip()
    if not result:
        raise ValueError("constraint_invalid")
    return result


def acceptance_items(content: str) -> dict[str, dict[str, str]]:
    matches = list(HEADING.finditer(content))
    if not matches:
        raise ValueError("acceptance_items_missing")
    result: dict[str, dict[str, str]] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        identifier = match.group(1)
        if identifier in result:
            raise ValueError("acceptance_duplicate")
        result[identifier] = parse_constraints(content[match.end():end])
    return result


def validate_receipt(path: Path, identifier: str, expected_role: str, expected: dict[str, str]) -> list[str]:
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ["receipt_unreadable"]
    if not isinstance(value, dict):
        return ["receipt_invalid"]
    required = {"schema_version", "acceptance_id", "role", "result", "expected", "actual", "deltas"}
    if set(value) != required or value.get("schema_version") != "evidence-receipt/v1":
        return ["receipt_invalid"]
    if value.get("acceptance_id") != identifier or value.get("role") != expected_role:
        return ["receipt_binding_mismatch"]
    actual = value.get("actual")
    declared_expected = value.get("expected")
    deltas = value.get("deltas")
    if not isinstance(actual, dict) or declared_expected != expected or not isinstance(deltas, list):
        return ["receipt_binding_mismatch"]
    computed = [key for key, expected_value in expected.items() if actual.get(key) != expected_value]
    declared = sorted(str(item) for item in deltas)
    problems: list[str] = []
    if sorted(computed) != declared:
        problems.append("receipt_delta_invalid")
    if computed or value.get("result") != "PASS":
        problems.append("non_substitutable_mismatch")
    return problems


def verify(list_path: Path, profile: str) -> tuple[bool, list[str]]:
    content = list_path.read_text(encoding="utf-8")
    items = acceptance_items(content)
    audit_priority = [
        identifier for identifier, _constraints in sorted(
            items.items(), key=lambda item: (-len(item[1]), int(item[0][1:])),
        )[:2]
    ]
    evidence_dir = list_path.with_suffix("") / "evidence"
    if not evidence_dir.is_dir():
        return False, ["evidence_directory_missing"]
    messages: list[str] = [f"main_audit_priority={','.join(audit_priority)}"]
    passed = True
    required_roles = {"qa", "pm"} if profile == "full" else {"verifier"}
    for identifier, constraints in items.items():
        matches = sorted(path for path in evidence_dir.iterdir() if path.is_file() and f"{identifier}-" in path.name)
        owned: dict[str, list[Path]] = {role: [] for role in ROLES}
        item_problem = False
        for path in matches:
            owner = path.name.split("-", 1)[0]
            if owner not in ROLES:
                passed = False
                item_problem = True
                messages.append(f"{identifier}:unknown_owner_prefix")
                continue
            owned[owner].append(path)
        for role in sorted(required_roles):
            role_receipts = [path for path in owned[role] if path.name.endswith("-receipt.json")]
            role_artifacts = [path for path in owned[role] if path not in role_receipts]
            if not role_artifacts:
                passed = False
                item_problem = True
                messages.append(f"{identifier}:{role}_artifact_missing")
                continue
            if constraints:
                valid_receipt = False
                for receipt in role_receipts:
                    problems = validate_receipt(receipt, identifier, role, constraints)
                    if problems:
                        passed = False
                        item_problem = True
                        messages.extend(f"{identifier}:{role}_{problem}" for problem in problems)
                    else:
                        valid_receipt = True
                if not valid_receipt:
                    passed = False
                    item_problem = True
                    messages.append(f"{identifier}:{role}_receipt_missing")
        if not matches:
            passed = False
            item_problem = True
            messages.append(f"{identifier}:evidence_missing")
        if not item_problem:
            messages.append(f"{identifier}:passed")
    return passed, messages


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("acceptance")
    parser.add_argument("--profile", choices=("full", "standard", "lite"), required=True)
    args = parser.parse_args()
    try:
        passed, messages = verify(Path(args.acceptance).expanduser().resolve(strict=True), args.profile)
    except (OSError, UnicodeError, ValueError):
        print("evidence: blocked reason=validation_error")
        return 1
    for message in messages:
        print(message)
    print("evidence: passed" if passed else "evidence: blocked")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
