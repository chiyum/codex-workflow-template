#!/usr/bin/env python3
"""清理單一已完成任務的執行期產物，並保留可稽核 manifest。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys


SLUG_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
DONE_STATUSES = {"done", "complete", "completed"}


class CleanupError(RuntimeError):
    pass


def run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="清理單一 status=done 任務的 evidence、state runtime 與同名 worktree；預設只 dry-run。",
    )
    parser.add_argument("slug", help="任務 slug")
    parser.add_argument("--apply", action="store_true", help="實際清理；省略時只列出目標")
    parser.add_argument(
        "--root",
        help="工作流安裝 root；優先於 CODEX_HOME，省略時使用 CODEX_HOME 或腳本安裝根",
    )
    return parser.parse_args()


def resolve_root(explicit: str | None) -> Path:
    raw = explicit or os.environ.get("CODEX_HOME")
    if raw is None:
        raw = str(Path(__file__).resolve().parent.parent)
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise CleanupError("root 必須是絕對路徑")
    try:
        root = candidate.resolve(strict=True)
    except OSError as exc:
        raise CleanupError("root 不存在或不可讀") from exc
    if not root.is_dir() or root == Path(root.anchor):
        raise CleanupError("root 必須是非系統根目錄的既有目錄")
    return root


def relative_to_root(path: Path, root: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError as exc:
        raise CleanupError("目標超出工作流 root") from exc


def assert_safe_existing(path: Path, root: Path, *, directory: bool) -> None:
    relative = relative_to_root(path, root)
    current = root
    for part in relative.parts:
        current /= part
        try:
            mode = os.lstat(current).st_mode
        except OSError as exc:
            raise CleanupError(f"目標不存在或不可讀：{relative.as_posix()}") from exc
        if stat.S_ISLNK(mode):
            raise CleanupError(f"目標路徑不可含 symlink：{relative.as_posix()}")
        if current != path and not stat.S_ISDIR(mode):
            raise CleanupError(f"目標 ancestor 不是目錄：{relative.as_posix()}")
    mode = os.lstat(path).st_mode
    if directory and not stat.S_ISDIR(mode):
        raise CleanupError(f"目標不是目錄：{relative.as_posix()}")
    if not directory and not stat.S_ISREG(mode):
        raise CleanupError(f"目標不是 regular file：{relative.as_posix()}")


def load_done_state(root: Path, slug: str) -> None:
    state_path = root / "state" / f"{slug}.json"
    assert_safe_existing(state_path, root, directory=False)
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CleanupError("state 無法讀取或不是有效 JSON") from exc
    status_value = payload.get("status") if isinstance(payload, dict) else None
    if status_value not in DONE_STATUSES:
        status = status_value if isinstance(status_value, str) else "invalid"
        raise CleanupError(f"任務 status={status}，只有 done/complete/completed 可以清理")


def collect_evidence(root: Path, slug: str) -> list[Path]:
    task_root = root / "acceptance" / slug
    if not task_root.exists():
        return []
    assert_safe_existing(task_root, root, directory=True)
    targets: list[Path] = []
    for current, directories, _files in os.walk(task_root, topdown=True, followlinks=False):
        current_path = Path(current)
        depth = len(current_path.relative_to(task_root).parts)
        if depth > 6:
            raise CleanupError("acceptance evidence 搜尋深度超過 6 層")
        kept: list[str] = []
        for name in directories:
            child = current_path / name
            assert_safe_existing(child, root, directory=True)
            if name == "evidence":
                targets.append(child)
            else:
                kept.append(name)
        directories[:] = kept
    return sorted(targets)


def collect_targets(root: Path, slug: str) -> tuple[list[Path], Path | None, Path | None]:
    evidence = collect_evidence(root, slug)
    runtime = root / "state" / slug / "runtime"
    if runtime.exists():
        assert_safe_existing(runtime, root, directory=True)
    else:
        runtime = None
    worktree = root / "worktrees" / slug
    if worktree.exists():
        assert_safe_existing(worktree, root, directory=True)
    else:
        worktree = None
    return evidence, runtime, worktree


def assert_not_busy(path: Path) -> None:
    executable = shutil.which("lsof")
    if executable is None:
        raise CleanupError("找不到 lsof，無法證明目標未被程序使用；維持保留")
    try:
        completed = subprocess.run(
            [executable, "+D", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CleanupError(f"busy 檢查失敗，維持保留：{path.name}") from exc
    if completed.returncode == 0:
        raise CleanupError(f"目標有程序開啟檔案，維持保留：{path.name}")
    if completed.returncode != 1:
        raise CleanupError(f"busy 檢查結果不明，維持保留：{path.name}")
    # 部分 lsof build 對 +D 只回傳「沒有目錄級命中」，再以具名 regular files
    # 分批重查，避免已開啟的 artifact 被誤判為安全。
    regular_files: list[str] = []
    for current, directories, files in os.walk(path, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in directories:
            child = current_path / name
            if stat.S_ISLNK(os.lstat(child).st_mode):
                raise CleanupError(f"busy 檢查遇到 symlink，維持保留：{path.name}")
        for name in files:
            item = current_path / name
            mode = os.lstat(item).st_mode
            if stat.S_ISLNK(mode):
                raise CleanupError(f"busy 檢查遇到 symlink，維持保留：{path.name}")
            if stat.S_ISREG(mode):
                regular_files.append(str(item))
    for index in range(0, len(regular_files), 64):
        batch = regular_files[index:index + 64]
        try:
            exact = subprocess.run(
                [executable, *batch],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CleanupError(f"busy 檢查失敗，維持保留：{path.name}") from exc
        if exact.returncode == 0:
            raise CleanupError(f"目標有程序開啟檔案，維持保留：{path.name}")
        if exact.returncode != 1:
            raise CleanupError(f"busy 檢查結果不明，維持保留：{path.name}")


def git_worktree_context(path: Path) -> tuple[Path, str]:
    marker = path / ".git"
    if not os.path.lexists(marker):
        raise CleanupError("worktree 缺少可驗證的 .git，維持保留")
    marker_mode = os.lstat(marker).st_mode
    if stat.S_ISLNK(marker_mode):
        raise CleanupError("worktree .git 不可為 symlink")
    inside = run(["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"])
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise CleanupError("worktree Git identity 無法確認，維持保留")
    dirty = run(["git", "-C", str(path), "status", "--porcelain"])
    if dirty.returncode != 0:
        raise CleanupError("worktree dirty 狀態無法確認，維持保留")
    if dirty.stdout:
        raise CleanupError("worktree 有未 commit 變更，維持保留")
    branch = run(["git", "-C", str(path), "branch", "--show-current"])
    if branch.returncode != 0 or not branch.stdout.strip():
        raise CleanupError("worktree 不在具名 branch，維持保留")
    head = run(["git", "-C", str(path), "rev-parse", "HEAD"])
    if head.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", head.stdout.strip()):
        raise CleanupError("worktree HEAD 無法確認，維持保留")
    common = run(["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir"])
    if common.returncode != 0:
        raise CleanupError("worktree common Git dir 無法確認，維持保留")
    common_dir = Path(common.stdout.strip()).resolve()
    main_repo = common_dir.parent if common_dir.name == ".git" else common_dir
    main_ref = run(["git", "-C", str(main_repo), "rev-parse", "--verify", "refs/heads/main"])
    if main_ref.returncode != 0:
        raise CleanupError("worktree repo 沒有可驗證的 main branch，維持保留")
    merged = run([
        "git", "-C", str(main_repo), "merge-base", "--is-ancestor",
        head.stdout.strip(), "refs/heads/main",
    ])
    if merged.returncode == 1:
        raise CleanupError("worktree branch 尚未合進 main，維持保留")
    if merged.returncode != 0:
        raise CleanupError("worktree merge 狀態無法確認，維持保留")
    return main_repo, branch.stdout.strip()


def manifest_text(evidence: Path, root: Path) -> str:
    rows = [
        f"# cleanup {datetime.now(timezone.utc).isoformat()} source={relative_to_root(evidence, root).as_posix()}",
        "# bytes\tmtime_utc\tsha256\tpath",
    ]
    for current, directories, files in os.walk(evidence, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in directories:
            child = current_path / name
            if stat.S_ISLNK(os.lstat(child).st_mode):
                raise CleanupError("evidence 內含 symlink，維持保留")
        for name in sorted(files):
            item = current_path / name
            mode = os.lstat(item).st_mode
            if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
                raise CleanupError("evidence 內含非 regular file，維持保留")
            before = item.stat()
            content = item.read_bytes()
            after = item.stat()
            identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            if identity_before != identity_after:
                raise CleanupError("evidence 在建立 manifest 時改變，維持保留")
            digest = hashlib.sha256(content).hexdigest()
            mtime = datetime.fromtimestamp(before.st_mtime, timezone.utc).isoformat()
            relative = item.relative_to(evidence).as_posix()
            rows.append(f"{before.st_size}\t{mtime}\t{digest}\t{relative}")
    return "\n".join(rows) + "\n"


def assert_safe_output(path: Path, root: Path) -> os.stat_result | None:
    relative_to_root(path, root)
    assert_safe_existing(path.parent, root, directory=True)
    before: os.stat_result | None = None
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        pass
    if before is not None and (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1):
        raise CleanupError(f"輸出不是單一 regular file：{relative_to_root(path, root).as_posix()}")
    return before


def safe_append(path: Path, root: Path, content: str) -> None:
    before = assert_safe_output(path, root)
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = (
        os.O_WRONLY
        | os.O_APPEND
        | os.O_CREAT
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    parent_fd = os.open(path.parent, directory_flags)
    file_fd = -1
    try:
        file_fd = os.open(path.name, file_flags, 0o600, dir_fd=parent_fd)
        opened = os.fstat(file_fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise CleanupError(f"輸出 identity 不安全：{relative_to_root(path, root).as_posix()}")
        if before is not None and (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise CleanupError(f"輸出在開啟前遭替換：{relative_to_root(path, root).as_posix()}")
        encoded = content.encode("utf-8")
        written = 0
        while written < len(encoded):
            written += os.write(file_fd, encoded[written:])
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        os.close(parent_fd)


def append_log(root: Path, message: str) -> None:
    log_path = root / "state" / "cleanup.log"
    timestamp = datetime.now(timezone.utc).isoformat()
    safe_append(log_path, root, f"[{timestamp}] {message}\n")


def remove_worktree(path: Path, context: tuple[Path, str]) -> None:
    main_repo, _branch = context
    removed = run(["git", "-C", str(main_repo), "worktree", "remove", str(path)])
    if removed.returncode != 0:
        raise CleanupError("git worktree remove 失敗，未使用強制刪除")


def main() -> int:
    args = parse_args()
    if SLUG_PATTERN.fullmatch(args.slug) is None or args.slug in {".", ".."}:
        raise CleanupError("slug 只能包含英數、點、底線與連字號，且必須以英數開頭")
    root = resolve_root(args.root)
    load_done_state(root, args.slug)
    evidence, runtime, worktree = collect_targets(root, args.slug)
    targets = [*evidence, *([runtime] if runtime else []), *([worktree] if worktree else [])]
    worktree_context: tuple[Path, str] | None = None
    for target in targets:
        assert_not_busy(target)
    if worktree is not None:
        worktree_context = git_worktree_context(worktree)

    mode = "APPLY" if args.apply else "dry-run"
    print(f"任務 {args.slug}: status=done [{mode}]")
    if not targets:
        print("沒有可清理的目標")
        return 0
    for target in targets:
        print(f"{'將清理' if not args.apply else '清理'} {relative_to_root(target, root).as_posix()}")
    if not args.apply:
        return 0

    if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
        raise CleanupError("目前 Python 平台缺少 symlink-safe rmtree，維持保留")
    manifests = [(target.parent / "evidence-manifest.txt", manifest_text(target, root)) for target in evidence]
    for output, _content in manifests:
        assert_safe_output(output, root)
    assert_safe_output(root / "state" / "cleanup.log", root)
    for output, content in manifests:
        safe_append(output, root, content)
    for target in evidence:
        shutil.rmtree(target)
        append_log(root, f"{args.slug} 刪除 {relative_to_root(target, root).as_posix()}")
    if runtime is not None:
        shutil.rmtree(runtime)
        append_log(root, f"{args.slug} 刪除 {relative_to_root(runtime, root).as_posix()}")
    if worktree is not None:
        if worktree_context is None:
            raise CleanupError("worktree identity 無法確認，維持保留")
        remove_worktree(worktree, worktree_context)
        append_log(root, f"{args.slug} 移除 {relative_to_root(worktree, root).as_posix()}；branch 保留")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CleanupError as exc:
        print(f"cleanup 拒絕：{exc}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        print(f"cleanup 故障：{exc}", file=sys.stderr)
        raise SystemExit(2)
