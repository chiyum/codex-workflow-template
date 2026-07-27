#!/usr/bin/env python3
"""檢查 Markdown 中指向 repo 內檔案的相對連結。"""
from pathlib import Path
import os
import re
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
link_re = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
bad = []
paths=[]
def onerror(error): raise error
for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False, onerror=onerror):
    directory=Path(dirpath)
    if directory==root and ".git" in dirnames: dirnames.remove(".git")
    paths.extend(directory/name for name in filenames if name.endswith(".md"))
for path in paths:
    text = path.read_text(encoding="utf-8")
    for raw in link_re.findall(text):
        target = raw.strip().split(maxsplit=1)[0].strip("<>")
        if not target or target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        candidate = (path.parent / target.split("#", 1)[0]).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            bad.append((path.relative_to(root), "越界連結"))
            continue
        if not candidate.exists():
            bad.append((path.relative_to(root), "缺少目標"))
for path, reason in bad:
    print(f"{path}: {reason}")
if bad:
    raise SystemExit(1)
print("Markdown 相對連結檢查通過")
