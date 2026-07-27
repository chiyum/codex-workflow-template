#!/usr/bin/env python3
"""Fail-safe installer for a manifest-declared Codex home."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import time
import tomllib
import uuid

LEGACY_RELEASES_PATH = PurePosixPath("policy/legacy-releases.tsv")


def canonical_relative(raw: str, label: str) -> PurePosixPath:
    path = PurePosixPath(raw)
    if path.is_absolute() or not raw or raw != path.as_posix() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"{label} 不是正規相對路徑")
    return path


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def reject_symlink_ancestors(raw_absolute: Path) -> None:
    current = Path(raw_absolute.anchor)
    for part in raw_absolute.parts[1:]:
        current /= part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ValueError("target 或其 ancestor 不得是 symlink")


def read_regular_nofollow(path: Path, label: str) -> bytes:
    """以 descriptor identity 驗證並讀取 regular file，避免 lstat/open 間換檔。"""
    before = os.lstat(path)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError(f"{label} 不是安全 regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        before_identity = (before.st_dev, before.st_ino, stat.S_IFMT(before.st_mode), before.st_nlink)
        opened_identity = (opened.st_dev, opened.st_ino, stat.S_IFMT(opened.st_mode), opened.st_nlink)
        if before_identity != opened_identity or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise ValueError(f"{label} 在讀取時變更")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def parse_manifest(root: Path) -> list[tuple[Path, PurePosixPath]]:
    rows: list[tuple[Path, PurePosixPath]] = []
    seen_sources: set[str] = set()
    seen_destinations: set[str] = set()
    manifest = root / "manifest.tsv"
    for number, raw in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not raw or raw.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) != 3:
            raise ValueError(f"manifest 第 {number} 列欄位數錯誤")
        kind, source_raw, destination_raw = parts
        if kind not in {"install", "support"}:
            raise ValueError(f"manifest 第 {number} 列 kind 錯誤")
        source_rel = canonical_relative(source_raw, "source")
        if source_raw in seen_sources:
            raise ValueError("manifest source 重複")
        seen_sources.add(source_raw)
        source = root.joinpath(*source_rel.parts)
        source_mode = os.lstat(source).st_mode
        if not stat.S_ISREG(source_mode) or stat.S_ISLNK(source_mode):
            raise ValueError(f"manifest source 不是 regular file：{source_raw}")
        resolved_source = source.resolve(strict=True)
        if not is_within(resolved_source, root) or resolved_source != source:
            raise ValueError(f"manifest source 越界或經 symlink：{source_raw}")
        if kind == "support":
            if destination_raw != "-":
                raise ValueError("support row destination 必須是 -")
            continue
        destination = canonical_relative(destination_raw, "destination")
        if destination_raw in seen_destinations:
            raise ValueError("manifest destination 重複")
        seen_destinations.add(destination_raw)
        rows.append((source, destination))
    if not rows:
        raise ValueError("manifest 沒有 install entries")
    return rows


def validate_staging(stage: Path, rows: list[tuple[Path, PurePosixPath]]) -> None:
    for _, destination in rows:
        path = stage.joinpath(*destination.parts)
        mode = os.lstat(path).st_mode
        if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
            raise ValueError(f"staging 不是 regular file：{destination}")
        if not is_within(path.resolve(strict=True), stage):
            raise ValueError(f"staging 越界：{destination}")
    config = stage / "config.toml"
    with config.open("rb") as handle:
        parsed = tomllib.load(handle)
    if parsed.get("features", {}).get("memories") is not True:
        raise ValueError("安裝後 config.toml 未啟用 Memories")


def parse_installed_manifest(target: Path) -> set[PurePosixPath]:
    manifest = target / "manifest.tsv"
    manifest_data = read_regular_nofollow(manifest, "installed manifest")
    if manifest.resolve(strict=True) != manifest:
        raise ValueError("installed manifest 不是安全 regular file")
    destinations: set[PurePosixPath] = set()
    seen_sources: set[str] = set()
    provenance_rows = 0
    for number, raw in enumerate(manifest_data.decode("utf-8").splitlines(), 1):
        if not raw or raw.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) != 3:
            raise ValueError(f"installed manifest 第 {number} 列欄位數錯誤")
        kind, source_raw, destination_raw = parts
        if kind not in {"install", "support"}:
            raise ValueError(f"installed manifest 第 {number} 列 kind 錯誤")
        canonical_relative(source_raw, "installed source")
        if source_raw == "manifest.tsv" or destination_raw == "manifest.tsv":
            # PROVENANCE_SOURCE_GUARD_BEGIN
            if destination_raw == "manifest.tsv" and source_raw != "manifest.tsv":
                raise ValueError("installed manifest provenance source 不精確")
            # PROVENANCE_SOURCE_GUARD_END
            # PROVENANCE_DESTINATION_GUARD_BEGIN
            if source_raw == "manifest.tsv" and destination_raw != "manifest.tsv":
                raise ValueError("installed manifest provenance destination 不精確")
            # PROVENANCE_DESTINATION_GUARD_END
            # PROVENANCE_KIND_GUARD_BEGIN
            if kind != "install":
                raise ValueError("installed manifest provenance kind 不精確")
            # PROVENANCE_KIND_GUARD_END
            provenance_rows += 1
            # PROVENANCE_DUPLICATE_GUARD_BEGIN
            if provenance_rows > 1:
                raise ValueError("installed manifest provenance row 重複")
            # PROVENANCE_DUPLICATE_GUARD_END
            # 通過專屬守衛後只記錄 canonical provenance，避免通用守衛代為攔截突變。
            destinations.add(PurePosixPath("manifest.tsv"))
            continue
        if source_raw in seen_sources:
            raise ValueError("installed manifest source 重複")
        seen_sources.add(source_raw)
        if kind == "support":
            if destination_raw != "-":
                raise ValueError("installed support row destination 必須是 -")
            continue
        destination = canonical_relative(destination_raw, "installed destination")
        if destination in destinations:
            raise ValueError("installed manifest destination 重複")
        destinations.add(destination)
    if provenance_rows == 0:
        raise ValueError("installed manifest 缺少 provenance row")
    return destinations


def expected_directories(destinations: set[PurePosixPath]) -> set[PurePosixPath]:
    result: set[PurePosixPath] = set()
    for destination in destinations:
        parent = destination.parent
        while parent != PurePosixPath("."):
            result.add(parent)
            parent = parent.parent
    return result


def installed_inventory(target: Path) -> tuple[set[PurePosixPath], set[PurePosixPath]]:
    files: set[PurePosixPath] = set()
    directories: set[PurePosixPath] = set()
    def onerror(error: OSError) -> None:
        raise error
    for dirpath, dirnames, filenames in os.walk(target, topdown=True, followlinks=False, onerror=onerror):
        directory = Path(dirpath)
        for name in dirnames:
            path = directory / name
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode) or path.resolve(strict=True) != path:
                raise ValueError("legacy target 含不安全目錄")
            directories.add(PurePosixPath(path.relative_to(target).as_posix()))
        for name in filenames:
            path = directory / name
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or path.resolve(strict=True) != path:
                raise ValueError("legacy target 含不安全檔案")
            files.add(PurePosixPath(path.relative_to(target).as_posix()))
    return files, directories


def load_legacy_release_fingerprints(root: Path) -> frozenset[str]:
    path = root.joinpath(*LEGACY_RELEASES_PATH.parts)
    mode = os.lstat(path).st_mode
    # LEGACY_REGISTRY_FILE_GUARD_BEGIN
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode) or path.resolve(strict=True) != path:
        raise ValueError("legacy release registry 不是安全 regular file")
    # LEGACY_REGISTRY_FILE_GUARD_END
    releases: set[str] = set()
    fingerprints: set[str] = set()
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw or raw.startswith("#"):
            continue
        parts = raw.split("\t")
        # LEGACY_REGISTRY_COLUMNS_GUARD_BEGIN
        if len(parts) != 2:
            raise ValueError(f"legacy release registry 第 {number} 列欄位數錯誤")
        # LEGACY_REGISTRY_COLUMNS_GUARD_END
        release, fingerprint = parts
        # LEGACY_REGISTRY_ID_GUARD_BEGIN
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", release):
            raise ValueError(f"legacy release registry 第 {number} 列 release id 錯誤")
        # LEGACY_REGISTRY_ID_GUARD_END
        # LEGACY_REGISTRY_HASH_GUARD_BEGIN
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ValueError(f"legacy release registry 第 {number} 列 fingerprint 錯誤")
        # LEGACY_REGISTRY_HASH_GUARD_END
        # LEGACY_REGISTRY_DUPLICATE_RELEASE_GUARD_BEGIN
        if release in releases:
            raise ValueError("legacy release registry 含重複 release id")
        # LEGACY_REGISTRY_DUPLICATE_RELEASE_GUARD_END
        # LEGACY_REGISTRY_DUPLICATE_FINGERPRINT_GUARD_BEGIN
        if fingerprint in fingerprints:
            raise ValueError("legacy release registry 含重複 fingerprint")
        # LEGACY_REGISTRY_DUPLICATE_FINGERPRINT_GUARD_END
        releases.add(release)
        fingerprints.add(fingerprint)
    # LEGACY_REGISTRY_EMPTY_GUARD_BEGIN
    if not fingerprints:
        raise ValueError("legacy release registry 不得為空")
    # LEGACY_REGISTRY_EMPTY_GUARD_END
    return frozenset(fingerprints)


def legacy_tree_fingerprint(target: Path) -> str:
    files, directories = installed_inventory(target)
    digest = hashlib.sha256(b"codex-legacy-release-v1\0")

    def add_field(kind: bytes, value: PurePosixPath) -> None:
        encoded = value.as_posix().encode("utf-8")
        digest.update(kind)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)

    for directory in sorted(directories, key=lambda value: value.as_posix()):
        add_field(b"D", directory)
    for relative in sorted(files, key=lambda value: value.as_posix()):
        path = target.joinpath(*relative.parts)
        mode = os.lstat(path).st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode) or path.resolve(strict=True) != path:
            raise ValueError("legacy target fingerprint 遇到不安全檔案")
        add_field(b"F", relative)
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def is_legacy_codex_home(target: Path, root: Path) -> bool:
    try:
        fingerprint = legacy_tree_fingerprint(target)
        known_fingerprints = load_legacy_release_fingerprints(root)
    except (OSError, UnicodeError, ValueError):
        return False
    # LEGACY_RELEASE_FINGERPRINT_GUARD_BEGIN
    if fingerprint not in known_fingerprints:
        return False
    # LEGACY_RELEASE_FINGERPRINT_GUARD_END
    return True


def is_existing_codex_home(target: Path, root: Path, trusted_rows: list[tuple[Path, PurePosixPath]]) -> bool:
    """只允許 --force 替換與本次 trusted source 完全一致的現行安裝。"""
    try:
        destinations = parse_installed_manifest(target)
    except FileNotFoundError:
        return is_legacy_codex_home(target, root)
    except (OSError, UnicodeError, ValueError):
        return False

    expected_files = {destination for _, destination in trusted_rows}
    if destinations != expected_files:
        return False
    try:
        trusted_manifest = read_regular_nofollow(root / "manifest.tsv", "trusted manifest")
        installed_manifest = read_regular_nofollow(target / "manifest.tsv", "installed manifest")
        if installed_manifest != trusted_manifest:
            return False
        files, directories = installed_inventory(target)
        if files != expected_files or directories != expected_directories(expected_files):
            return False
        for source, destination in trusted_rows:
            installed = target.joinpath(*destination.parts)
            if installed.resolve(strict=True) != installed:
                return False
            source_digest = hashlib.sha256(read_regular_nofollow(source, "trusted install source")).digest()
            installed_digest = hashlib.sha256(read_regular_nofollow(installed, "installed destination")).digest()
            if installed_digest != source_digest:
                return False
        config_data = read_regular_nofollow(target / "config.toml", "installed config")
        parsed = tomllib.loads(config_data.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, tomllib.TOMLDecodeError):
        return False
    if parsed.get("features", {}).get("memories") is not True:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Install workflow into a safe, isolated CODEX_HOME")
    parser.add_argument("--target", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve(strict=True).parent.parent
    if not args.target or not args.target.strip():
        raise ValueError("target 不得為空白")
    raw_target = Path(os.path.abspath(os.path.expanduser(args.target)))
    if any(part == ".." for part in Path(args.target).parts):
        raise ValueError("target 不得包含 ..")
    reject_symlink_ancestors(raw_target)
    target = raw_target.resolve(strict=False)
    home = Path.home().resolve(strict=True)
    if target == Path(target.anchor) or target == home:
        raise ValueError("拒絕危險 target")
    if is_within(target, root) or is_within(root, target):
        raise ValueError("target 與 source repo 不得重疊")

    rows = parse_manifest(root)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    reject_symlink_ancestors(target)
    stage = parent / f".{target.name}.stage-{uuid.uuid4().hex}"
    backup: Path | None = None
    try:
        os.mkdir(stage, 0o700)
        for source, destination in rows:
            output = stage.joinpath(*destination.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, output, follow_symlinks=False)
        validate_staging(stage, rows)

        exists = os.path.lexists(target)
        if exists:
            mode = os.lstat(target).st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise ValueError("target 已存在且不是安全目錄")
            try:
                nonempty = next(target.iterdir(), None) is not None
            except OSError as exc:
                raise ValueError("無法安全檢查 target 內容") from exc
            if nonempty and not args.force:
                raise ValueError("target 非空；未寫入。請改用全新目錄，或明確加 --force 先備份")
            if nonempty:
                if not is_existing_codex_home(target, root, rows):
                    raise ValueError("--force 只允許替換既有 Codex workflow home")
                backup = parent / f"{target.name}.backup-{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
                os.replace(target, backup)
            else:
                os.rmdir(target)
        try:
            os.replace(stage, target)
        except Exception:
            if backup is not None and backup.exists() and not target.exists():
                os.replace(backup, target)
            raise
    finally:
        if stage.exists() and stage.parent == parent and stage.name.startswith(f".{target.name}.stage-"):
            shutil.rmtree(stage)

    if backup is not None:
        print(f"既有 target 已移至：{backup}")
    print(f"安裝完成：{target}")
    print("下一步：檢視 config.toml，並自行建立未追蹤的 SECRETS.local.md。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"安裝失敗：{exc}", file=sys.stderr)
        raise SystemExit(1)
