#!/usr/bin/env python3
"""rate-run.py — 把 使用者 的一行驗收評分寫回 run 記錄

用法：python3 rate-run.py --slug <任務slug> --verdict good|ok|bad [--comment "一句話"]
slug 支援部分比對（取最新一筆命中）。
"""
import argparse, glob, json, os, sys

RUNS_DIR = os.path.join(os.path.expanduser("~"), ".codex", "run-metrics", "runs")

ap = argparse.ArgumentParser()
ap.add_argument("--slug", required=True)
ap.add_argument("--verdict", required=True, choices=["good", "ok", "bad"])
ap.add_argument("--comment", default="")
args = ap.parse_args()

hits = sorted(g for g in glob.glob(os.path.join(RUNS_DIR, "*.json")) if args.slug in os.path.basename(g))
if not hits:
    print(f"找不到含「{args.slug}」的 run 記錄", file=sys.stderr)
    sys.exit(1)
path = hits[-1]
run = json.load(open(path))
run["verdict"] = args.verdict
run["verdict_comment"] = args.comment or run.get("verdict_comment")
with open(path, "w", encoding="utf-8") as f:
    json.dump(run, f, ensure_ascii=False, indent=2)
print(f"已寫入 {os.path.basename(path)}：verdict={args.verdict}" + (f"（{args.comment}）" if args.comment else ""))
