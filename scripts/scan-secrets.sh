#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
command -v python3 >/dev/null 2>&1 || { echo "機密掃描器故障：找不到 python3" >&2; exit 2; }
[ "$#" -le 1 ] || { echo "機密掃描器故障：未知參數" >&2; exit 2; }
WITH_HISTORY=0
if [ "$#" -eq 1 ]; then
  [ "$1" = "--history" ] || { echo "機密掃描器故障：未知參數" >&2; exit 2; }
  WITH_HISTORY=1
fi

exec python3 - "$ROOT" "$WITH_HISTORY" <<'PY'
from __future__ import annotations

import base64
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import unicodedata


class ScanFailure(Exception):
    pass


root = Path(sys.argv[1]).resolve(strict=True)
with_history = sys.argv[2] == "1"
git_bin = os.environ.get("GIT_BIN", "git")
if shutil.which(git_bin) is None:
    print("機密掃描器故障：找不到 git", file=sys.stderr)
    raise SystemExit(2)


def text(data: bytes) -> str | None:
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def read_text(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ScanFailure("file read failed") from exc
    decoded = text(data)
    if decoded is None:
        raise ScanFailure("policy file is not UTF-8 text")
    return decoded


def load_secret_rules() -> list[tuple[str, re.Pattern[str]]]:
    result = []
    for number, raw in enumerate(read_text(root / "policy/secret-rules.tsv").splitlines(), 1):
        if not raw or raw.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) != 2:
            raise ScanFailure(f"invalid secret rule row {number}")
        name, encoded = parts
        try:
            pattern = base64.b64decode(encoded, validate=True).decode("utf-8")
            result.append((name, re.compile(pattern, re.IGNORECASE)))
        except Exception as exc:
            raise ScanFailure(f"invalid secret rule row {number}") from exc
    if not result:
        raise ScanFailure("secret rules are empty")
    return result


def load_path_rules() -> list[tuple[str, re.Pattern[str], str]]:
    result = []
    for number, raw in enumerate(read_text(root / "policy/deny-paths.tsv").splitlines(), 1):
        if not raw or raw.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) != 3:
            raise ScanFailure(f"invalid path rule row {number}")
        name, pattern, ignore = parts
        try:
            result.append((name, re.compile(pattern), ignore))
        except re.error as exc:
            raise ScanFailure(f"invalid path rule row {number}") from exc
    if not result:
        raise ScanFailure("path rules are empty")
    return result


def canonical_path(raw: str) -> str:
    value = PurePosixPath(raw)
    if value.is_absolute() or not raw or raw != value.as_posix() or any(part in ("", ".", "..") for part in value.parts):
        raise ScanFailure("non-canonical Git path")
    return raw


def unsafe_path_rule(raw: str) -> str | None:
    try:
        raw.encode("utf-8")
    except UnicodeEncodeError:
        return "non-utf8-path"
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in raw):
        return "path-control-character"
    return None


secret_rules = load_secret_rules()
path_rules = load_path_rules()
failed = False


def scan_content(label: str, data: bytes, *, history: bool = False, metadata: bool = False) -> None:
    global failed
    decoded = text(data)
    if decoded is None:
        scope = "歷史" if history else "目前檔案"
        print(f"{scope}不是純文字：{label}（規則：binary-text-only）")
        failed = True
        return
    for name, pattern in secret_rules:
        if pattern.search(decoded):
            if metadata:
                print(f"歷史疑似機密：commit {label}（規則：{name}）")
            elif history:
                print(f"歷史疑似機密：{label}（規則：{name}）")
            else:
                print(f"疑似機密：{label}（規則：{name}）")
            failed = True


def scan_path(rel: str, *, history: bool = False) -> bool:
    global failed
    canonical_path(rel)
    unsafe = unsafe_path_rule(rel)
    if unsafe is not None:
        prefix = "歷史禁止路徑" if history else "禁止路徑"
        print(f"{prefix}：<redacted-path>（規則：{unsafe}）")
        failed = True
        return True
    for name, pattern, _ in path_rules:
        if pattern.search(rel):
            prefix = "歷史禁止路徑" if history else "禁止路徑"
            print(f"{prefix}：<redacted-path>（規則：{name}）")
            failed = True
            return True
    return False


def current_tree() -> tuple[list[Path], list[str]]:
    files = []
    directories = []
    def onerror(error: OSError) -> None:
        raise error
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False, onerror=onerror):
        directory = Path(dirpath)
        if directory == root and ".git" in dirnames:
            dirnames.remove(".git")
        for name in dirnames:
            path = directory / name
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode):
                raise ScanFailure("symlink directory")
            directories.append(path.relative_to(root).as_posix())
        for name in filenames:
            path = directory / name
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise ScanFailure("non-regular file")
            files.append(path)
    return sorted(files), sorted(directories)


def git(*args: str) -> bytes:
    try:
        completed = subprocess.run([git_bin, *args], cwd=root, check=False, capture_output=True)
    except OSError as exc:
        raise ScanFailure("git could not start") from exc
    if completed.returncode != 0:
        raise ScanFailure("git command failed")
    return completed.stdout


try:
    files, directories = current_tree()
    for rel in directories:
        scan_path(rel)
    for path in files:
        rel = path.relative_to(root).as_posix()
        path_matched = scan_path(rel)
        scan_content("<redacted-path>" if path_matched else rel, path.read_bytes())

    ignores = set(read_text(root / ".gitignore").splitlines())
    for name, _, required in path_rules:
        if required != "-" and required not in ignores:
            print(f".gitignore 缺少 deny-path 規則：{name}")
            failed = True

    if with_history:
        if not os.path.lexists(root / ".git"):
            raise ScanFailure("missing Git repository")
        top = text(git("rev-parse", "--show-toplevel"))
        if top is None or Path(top.strip()).resolve(strict=True) != root:
            raise ScanFailure("invalid Git repository")
        revisions_text = text(git("rev-list", "--all"))
        if revisions_text is None:
            raise ScanFailure("non-text revision list")
        revisions = [line for line in revisions_text.splitlines() if line]
        if not revisions:
            raise ScanFailure("Git repository has no revisions")
        for rev in revisions:
            names = git("ls-tree", "-rz", "--name-only", rev)
            for raw in names.split(b"\x00"):
                if not raw:
                    continue
                try:
                    rel = canonical_path(raw.decode("utf-8"))
                except UnicodeDecodeError as exc:
                    raise ScanFailure("non-UTF8 Git path") from exc
                path_matched = scan_path(rel, history=True)
                scan_content("<redacted-path>" if path_matched else rel, git("show", f"{rev}:{rel}"), history=True)
            # 掃完整 raw commit object，避免格式化輸出漏掉 signature／未知 header。
            commit_data = git("cat-file", "commit", rev)
            scan_content(rev, commit_data, history=True, metadata=True)
except (OSError, ScanFailure) as exc:
    print(f"機密掃描器故障：{type(exc).__name__}", file=sys.stderr)
    raise SystemExit(2)

if failed:
    raise SystemExit(1)
scope = "tree + history" if with_history else "tree"
print(f"機密掃描通過（{scope}）")
PY
