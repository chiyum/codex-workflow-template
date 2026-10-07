#!/usr/bin/env python3
"""retro-digest.py — 自我複盤迴圈的確定性彙整腳本

把「上次複盤之後」的 run 遙測 verdict、新增知識卡／流程提案、新增 ADR 索引成一份 digest，
供 /retro skill 分析用。只讀取彙整，不做任何分析與修改。

用法：
  python3 retro-digest.py            # 輸出 digest markdown 到 stdout
  python3 retro-digest.py --count    # 只印「未複盤且有 verdict 的 run 數」（給任務完成時判斷 >= N 用）
  python3 retro-digest.py --mark     # 複盤完成後呼叫：更新 .last-retro 時間戳

設計原則：
  - 不存計數器，一律從 .last-retro 時間戳推導，避免計數漂移
  - 沒有 verdict 的 run 不計入門檻（問了不追，使用者 補評後自然計入）
"""
import argparse, glob, json, os, sys, time

HOME = os.path.expanduser("~")
RUNS_DIR = os.path.join(HOME, ".codex", "run-metrics", "runs")
KNOWLEDGE_DIR = os.path.join(HOME, ".codex", "knowledge")
MARKER = os.path.join(HOME, ".codex", "run-metrics", ".last-retro")
GOVERNANCE_FILES = frozenset({"INDEX.md", "README.md", "ROUTER.md", "SHARED_CONTEXT_POLICY.md"})
EXCLUDED_DIRS = frozenset({"archive", "playbooks"})


def last_retro_ts():
    """讀上次複盤時間戳；從未複盤過則回 0（全部納入）"""
    try:
        with open(MARKER) as f:
            return float(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 0.0


def pending_runs(since):
    """上次複盤後、且已有 verdict 的 run 記錄（未評分的不計入）"""
    out = []
    for p in sorted(glob.glob(os.path.join(RUNS_DIR, "*.json"))):
        if os.path.getmtime(p) <= since:
            continue
        try:
            run = json.load(open(p))
        except (json.JSONDecodeError, OSError):
            continue
        if run.get("verdict"):
            out.append((p, run))
    return out


def new_knowledge_cards(since):
    """上次複盤後新增/更新的根層知識卡與 harness 提案。"""
    out = []
    candidates = glob.glob(os.path.join(KNOWLEDGE_DIR, "*.md"))
    candidates.extend(glob.glob(os.path.join(KNOWLEDGE_DIR, "harness", "**", "*.md"), recursive=True))
    for p in sorted(set(candidates)):
        relative_path = os.path.relpath(p, KNOWLEDGE_DIR)
        parts = relative_path.split(os.sep)
        if os.path.basename(p) in GOVERNANCE_FILES or EXCLUDED_DIRS.intersection(parts):
            continue
        if os.path.getmtime(p) > since:
            out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", action="store_true", help="只印未複盤 run 數")
    ap.add_argument("--mark", action="store_true", help="更新 .last-retro 時間戳")
    args = ap.parse_args()

    if args.mark:
        with open(MARKER, "w") as f:
            f.write(str(time.time()))
        print("已更新 .last-retro")
        return

    since = last_retro_ts()
    runs = pending_runs(since)

    if args.count:
        print(len(runs))
        return

    # === 輸出 digest markdown ===
    print("# 複盤 digest（確定性彙整，供 /retro 分析）")
    print(f"\n上次複盤：{'從未' if since == 0 else time.strftime('%Y-%m-%d %H:%M', time.localtime(since))}")
    print(f"待複盤 run 數（有 verdict）：{len(runs)}\n")

    print("## Run verdicts")
    for p, run in runs:
        name = os.path.basename(p).replace(".json", "")
        v = run.get("verdict", "?")
        c = run.get("verdict_comment") or ""
        # 摩擦訊號：outcome / notes（run json 實際存在的欄位）
        extra = []
        for key in ("outcome", "notes"):
            if run.get(key):
                extra.append(f"{key}={run[key]}")
        line = f"- **{name}**：{v}" + (f"（{c}）" if c else "")
        if extra:
            line += f" ｜ {' / '.join(extra)}"
        print(line)

    cards = new_knowledge_cards(since)
    print(f"\n## 期間新增/更新的知識卡／流程提案（{len(cards)} 張）")
    for p in cards:
        print(f"- {os.path.relpath(p, KNOWLEDGE_DIR)}")

    print("\n## 提示")
    print("- ADR 散在各 repo docs/adr/，analysis 時針對 digest 中提到的專案抽查即可，不全掃")
    print("- 分析判準：同一種【流程】病在 >=2 個不同任務出現才算 pattern")


if __name__ == "__main__":
    main()
