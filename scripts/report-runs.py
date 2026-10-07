#!/usr/bin/env python3
"""輸出 metrics v3 趨勢與 A/B 報表，並相容讀取 v1/v2 run。"""
from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path
import sys
from typing import Any


def fmt(value: float | int) -> str:
    return f"{value / 1000:.1f}k" if value >= 10_000 else str(round(value, 1))


def domain_exact(run: dict[str, Any], domain: str) -> bool:
    if run.get("schema_version") == 3:
        return ((run.get("availability") or {}).get(domain) or {}).get("status") == "exact"
    if run.get("schema_version") == 2:
        return run.get("collector_status") in {"ok", "unsupported_token_attribution"}
    return False


def exact_tokens(run: dict[str, Any]) -> bool:
    return (
        domain_exact(run, "tokens")
        and ((run.get("token_attribution") or {}).get("status") == "exact_single_transcript")
    )


def token_display(run: dict[str, Any], key: str) -> str:
    value = token_value(run, key)
    return fmt(value) if value is not None else "—"


def token_value(run: dict[str, Any], key: str) -> int | None:
    if not exact_tokens(run):
        return None
    value = ((run.get("tokens") or {}).get("total") or {}).get(key)
    return int(value) if isinstance(value, (int, float)) else None


def calls(run: dict[str, Any]) -> int | None:
    if not domain_exact(run, "agents"):
        return None
    explicit = run.get("agent_spawn_total")
    if isinstance(explicit, (int, float)):
        return int(explicit)
    values = run.get("agent_calls")
    if isinstance(values, dict) and all(isinstance(value, (int, float)) for value in values.values()):
        return int(sum(values.values()))
    return None


def reuse_count(run: dict[str, Any]) -> int | None:
    if not domain_exact(run, "reuse"):
        return None
    value = (run.get("reuse") or {}).get("follow_up_events")
    return int(value) if isinstance(value, (int, float)) else None


def repair_count(run: dict[str, Any]) -> int | None:
    value = run.get("repair_count")
    return int(value) if isinstance(value, (int, float)) else None


def metric_display(value: int | None) -> str:
    return "—" if value is None else str(value)


def verdict_summary(runs: list[dict[str, Any]]) -> str:
    result: dict[str, int] = {}
    for run in runs:
        key = run.get("verdict") or "未評"
        result[key] = result.get(key, 0) + 1
    return " ".join(f"{key}×{value}" for key, value in sorted(result.items()))


def rate(runs: list[dict[str, Any]], predicate) -> str:
    return f"{100 * sum(1 for run in runs if predicate(run)) / len(runs):.0f}%" if runs else "—"


def available_average(runs: list[dict[str, Any]], getter) -> str:
    values = [value for run in runs if (value := getter(run)) is not None]
    return f"{fmt(sum(values) / len(values))}(n={len(values)})" if values else "—(n=0)"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--last", type=int, default=20)
    parser.add_argument("--md", default="")
    parser.add_argument("--codex-home", default="")
    parser.add_argument("--runs-dir", default="")
    args = parser.parse_args()
    codex_home = Path(args.codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    runs_dir = Path(args.runs_dir).expanduser() if args.runs_dir else codex_home / "run-metrics" / "runs"
    runs: list[dict[str, Any]] = []
    for path in sorted(glob.glob(str(runs_dir / "*.json")), reverse=True)[: args.last]:
        try:
            runs.append(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[warn] 讀取失敗 {path}: {exc}", file=sys.stderr)
    if not runs:
        print("尚無 run 記錄")
        return 0

    lines = [
        "## 逐筆 run 對照（新 → 舊）",
        "",
        "| 日期 | 任務 | schema/status | lane | out | reasoning | cached | agents/reuse | 結果 | verdict |",
        "|---|---|---|---|---:|---:|---:|---:|---|---|",
    ]
    for run in runs:
        status = run.get("collector_status") or ("legacy_unverified" if run.get("tokens") else "unsupported_schema")
        attribution = ((run.get("token_attribution") or {}).get("status") or "unverified_token_attribution")
        lines.append(
            f"| {run.get('date', '?')} | {run.get('slug', '?')} | v{run.get('schema_version', 1)}/{status}/{attribution} | "
            f"{run.get('risk_lane', 'unknown')} | {token_display(run, 'output_tokens')} | "
            f"{token_display(run, 'reasoning_output_tokens')} | {token_display(run, 'cached_input_tokens')} | "
            f"{metric_display(calls(run))}/{metric_display(reuse_count(run))} | "
            f"{run.get('outcome', '?')} | {run.get('verdict') or '未評'} |"
        )

    valid = [run for run in runs if exact_tokens(run)]
    excluded = len(runs) - len(valid)
    reportable = [run for run in runs if run.get("schema_version") in {2, 3}]
    groups: dict[str, list[dict[str, Any]]] = {}
    for run in reportable:
        groups.setdefault(run.get("config_commit", "unknown"), []).append(run)
    lines += [
        "",
        f"## 按 config 版本分組（token 排除 {excluded} 筆 unavailable run）",
        "",
        "> token/agents/reuse/repair 各自排除 unavailable，括號 n 為該欄實際 denominator。",
        "",
        "| config | runs | 平均 input/cached/out/reasoning | 平均 agents/reuse/repair | 失敗率 | 驗收失敗率 | verdict |",
        "|---|---:|---|---|---:|---:|---|",
    ]
    for config, group in groups.items():
        token_text = "/".join(
            available_average(group, lambda run, key=key: token_value(run, key))
            for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
        )
        process_text = "/".join((
            available_average(group, calls),
            available_average(group, reuse_count),
            available_average(group, repair_count),
        ))
        lines.append(
            f"| `{config}` | {len(group)} | {token_text} | {process_text} | "
            f"{rate(group, lambda run: run.get('outcome') != 'done')} | "
            f"{rate(group, lambda run: run.get('acceptance_result') == 'fail')} | {verdict_summary(group)} |"
        )

    experiments: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for run in reportable:
        experiment = run.get("experiment") or {}
        name, variant = experiment.get("name"), experiment.get("variant")
        if name and name != "unknown" and variant in {"before", "after"}:
            experiments.setdefault((name, variant), []).append(run)
    if experiments:
        lines += [
            "",
            "## A/B（每個 variant 至少 5 筆才可下結論）",
            "",
            "| experiment | variant | n | 平均 total/out/reasoning | 平均 agents/reuse/repair | 失敗率 | 驗收失敗率 | verdict | 可判讀 |",
            "|---|---|---:|---|---|---:|---:|---|---|",
        ]
        for (name, variant), group in sorted(experiments.items()):
            exact_group = [run for run in group if exact_tokens(run)]
            tokens = "/".join(
                available_average(group, lambda run, key=key: token_value(run, key))
                for key in ("input_tokens", "output_tokens", "reasoning_output_tokens")
            )
            process = "/".join((
                available_average(group, calls),
                available_average(group, reuse_count),
                available_average(group, repair_count),
            ))
            lines.append(
                f"| {name} | {variant} | {len(group)} | {tokens} | {process} | "
                f"{rate(group, lambda run: run.get('outcome') != 'done')} | "
                f"{rate(group, lambda run: run.get('acceptance_result') == 'fail')} | "
                f"{verdict_summary(group)} | {'是' if len(exact_group) >= 5 else '否'} |"
            )

    output = "\n".join(lines) + "\n"
    print(output, end="")
    if args.md:
        Path(args.md).write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
