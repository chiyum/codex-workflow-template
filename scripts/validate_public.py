#!/usr/bin/env python3
"""Scan current Public tree, every committed blob, and Git metadata."""
from __future__ import annotations

import argparse
import base64
import binascii
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import unicodedata
from urllib.parse import unquote


MAX_DECODED_VIEWS = 1024
MAX_BASE64_CHARS = 8 * 1024 * 1024
MAX_DERIVED_TEXT_BYTES = 16 * 1024 * 1024
BASE64_TOKEN = re.compile(r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{8,}={0,2}(?![A-Za-z0-9+/_-])")
BASE64_LINE = re.compile(r"[A-Za-z0-9+/_-]+={0,2}")


def run(command: list[str], *, cwd: Path, text: bool = False) -> bytes | str:
    try:
        completed = subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=text)
    except OSError as exc:
        raise RuntimeError("required command could not start") from exc
    if completed.returncode != 0:
        raise RuntimeError("required command failed")
    return completed.stdout


def load_rules(path: Path) -> list[tuple[str, re.Pattern[str]]]:
    rules = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw or raw.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) != 2:
            raise ValueError(f"invalid public rule row {number}")
        name, encoded = parts
        pattern = base64.b64decode(encoded, validate=True).decode("utf-8")
        rules.append((name, re.compile(pattern, re.IGNORECASE)))
    if not rules:
        raise ValueError("public rule set is empty")
    return rules


def textual(data: bytes) -> str | None:
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def extra_rule(text: str) -> str | None:
    email = re.compile(r"[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})")
    url = re.compile(r"https?://((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})")
    allowed = ("example.com", "example.org", "example.net")
    hosts = [match.lower() for match in email.findall(text)] + [match.lower() for match in url.findall(text)]
    for host in hosts:
        if host not in allowed and not any(host.endswith("." + suffix) for suffix in allowed):
            return "non-example-domain"
    return None


def decoded_views(text: str) -> list[str]:
    """把常見包裝還原後再掃描，避免 encoded value 變成發布繞過。"""
    views: list[str] = []
    seen: set[str] = set()
    derived_text_bytes = 0

    def add(value: str, *, derived: bool = False) -> None:
        nonlocal derived_text_bytes
        normalized = unicodedata.normalize("NFKC", value)
        if normalized in seen:
            return
        if len(views) >= MAX_DECODED_VIEWS:
            raise ValueError("decoded view limit exceeded")
        if derived:
            derived_text_bytes += len(normalized.encode("utf-8"))
            if derived_text_bytes > MAX_DERIVED_TEXT_BYTES:
                raise ValueError("decoded text limit exceeded")
        seen.add(normalized)
        views.append(normalized)

    def base64_candidates(value: str) -> list[str]:
        candidates: list[str] = []
        token_lines: list[str] = []
        block: list[str] = []

        def flush() -> None:
            if len(block) >= 2 and any(len(line) >= 32 for line in block[:-1]):
                candidates.append("".join(block))
            else:
                token_lines.extend(block)
            block.clear()

        for raw_line in value.splitlines():
            line = raw_line.strip()
            if len(line) >= 4 and BASE64_LINE.fullmatch(line):
                block.append(line)
            else:
                flush()
                token_lines.append(raw_line)
        flush()
        candidates.extend(BASE64_TOKEN.findall("\n".join(token_lines)))
        return candidates

    def add_base64_views(value: str) -> None:
        attempted: set[tuple[str, bytes | None]] = set()
        for compact in base64_candidates(value):
            if len(compact) > MAX_BASE64_CHARS:
                raise ValueError("base64 candidate limit exceeded")
            padded = compact + "=" * (-len(compact) % 4)
            for altchars in (None, b"-_"):
                key = (padded, altchars)
                if key in attempted:
                    continue
                attempted.add(key)
                try:
                    data = base64.b64decode(padded, altchars=altchars, validate=True)
                except (ValueError, binascii.Error):
                    continue
                if data:
                    add(data.decode("utf-8", errors="replace"), derived=True)

    add(text)
    offset = 0
    for _ in range(3):
        batch = views[offset:]
        if not batch:
            break
        offset = len(views)
        for value in batch:
            percent = unquote(value)
            if percent != value:
                add(percent, derived=True)
            add_base64_views(value)
    return views


def matching_rule(text: str, rules: list[tuple[str, re.Pattern[str]]]) -> str | None:
    for view in decoded_views(text):
        for name, pattern in rules:
            if pattern.search(view):
                return name
        name = extra_rule(view)
        if name:
            return name
    return None


def scan(label: str, data: bytes, rules: list[tuple[str, re.Pattern[str]]]) -> bool:
    text = textual(data)
    if text is None:
        print(f"Public 脫敏失敗：{label}（規則：binary-text-only）")
        return True
    name = matching_rule(text, rules)
    if name:
        print(f"Public 脫敏失敗：{label}（規則：{name}）")
        return True
    return False


def scan_path(rel: str, rules: list[tuple[str, re.Pattern[str]]], *, revision: str | None = None) -> bool:
    path = PurePosixPath(rel)
    if path.is_absolute() or not rel or path.as_posix() != rel or any(part in ("", ".", "..") for part in path.parts):
        raise RuntimeError("non-canonical repository path")
    try:
        rel.encode("utf-8")
    except UnicodeEncodeError:
        name = "non-utf8-path"
    else:
        name = "path-control-character" if any(unicodedata.category(character) in {"Cc", "Cf"} for character in rel) else matching_rule(rel, rules)
    if name is None:
        return False
    if revision is None:
        print(f"Public 脫敏失敗：<redacted-path>（規則：{name}）")
    else:
        print(f"Public 脫敏失敗：<redacted-path>（commit：{revision}；規則：{name}）")
    return True


def current_tree(root: Path) -> tuple[list[Path], list[str]]:
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
            if path.is_symlink():
                raise RuntimeError("Public tree contains a symlink")
            directories.append(path.relative_to(root).as_posix())
        for name in filenames:
            path = directory / name
            mode = os.lstat(path)
            if path.is_symlink() or not path.is_file() or mode.st_nlink != 1:
                raise RuntimeError("Public tree contains a non-regular file")
            files.append(path)
    return sorted(files), sorted(directories)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve(strict=True)
    expected_script = (root / "scripts/validate_public.py").resolve(strict=True)
    if Path(__file__).resolve(strict=True) != expected_script:
        raise ValueError("validator must execute from the selected root")
    rules = load_rules(root / "policy/public-rules.tsv") + load_rules(root / "policy/secret-rules.tsv")
    failed = False

    current_files, current_directories = current_tree(root)
    for rel in current_directories:
        failed = scan_path(rel, rules) or failed
    for path in current_files:
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise RuntimeError("current file read failed") from exc
        rel = path.relative_to(root).as_posix()
        path_matched = scan_path(rel, rules)
        failed = path_matched or failed
        failed = scan("<redacted-path>" if path_matched else rel, data, rules) or failed

    git_marker = root / ".git"
    if os.path.lexists(git_marker):
        inside = run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root, text=True)
        if inside.strip() != "true":
            raise RuntimeError("invalid Git repository")
        revisions = run(["git", "rev-list", "--all"], cwd=root, text=True).splitlines()
        if not revisions:
            raise RuntimeError("Git repository has no revisions")
        for rev in revisions:
            paths_raw = run(["git", "ls-tree", "-rz", rev], cwd=root)
            for raw in paths_raw.split(b"\x00"):
                if not raw:
                    continue
                try:
                    metadata, path_bytes = raw.split(b"\t", 1)
                    mode, object_type, object_id = metadata.decode("ascii").split(" ", 2)
                    rel = path_bytes.decode("utf-8")
                except (UnicodeDecodeError, UnicodeEncodeError, ValueError) as exc:
                    raise RuntimeError("invalid Git tree entry") from exc
                if mode not in {"100644", "100755"} or object_type != "blob" or not re.fullmatch(r"[0-9a-f]{40,64}", object_id):
                    raise RuntimeError("Git history contains a non-regular entry")
                data = run(["git", "cat-file", "blob", object_id], cwd=root)
                path_matched = scan_path(rel, rules, revision=rev)
                failed = path_matched or failed
                label = f"history:{rev}:<redacted-path>" if path_matched else f"history:{rel}"
                failed = scan(label, data, rules) or failed
            # 格式化欄位會漏掉 gpgsig、encoding 與未知 header；raw commit object 才是
            # 真正會隨 repository 發布的完整 metadata。不可解碼或含 NUL 也一律 fail closed。
            commit_data = run(["git", "cat-file", "commit", rev], cwd=root)
            failed = scan(f"history:commit:{rev}", commit_data, rules) or failed

        tag_refs = run(["git", "for-each-ref", "--format=%(refname)%00%(objecttype)%00%(objectname)", "refs/tags"], cwd=root)
        for raw in tag_refs.splitlines():
            if not raw:
                continue
            try:
                ref_bytes, object_type_bytes, object_id_bytes = raw.split(b"\x00", 2)
                ref_name = ref_bytes.decode("utf-8")
                object_type = object_type_bytes.decode("ascii")
                object_id = object_id_bytes.decode("ascii")
            except (UnicodeDecodeError, ValueError) as exc:
                raise RuntimeError("invalid Git tag metadata") from exc
            failed = scan_path(ref_name, rules, revision="tag-ref") or failed
            if object_type == "tag":
                tag_data = run(["git", "cat-file", "tag", object_id], cwd=root)
                failed = scan("history:tag-metadata", tag_data, rules) or failed
    if failed:
        return 1
    print("Public 脫敏驗證通過（current/history paths + blobs + commit metadata）")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Public 驗證器故障：{type(exc).__name__}", file=sys.stderr)
        raise SystemExit(2)
