#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
REAL_GIT="$(command -v git)"
command -v python3 >/dev/null 2>&1 || exit 2
TMP="$(mktemp -d "${TMPDIR:-/tmp}/workflow-security-tests.XXXXXX")"
cleanup() { python3 - "$TMP" <<'PY'
from pathlib import Path
import shutil, sys
p=Path(sys.argv[1])
if p.name.startswith('workflow-security-tests.') and p.is_dir(): shutil.rmtree(p)
PY
}
trap cleanup EXIT

capture_rc() {
  local expected="$1" label="$2"; shift 2
  set +e
  "$@" > "$TMP/output" 2>&1
  local rc=$?
  set -e
  [ "$rc" -eq "$expected" ] || { echo "security mutation wrong exit: $label expected=$expected actual=$rc" >&2; exit 1; }
}

snapshot_target() {
  local target="$1" output="$2"
  python3 - "$target" "$output" <<'PY'
from pathlib import Path
import hashlib, os, stat, sys
root = Path(sys.argv[1])
rows = [f".\tdirectory\tinode={os.lstat(root).st_ino}"]
for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
    rel = path.relative_to(root).as_posix()
    mode = os.lstat(path).st_mode
    if stat.S_ISREG(mode):
        rows.append(f"{rel}\tregular\tsha256={hashlib.sha256(path.read_bytes()).hexdigest()}")
    elif stat.S_ISDIR(mode):
        rows.append(f"{rel}\tdirectory")
    elif stat.S_ISLNK(mode):
        rows.append(f"{rel}\tsymlink\ttarget={os.readlink(path)}")
    else:
        rows.append(f"{rel}\tother\tmode={mode}")
Path(sys.argv[2]).write_text("\n".join(rows) + "\n", encoding="utf-8")
PY
}

assert_no_target_backup() {
  python3 - "$1" <<'PY'
from pathlib import Path
import sys
target = Path(sys.argv[1])
assert not list(target.parent.glob(f"{target.name}.backup-*"))
PY
}

expect_finding() {
  local label="$1" rule="$2" forbidden="$3"; shift 3
  capture_rc 1 "$label" "$@"
  grep -F "規則：${rule}" "$TMP/output" >/dev/null || { echo "missing finding rule: $label" >&2; exit 1; }
  if [ -n "$forbidden" ] && grep -F -- "$forbidden" "$TMP/output" >/dev/null; then
    echo "finding leaked matched value: $label" >&2
    exit 1
  fi
}

expect_path_finding() {
  local label="$1" rule="$2" forbidden="$3" revision="$4"; shift 4
  capture_rc 1 "$label" "$@"
  grep -F "規則：${rule}" "$TMP/output" >/dev/null || { echo "missing path finding rule: $label" >&2; exit 1; }
  grep -F "<redacted-path>" "$TMP/output" >/dev/null || { echo "missing redacted path marker: $label" >&2; exit 1; }
  if [ -n "$revision" ]; then
    grep -F "$revision" "$TMP/output" >/dev/null || { echo "missing history commit: $label" >&2; exit 1; }
  fi
  if grep -F -- "$forbidden" "$TMP/output" >/dev/null; then
    echo "path finding leaked matched filename: $label" >&2
    exit 1
  fi
}

expect_crash() { local label="$1"; shift; capture_rc 2 "$label" "$@"; }

expect_raw_commit_finding() {
  local label="$1" rule="$2" forbidden="$3" revision="$4"; shift 4
  expect_finding "$label" "$rule" "$forbidden" "$@"
  grep -F "$revision" "$TMP/output" >/dev/null || { echo "missing raw commit id: $label" >&2; exit 1; }
}

init_repo() {
  local repo="$1"
  "$REAL_GIT" -C "$repo" init -q -b main
  "$REAL_GIT" -C "$repo" config user.name 'Workflow Test'
  "$REAL_GIT" -C "$repo" config user.email 'maintainer@example.com'
  "$REAL_GIT" -C "$repo" add .
  "$REAL_GIT" -C "$repo" commit -q -m baseline
}

create_raw_commit() {
  local repo="$1" mode="$2" payload="$3" ref="$4"
  local tree parent object_file object_id
  tree="$("$REAL_GIT" -C "$repo" rev-parse HEAD^{tree})"
  parent="$("$REAL_GIT" -C "$repo" rev-parse HEAD)"
  object_file="$TMP/raw-commit-${ref//\//-}"
  python3 - "$object_file" "$tree" "$parent" "$mode" "$payload" <<'PY'
from pathlib import Path
import sys
path, tree, parent, mode, payload = sys.argv[1:]
headers = (
    f"tree {tree}\n"
    f"parent {parent}\n"
    "author Workflow Test <maintainer@example.com> 0 +0000\n"
    "committer Workflow Test <maintainer@example.com> 0 +0000\n"
).encode("ascii")
if mode == "secret-header":
    body = headers + b"gpgsig -----BEGIN TEST SIGNATURE-----\n " + payload.encode("utf-8") + b"\n -----END TEST SIGNATURE-----\n\nneutral\n"
elif mode == "binary-header":
    body = headers + b"x-test \xff\n\nneutral\n"
else:
    raise SystemExit("unknown raw commit mode")
Path(path).write_bytes(body)
PY
  object_id="$("$REAL_GIT" -C "$repo" hash-object -t commit -w --stdin < "$object_file")"
  "$REAL_GIT" -C "$repo" update-ref "refs/heads/$ref" "$object_id"
  printf '%s\n' "$object_id"
}

make_scanner() {
  local repo="$1"
  mkdir -p "$repo/scripts" "$repo/policy"
  cp "$ROOT/scripts/scan-secrets.sh" "$repo/scripts/"
  cp "$ROOT/policy/deny-paths.tsv" "$ROOT/policy/secret-rules.tsv" "$repo/policy/"
  cp "$ROOT/.gitignore" "$repo/.gitignore"
}

# Secret scanner: exact exit semantics, metadata, binary blobs, and history failures.
expect_crash unknown-argument bash "$ROOT/scripts/scan-secrets.sh" --unknown
NO_GIT="$TMP/no-git"; make_scanner "$NO_GIT"
expect_crash missing-repository bash "$NO_GIT/scripts/scan-secrets.sh" --history
EMPTY_GIT="$TMP/empty-git"; make_scanner "$EMPTY_GIT"; "$REAL_GIT" -C "$EMPTY_GIT" init -q -b main
expect_crash empty-history bash "$EMPTY_GIT/scripts/scan-secrets.sh" --history
expect_crash missing-git env GIT_BIN=workflow-command-that-does-not-exist bash "$ROOT/scripts/scan-secrets.sh" --history

SCAN="$TMP/scanner"; make_scanner "$SCAN"; init_repo "$SCAN"
bash "$SCAN/scripts/scan-secrets.sh" --history >/dev/null
printf '#!/usr/bin/env bash\nfor arg in "$@"; do [ "$arg" = show ] && exit 3; done\nexec "%s" "$@"\n' "$REAL_GIT" > "$TMP/fake-git"
chmod +x "$TMP/fake-git"
expect_crash unreadable-history env GIT_BIN="$TMP/fake-git" bash "$SCAN/scripts/scan-secrets.sh" --history
printf '#!/usr/bin/env bash\n[ "$1" = cat-file ] && [ "$2" = commit ] && exit 3\nexec "%s" "$@"\n' "$REAL_GIT" > "$TMP/fake-raw-commit-git"
chmod +x "$TMP/fake-raw-commit-git"
expect_crash unreadable-raw-commit env GIT_BIN="$TMP/fake-raw-commit-git" bash "$SCAN/scripts/scan-secrets.sh" --history

SECRET="$(python3 - <<'PY'
print("gh" + "p_" + "abcdefghijklmnopqrstuvwxyz123456")
PY
)"
printf '%s\n' "$SECRET" > "$SCAN/tree.txt"
expect_finding tree-secret github-token "$SECRET" bash "$SCAN/scripts/scan-secrets.sh"
rm "$SCAN/tree.txt"
python3 - "$SCAN/current.bin" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).write_bytes(b'text\x00bytes')
PY
expect_finding tree-binary binary-text-only '' bash "$SCAN/scripts/scan-secrets.sh"
rm "$SCAN/current.bin"
CONTROL_TAB_NAME=$'neutral\tcontrol'
printf 'neutral\n' > "$SCAN/$CONTROL_TAB_NAME"
expect_path_finding scanner-current-control path-control-character "$CONTROL_TAB_NAME" '' bash "$SCAN/scripts/scan-secrets.sh"
rm "$SCAN/$CONTROL_TAB_NAME"
mkdir "$SCAN/$CONTROL_TAB_NAME"
expect_path_finding scanner-current-control-directory path-control-character "$CONTROL_TAB_NAME" '' bash "$SCAN/scripts/scan-secrets.sh"
rmdir "$SCAN/$CONTROL_TAB_NAME"

HSECRET="$TMP/history-secret"; cp -R "$SCAN" "$HSECRET"
printf '%s\n' "$SECRET" > "$HSECRET/value.txt"; "$REAL_GIT" -C "$HSECRET" add value.txt; "$REAL_GIT" -C "$HSECRET" commit -q -m add
"$REAL_GIT" -C "$HSECRET" rm -q value.txt; "$REAL_GIT" -C "$HSECRET" commit -q -m remove
expect_finding history-secret github-token "$SECRET" bash "$HSECRET/scripts/scan-secrets.sh" --history

HMETA="$TMP/history-meta"; cp -R "$SCAN" "$HMETA"; "$REAL_GIT" -C "$HMETA" commit -q --allow-empty -m "$SECRET"
expect_finding history-metadata github-token "$SECRET" bash "$HMETA/scripts/scan-secrets.sh" --history

HRAW="$TMP/history-raw-meta"; cp -R "$SCAN" "$HRAW"
HRAW_COMMIT="$(create_raw_commit "$HRAW" secret-header "$SECRET" raw-secret-header)"
expect_raw_commit_finding history-raw-metadata github-token "$SECRET" "$HRAW_COMMIT" bash "$HRAW/scripts/scan-secrets.sh" --history
HRAW_BINARY="$TMP/history-raw-binary"; cp -R "$SCAN" "$HRAW_BINARY"
HRAW_BINARY_COMMIT="$(create_raw_commit "$HRAW_BINARY" binary-header '' raw-binary-header)"
expect_raw_commit_finding history-raw-binary binary-text-only '' "$HRAW_BINARY_COMMIT" bash "$HRAW_BINARY/scripts/scan-secrets.sh" --history

HBINARY="$TMP/history-binary"; cp -R "$SCAN" "$HBINARY"
python3 - "$HBINARY/value.bin" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).write_bytes(b'archive\x00binary')
PY
"$REAL_GIT" -C "$HBINARY" add value.bin; "$REAL_GIT" -C "$HBINARY" commit -q -m add
"$REAL_GIT" -C "$HBINARY" rm -q value.bin; "$REAL_GIT" -C "$HBINARY" commit -q -m remove
expect_finding history-binary binary-text-only '' bash "$HBINARY/scripts/scan-secrets.sh" --history

HCONTROL="$TMP/history-control-path"; cp -R "$SCAN" "$HCONTROL"
CONTROL_BIDI_NAME="$(python3 - <<'PY'
print("neutral-" + chr(0x202E) + "-control")
PY
)"
printf 'neutral\n' > "$HCONTROL/$CONTROL_BIDI_NAME"
"$REAL_GIT" -C "$HCONTROL" add -- "$CONTROL_BIDI_NAME"
"$REAL_GIT" -C "$HCONTROL" commit -q -m add-control-path
"$REAL_GIT" -C "$HCONTROL" rm -q -- "$CONTROL_BIDI_NAME"
"$REAL_GIT" -C "$HCONTROL" commit -q -m remove-control-path
expect_path_finding scanner-history-control path-control-character "$CONTROL_BIDI_NAME" '' bash "$HCONTROL/scripts/scan-secrets.sh" --history

# Deny-path regex and required ignore entry are paired one-for-one.
POLICY="$TMP/policy"; mkdir -p "$POLICY/policy" "$POLICY/scripts"
cp "$ROOT/policy/deny-paths.tsv" "$ROOT/policy/deny-path-fixtures.tsv" "$POLICY/policy/"
cp "$ROOT/scripts/policy_check.py" "$POLICY/scripts/"; cp "$ROOT/.gitignore" "$POLICY/.gitignore"
while IFS=$'\t' read -r rule fixture extra; do
  [ -n "$rule" ] || continue; case "$rule" in \#*) continue ;; esac; [ -z "${extra:-}" ] || exit 1
  printf '%s\n' "$fixture" > "$TMP/paths"
  capture_rc 1 "deny-path-$rule" python3 "$POLICY/scripts/policy_check.py" --root "$POLICY" --paths-file "$TMP/paths"
  grep -F "規則：${rule}" "$TMP/output" >/dev/null || exit 1
done < "$ROOT/policy/deny-path-fixtures.tsv"
while IFS=$'\t' read -r rule pattern required extra; do
  [ -n "$rule" ] || continue; case "$rule" in \#*) continue ;; esac; [ -z "${extra:-}" ] || exit 1
  [ "$required" != "-" ] || continue
  python3 - "$ROOT/.gitignore" "$POLICY/.gitignore" "$required" <<'PY'
from pathlib import Path
import sys
lines=Path(sys.argv[1]).read_text(encoding='utf-8').splitlines(); required=sys.argv[3]
assert lines.count(required) == 1
Path(sys.argv[2]).write_text('\n'.join(x for x in lines if x != required)+'\n', encoding='utf-8')
PY
  : > "$TMP/paths"
  capture_rc 1 "gitignore-$rule" python3 "$POLICY/scripts/policy_check.py" --root "$POLICY" --paths-file "$TMP/paths" --check-gitignore
  grep -F "規則：${rule}" "$TMP/output" >/dev/null || exit 1
done < "$ROOT/policy/deny-paths.tsv"
cp "$ROOT/.gitignore" "$POLICY/.gitignore"
python3 "$POLICY/scripts/policy_check.py" --root "$POLICY" --paths-file "$TMP/paths" --check-gitignore

# Installer rejects blank, broad, arbitrary, linked, overlapping, and malformed targets.
INSTALL="$TMP/installer"; mkdir -p "$INSTALL/agents" "$INSTALL/scripts" "$INSTALL/workflows"
cp "$ROOT/scripts/install.sh" "$ROOT/scripts/install_impl.py" "$INSTALL/scripts/"
cp "$ROOT/config.example.toml" "$INSTALL/config.example.toml"; printf 'workflow\n' > "$INSTALL/AGENTS.md"
printf 'architect\n' > "$INSTALL/agents/architect.toml"
printf 'development workflow\n' > "$INSTALL/workflows/development-workflow.md"
write_manifest() { printf 'install\tAGENTS.md\tAGENTS.md\ninstall\tagents/architect.toml\tagents/architect.toml\ninstall\tconfig.example.toml\tconfig.toml\ninstall\tmanifest.tsv\tmanifest.tsv\nsupport\tscripts/install.sh\t-\nsupport\tscripts/install_impl.py\t-\ninstall\tworkflows/development-workflow.md\tworkflows/development-workflow.md\n' > "$INSTALL/manifest.tsv"; }
write_manifest
SAFE_PARENT="$(python3 - "$TMP/install-targets" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]).resolve(); p.mkdir(parents=True); print(p)
PY
)"

expect_installed_validation_rejected() {
  local label="$1" target="$2" expected="$3"
  capture_rc 1 "installed-$label" python3 "$ROOT/scripts/validate_repo.py" --root "$ROOT" --installed "$target"
  grep -F "$expected" "$TMP/output" >/dev/null || {
    echo "installed validation missing failure: $label" >&2
    exit 1
  }
}

# --installed 只驗 managed surface；runtime extras 不得影響結果。
MANAGED_RUNTIME="$SAFE_PARENT/managed-runtime"
bash "$ROOT/scripts/install.sh" --target "$MANAGED_RUNTIME" >/dev/null
mkdir -p "$MANAGED_RUNTIME/plugins/cache/openai-bundled/chrome"
printf 'runtime cache\n' > "$MANAGED_RUNTIME/plugins/cache/runtime-extra"
ln -s "$SAFE_PARENT/unmanaged-plugin-target" "$MANAGED_RUNTIME/plugins/cache/openai-bundled/chrome/latest"
python3 "$ROOT/scripts/validate_repo.py" --root "$ROOT" --installed "$MANAGED_RUNTIME" >/dev/null

MANAGED_MISSING="$SAFE_PARENT/managed-missing"
bash "$ROOT/scripts/install.sh" --target "$MANAGED_MISSING" >/dev/null
python3 - "$MANAGED_MISSING/AGENTS.md" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).unlink()
PY
expect_installed_validation_rejected missing "$MANAGED_MISSING" "managed destination missing: AGENTS.md"

MANAGED_TARGET_LINK="$SAFE_PARENT/managed-target-link"
bash "$ROOT/scripts/install.sh" --target "$MANAGED_TARGET_LINK" >/dev/null
mv "$MANAGED_TARGET_LINK/AGENTS.md" "$MANAGED_TARGET_LINK/AGENTS.real"
ln -s "AGENTS.real" "$MANAGED_TARGET_LINK/AGENTS.md"
expect_installed_validation_rejected target-link "$MANAGED_TARGET_LINK" "managed destination is symlink: AGENTS.md"

MANAGED_ANCESTOR_LINK="$SAFE_PARENT/managed-ancestor-link"
bash "$ROOT/scripts/install.sh" --target "$MANAGED_ANCESTOR_LINK" >/dev/null
mv "$MANAGED_ANCESTOR_LINK/agents" "$MANAGED_ANCESTOR_LINK/agents.real"
ln -s "agents.real" "$MANAGED_ANCESTOR_LINK/agents"
expect_installed_validation_rejected ancestor-link "$MANAGED_ANCESTOR_LINK" "managed destination ancestor is not a safe directory: agents"

MANAGED_NONREGULAR="$SAFE_PARENT/managed-nonregular"
bash "$ROOT/scripts/install.sh" --target "$MANAGED_NONREGULAR" >/dev/null
mv "$MANAGED_NONREGULAR/AGENTS.md" "$MANAGED_NONREGULAR/AGENTS.real"
mkdir "$MANAGED_NONREGULAR/AGENTS.md"
expect_installed_validation_rejected nonregular "$MANAGED_NONREGULAR" "managed destination is not regular file: AGENTS.md"

MANAGED_BAD_CONFIG="$SAFE_PARENT/managed-bad-config"
bash "$ROOT/scripts/install.sh" --target "$MANAGED_BAD_CONFIG" >/dev/null
python3 - "$MANAGED_BAD_CONFIG/config.toml" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
assert "memories = true" in text
path.write_text(text.replace("memories = true", "memories = false", 1), encoding="utf-8")
PY
expect_installed_validation_rejected bad-config "$MANAGED_BAD_CONFIG" "Memories is not enabled in config.toml"

materialize_known_legacy() {
  local target="$1"
  bash "$ROOT/scripts/install.sh" --target "$target" >/dev/null
  python3 - "$target/manifest.tsv" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).unlink()
PY
  local patch="$TMP/legacy-pre-provenance-20260727.patch"
  python3 - "$ROOT/tests/fixtures/legacy-pre-provenance-20260727/content.patch.b64" "$patch" <<'PY'
from base64 import b64decode
from pathlib import Path
import sys
source, destination = map(Path, sys.argv[1:])
encoded = b"".join(source.read_bytes().split())
destination.write_bytes(b64decode(encoded, validate=True))
PY
  (cd "$target" && "$REAL_GIT" apply --whitespace=nowarn --reverse "$patch")
  python3 - "$ROOT" "$target" <<'PY'
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
sys.dont_write_bytecode = True
root, target = Path(sys.argv[1]), Path(sys.argv[2])
module_path = root / "scripts/install_impl.py"
spec = spec_from_file_location("legacy_fixture_installer", module_path)
assert spec is not None and spec.loader is not None
module = module_from_spec(spec)
spec.loader.exec_module(module)
fingerprint = module.legacy_tree_fingerprint(target)
assert fingerprint in module.load_legacy_release_fingerprints(root), fingerprint
PY
}
make_registry_fault_source() {
  local label="$1"
  local source="$TMP/legacy-registry-$label-source"
  cp -R "$INSTALL" "$source"
  mkdir -p "$source/policy"
  cp "$ROOT/policy/legacy-releases.tsv" "$source/policy/legacy-releases.tsv"
  (cd "$source" && pwd -P)
}
assert_registry_fault() {
  local label="$1" source="$2" expected_parser_error="$3"
  local target="$SAFE_PARENT/legacy-registry-$label"
  python3 - "$source/scripts/install_impl.py" "$source" "$expected_parser_error" <<'PY'
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
sys.dont_write_bytecode = True
module_path, root, expected = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
spec = spec_from_file_location("registry_fault_install_impl", module_path)
assert spec is not None and spec.loader is not None
module = module_from_spec(spec)
spec.loader.exec_module(module)
try:
    module.load_legacy_release_fingerprints(root)
except ValueError as error:
    if str(error) == expected:
        raise SystemExit(0)
    print(f"registry parser wrong error: expected={expected!r} actual={str(error)!r}", file=sys.stderr)
    raise SystemExit(1)
print("registry parser accepted invalid policy", file=sys.stderr)
raise SystemExit(1)
PY
  test ! -e "$ROOT/scripts/__pycache__" || { echo "dynamic import left repository bytecode" >&2; exit 1; }
  materialize_known_legacy "$target"
  snapshot_target "$target" "$TMP/legacy-registry-$label.before"
  capture_rc 1 "legacy-registry-$label" bash "$source/scripts/install.sh" --target "$target" --force
  local actual
  actual="$(cat "$TMP/output")"
  if [ "$actual" != "安裝失敗：--force 只允許替換既有 Codex workflow home" ]; then
    echo "legacy registry wrong installer error: $label" >&2
    exit 1
  fi
  snapshot_target "$target" "$TMP/legacy-registry-$label.after"
  cmp -s "$TMP/legacy-registry-$label.before" "$TMP/legacy-registry-$label.after" || {
    echo "target changed after rejected legacy registry: $label" >&2
    exit 1
  }
  assert_no_target_backup "$target"
}
assert_rejected_legacy() {
  local label="$1" target="$2"
  snapshot_target "$target" "$TMP/$label.before"
  capture_rc 1 "$label" bash "$ROOT/scripts/install.sh" --target "$target" --force
  snapshot_target "$target" "$TMP/$label.after"
  cmp -s "$TMP/$label.before" "$TMP/$label.after" || { echo "target changed after rejected legacy: $label" >&2; exit 1; }
  assert_no_target_backup "$target"
}
assert_rejected_current() {
  local label="$1" target="$2"
  snapshot_target "$target" "$TMP/$label.before"
  capture_rc 1 "$label" bash "$INSTALL/scripts/install.sh" --target "$target" --force
  snapshot_target "$target" "$TMP/$label.after"
  cmp -s "$TMP/$label.before" "$TMP/$label.after" || { echo "target changed after rejected current install: $label" >&2; exit 1; }
  assert_no_target_backup "$target"
}
bash "$INSTALL/scripts/install.sh" --target "$SAFE_PARENT/clean" >/dev/null
capture_rc 1 blank-target bash "$INSTALL/scripts/install.sh" --target ''
capture_rc 1 blank-target-force bash "$INSTALL/scripts/install.sh" --target '' --force
capture_rc 1 whitespace-target bash "$INSTALL/scripts/install.sh" --target '   '
snapshot_target "$SAFE_PARENT/clean" "$TMP/current.before"
output="$(bash "$INSTALL/scripts/install.sh" --target "$SAFE_PARENT/clean" --force)"
backup="$(printf '%s\n' "$output" | sed -n 's/^既有 target 已移至：//p')"
test -n "$backup" && test -d "$backup"
snapshot_target "$backup" "$TMP/current.backup"
cmp -s "$TMP/current.before" "$TMP/current.backup"
mv "$SAFE_PARENT/clean" "$SAFE_PARENT/current-upgraded"
mv "$backup" "$SAFE_PARENT/clean"
snapshot_target "$SAFE_PARENT/clean" "$TMP/current.restored"
cmp -s "$TMP/current.before" "$TMP/current.restored"

bash "$INSTALL/scripts/install.sh" --target "$SAFE_PARENT/forged-current-manifest" >/dev/null
printf '# semantically inert forged provenance\n' >> "$SAFE_PARENT/forged-current-manifest/manifest.tsv"
assert_rejected_current forged-current-manifest "$SAFE_PARENT/forged-current-manifest"
bash "$INSTALL/scripts/install.sh" --target "$SAFE_PARENT/current-extra-payload" >/dev/null
printf 'unrelated\n' > "$SAFE_PARENT/current-extra-payload/unrelated-data"
assert_rejected_current current-extra-payload "$SAFE_PARENT/current-extra-payload"
bash "$INSTALL/scripts/install.sh" --target "$SAFE_PARENT/current-hash-drift" >/dev/null
printf '\ncontent drift\n' >> "$SAFE_PARENT/current-hash-drift/AGENTS.md"
assert_rejected_current current-hash-drift "$SAFE_PARENT/current-hash-drift"
materialize_known_legacy "$SAFE_PARENT/legacy"
snapshot_target "$SAFE_PARENT/legacy" "$TMP/legacy.before"
legacy_output="$(bash "$ROOT/scripts/install.sh" --target "$SAFE_PARENT/legacy" --force)"
legacy_backup="$(printf '%s\n' "$legacy_output" | sed -n 's/^既有 target 已移至：//p')"
test -n "$legacy_backup" && test -d "$legacy_backup"
test -f "$legacy_backup/AGENTS.md" && test ! -e "$legacy_backup/manifest.tsv"
test -f "$SAFE_PARENT/legacy/manifest.tsv"
snapshot_target "$legacy_backup" "$TMP/legacy.backup"
cmp -s "$TMP/legacy.before" "$TMP/legacy.backup"
mv "$SAFE_PARENT/legacy" "$SAFE_PARENT/legacy-upgraded"
mv "$legacy_backup" "$SAFE_PARENT/legacy"
snapshot_target "$SAFE_PARENT/legacy" "$TMP/legacy.restored"
cmp -s "$TMP/legacy.before" "$TMP/legacy.restored"
materialize_known_legacy "$SAFE_PARENT/unknown-legacy-content"
printf '\nunknown release drift\n' >> "$SAFE_PARENT/unknown-legacy-content/AGENTS.md"
assert_rejected_legacy unknown-legacy-content "$SAFE_PARENT/unknown-legacy-content"
materialize_known_legacy "$SAFE_PARENT/legacy-extra-payload"
printf 'unrelated\n' > "$SAFE_PARENT/legacy-extra-payload/unrelated-data"
assert_rejected_legacy legacy-extra-payload "$SAFE_PARENT/legacy-extra-payload"
materialize_known_legacy "$SAFE_PARENT/legacy-incomplete"
python3 - "$SAFE_PARENT/legacy-incomplete/AGENTS.md" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).unlink()
PY
assert_rejected_legacy legacy-incomplete "$SAFE_PARENT/legacy-incomplete"
materialize_known_legacy "$SAFE_PARENT/legacy-symlink"
cp "$SAFE_PARENT/legacy-symlink/AGENTS.md" "$SAFE_PARENT/legacy-symlink-payload"
python3 - "$SAFE_PARENT/legacy-symlink/AGENTS.md" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).unlink()
PY
ln -s "$SAFE_PARENT/legacy-symlink-payload" "$SAFE_PARENT/legacy-symlink/AGENTS.md"
assert_rejected_legacy legacy-symlink "$SAFE_PARENT/legacy-symlink"
mkdir "$SAFE_PARENT/forged-legacy-marker"
printf 'pre-provenance-20260727\n' > "$SAFE_PARENT/forged-legacy-marker/release-id"
cp "$ROOT/config.example.toml" "$SAFE_PARENT/forged-legacy-marker/config.toml"
printf 'forged workflow\n' > "$SAFE_PARENT/forged-legacy-marker/AGENTS.md"
assert_rejected_legacy forged-legacy-marker "$SAFE_PARENT/forged-legacy-marker"

KNOWN_LEGACY_FINGERPRINT="$(awk -F $'\t' '!/^#/ { print $2; exit }' "$ROOT/policy/legacy-releases.tsv")"
OTHER_LEGACY_FINGERPRINT="$(printf '%064d' 0)"
registry_source="$(make_registry_fault_source malformed-columns)"
printf 'known-release\t%s\textra-column\n' "$KNOWN_LEGACY_FINGERPRINT" > "$registry_source/policy/legacy-releases.tsv"
assert_registry_fault malformed-columns "$registry_source" "legacy release registry 第 1 列欄位數錯誤"
registry_source="$(make_registry_fault_source illegal-id)"
printf 'Illegal_ID\t%s\n' "$KNOWN_LEGACY_FINGERPRINT" > "$registry_source/policy/legacy-releases.tsv"
assert_registry_fault illegal-id "$registry_source" "legacy release registry 第 1 列 release id 錯誤"
registry_source="$(make_registry_fault_source illegal-hash)"
printf 'known-release\tnot-a-sha256\n' > "$registry_source/policy/legacy-releases.tsv"
assert_registry_fault illegal-hash "$registry_source" "legacy release registry 第 1 列 fingerprint 錯誤"
registry_source="$(make_registry_fault_source duplicate-release)"
printf 'known-release\t%s\nknown-release\t%s\n' \
  "$KNOWN_LEGACY_FINGERPRINT" "$OTHER_LEGACY_FINGERPRINT" > "$registry_source/policy/legacy-releases.tsv"
assert_registry_fault duplicate-release "$registry_source" "legacy release registry 含重複 release id"
registry_source="$(make_registry_fault_source duplicate-fingerprint)"
printf 'known-release-a\t%s\nknown-release-b\t%s\n' \
  "$KNOWN_LEGACY_FINGERPRINT" "$KNOWN_LEGACY_FINGERPRINT" > "$registry_source/policy/legacy-releases.tsv"
assert_registry_fault duplicate-fingerprint "$registry_source" "legacy release registry 含重複 fingerprint"
registry_source="$(make_registry_fault_source empty)"
: > "$registry_source/policy/legacy-releases.tsv"
assert_registry_fault empty "$registry_source" "legacy release registry 不得為空"
registry_source="$(make_registry_fault_source symlink)"
cp "$registry_source/policy/legacy-releases.tsv" "$TMP/legacy-registry-symlink.tsv"
rm "$registry_source/policy/legacy-releases.tsv"
ln -s "$TMP/legacy-registry-symlink.tsv" "$registry_source/policy/legacy-releases.tsv"
assert_registry_fault symlink "$registry_source" "legacy release registry 不是安全 regular file"

run_provenance_mutation() {
  local label="$1" kind="$2" source="$3" destination="$4" copies="$5"
  local target="$SAFE_PARENT/$label"
  bash "$INSTALL/scripts/install.sh" --target "$target" >/dev/null
  touch "$target/sentinel"; printf 'unrelated\n' > "$target/unrelated-data"
  python3 - "$target/manifest.tsv" "$kind" "$source" "$destination" "$copies" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = "install\tmanifest.tsv\tmanifest.tsv"
assert text.count(old) == 1
new = "\t".join(sys.argv[2:5])
replacement = "\n".join([new] * int(sys.argv[5]))
path.write_text(text.replace(old, replacement), encoding="utf-8")
PY
  snapshot_target "$target" "$TMP/$label.before"
  capture_rc 1 "$label" bash "$INSTALL/scripts/install.sh" --target "$target" --force
  snapshot_target "$target" "$TMP/$label.after"
  cmp -s "$TMP/$label.before" "$TMP/$label.after" || { echo "target changed after rejected provenance: $label" >&2; exit 1; }
  test -f "$target/sentinel"; test -f "$target/unrelated-data"
  assert_no_target_backup "$target"
}
run_provenance_mutation wrong-source-provenance install provenance.tsv manifest.tsv 1
run_provenance_mutation wrong-destination-provenance install manifest.tsv provenance.tsv 1
run_provenance_mutation wrong-kind-provenance support manifest.tsv manifest.tsv 1
run_provenance_mutation duplicate-provenance install manifest.tsv manifest.tsv 2
mkdir -p "$SAFE_PARENT/arbitrary"; touch "$SAFE_PARENT/arbitrary/sentinel"
printf 'project agents\n' > "$SAFE_PARENT/arbitrary/AGENTS.md"
cp "$INSTALL/config.example.toml" "$SAFE_PARENT/arbitrary/config.toml"
printf 'install\tAGENTS.md\tAGENTS.md\ninstall\tconfig.toml\tconfig.toml\ninstall\tmanifest.tsv\tmanifest.tsv\n' > "$SAFE_PARENT/arbitrary/manifest.tsv"
printf 'unrelated\n' > "$SAFE_PARENT/arbitrary/unrelated-data"
capture_rc 1 arbitrary-force bash "$INSTALL/scripts/install.sh" --target "$SAFE_PARENT/arbitrary" --force
test -f "$SAFE_PARENT/arbitrary/sentinel"
test -f "$SAFE_PARENT/arbitrary/AGENTS.md"
test -f "$SAFE_PARENT/arbitrary/config.toml"
test -f "$SAFE_PARENT/arbitrary/manifest.tsv"
test -f "$SAFE_PARENT/arbitrary/unrelated-data"
capture_rc 1 broad-directory-force bash "$INSTALL/scripts/install.sh" --target "$SAFE_PARENT" --force
mkdir -p "$SAFE_PARENT/real-parent"; ln -s "$SAFE_PARENT/real-parent" "$SAFE_PARENT/link-parent"
capture_rc 1 symlink-parent bash "$INSTALL/scripts/install.sh" --target "$SAFE_PARENT/link-parent/child"
capture_rc 1 root-target bash "$INSTALL/scripts/install.sh" --target /
capture_rc 1 home-target bash "$INSTALL/scripts/install.sh" --target "$HOME"
capture_rc 1 source-overlap bash "$INSTALL/scripts/install.sh" --target "$INSTALL/nested"
printf 'install\tAGENTS.md\t../escape\ninstall\tconfig.example.toml\tconfig.toml\n' > "$INSTALL/manifest.tsv"
capture_rc 1 traversal bash "$INSTALL/scripts/install.sh" --target "$SAFE_PARENT/traversal"
write_manifest; printf 'install\tAGENTS.md\tconfig.toml\ninstall\tconfig.example.toml\tconfig.toml\n' > "$INSTALL/manifest.tsv"
capture_rc 1 duplicate-destination bash "$INSTALL/scripts/install.sh" --target "$SAFE_PARENT/duplicate"

# Public validator carries only neutral generic rules but applies secret rules everywhere.
PUBLIC="$TMP/public"; mkdir -p "$PUBLIC/scripts" "$PUBLIC/policy"
cp "$ROOT/scripts/validate-public.sh" "$ROOT/scripts/validate_public.py" "$PUBLIC/scripts/"
cp "$ROOT/policy/public-rules.tsv" "$ROOT/policy/secret-rules.tsv" "$PUBLIC/policy/"
printf 'neutral public export\n' > "$PUBLIC/README.md"
bash "$PUBLIC/scripts/validate-public.sh" >/dev/null

HOME_FIXTURE="$(python3 - <<'PY'
print("/" + "Users" + "/sample-user/project")
PY
)"
printf '%s\n' "$HOME_FIXTURE" > "$PUBLIC/fixture.txt"
expect_finding public-home personal-home "$HOME_FIXTURE" bash "$PUBLIC/scripts/validate-public.sh"
rm "$PUBLIC/fixture.txt"
IP_FIXTURE="$(python3 - <<'PY'
print(".".join(("10", "20", "30", "40")))
PY
)"
printf '%s\n' "$IP_FIXTURE" > "$PUBLIC/fixture.txt"
expect_finding public-ip ipv4 "$IP_FIXTURE" bash "$PUBLIC/scripts/validate-public.sh"
rm "$PUBLIC/fixture.txt"

PERCENT_HOME="$(python3 - "$HOME_FIXTURE" <<'PY'
from urllib.parse import quote
import sys
print(quote(sys.argv[1], safe=""))
PY
)"
printf '%s\n' "$PERCENT_HOME" > "$PUBLIC/fixture.txt"
expect_finding public-percent-home personal-home "$HOME_FIXTURE" bash "$PUBLIC/scripts/validate-public.sh"
rm "$PUBLIC/fixture.txt"

BASE64_IP="$(python3 - "$IP_FIXTURE" <<'PY'
import base64
import sys
print(base64.urlsafe_b64encode(sys.argv[1].encode()).decode())
PY
)"
printf '%s\n' "$BASE64_IP" > "$PUBLIC/fixture.txt"
expect_finding public-base64-ip ipv4 "$IP_FIXTURE" bash "$PUBLIC/scripts/validate-public.sh"
rm "$PUBLIC/fixture.txt"

WRAPPED_BASE64_HOME="$(python3 - "$HOME_FIXTURE" <<'PY'
import base64
import sys
payload = b"x" * 54 + sys.argv[1].encode("utf-8")
print(base64.encodebytes(payload).decode("ascii"), end="")
PY
)"
printf '%s' "$WRAPPED_BASE64_HOME" > "$PUBLIC/fixture.b64"
expect_finding public-wrapped-base64-home personal-home "$HOME_FIXTURE" bash "$PUBLIC/scripts/validate-public.sh"
rm "$PUBLIC/fixture.b64"

FULLWIDTH_IP="$(python3 - "$IP_FIXTURE" <<'PY'
import sys
table = str.maketrans("0123456789.", "０１２３４５６７８９．")
print(sys.argv[1].translate(table))
PY
)"
printf '%s\n' "$FULLWIDTH_IP" > "$PUBLIC/fixture.txt"
expect_finding public-unicode-ip ipv4 "$IP_FIXTURE" bash "$PUBLIC/scripts/validate-public.sh"
rm "$PUBLIC/fixture.txt"

CASE_HOME="$(python3 - "$HOME_FIXTURE" <<'PY'
import sys
print(sys.argv[1].replace("Users", "uSeRs"))
PY
)"
printf '%s\n' "$CASE_HOME" > "$PUBLIC/fixture.txt"
expect_finding public-case-home personal-home "$HOME_FIXTURE" bash "$PUBLIC/scripts/validate-public.sh"
rm "$PUBLIC/fixture.txt"
printf 'neutral\n' > "$PUBLIC/$IP_FIXTURE"
expect_path_finding public-current-filename ipv4 "$IP_FIXTURE" '' bash "$PUBLIC/scripts/validate-public.sh"
rm "$PUBLIC/$IP_FIXTURE"
printf 'neutral\n' > "$PUBLIC/$CONTROL_TAB_NAME"
expect_path_finding public-current-control path-control-character "$CONTROL_TAB_NAME" '' bash "$PUBLIC/scripts/validate-public.sh"
rm "$PUBLIC/$CONTROL_TAB_NAME"
mkdir "$PUBLIC/$CONTROL_TAB_NAME"
expect_path_finding public-current-control-directory path-control-character "$CONTROL_TAB_NAME" '' bash "$PUBLIC/scripts/validate-public.sh"
rmdir "$PUBLIC/$CONTROL_TAB_NAME"
printf '%s\n' "$SECRET" > "$PUBLIC/fixture.txt"
expect_finding public-secret github-token "$SECRET" bash "$PUBLIC/scripts/validate-public.sh"
rm "$PUBLIC/fixture.txt"
python3 - "$PUBLIC/current.bin" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).write_bytes(b'public\x00binary')
PY
expect_finding public-binary binary-text-only '' bash "$PUBLIC/scripts/validate-public.sh"
rm "$PUBLIC/current.bin"

init_repo "$PUBLIC"
printf '#!/usr/bin/env bash\n[ "$1" = cat-file ] && [ "$2" = commit ] && exit 3\nexec "%s" "$@"\n' "$REAL_GIT" > "$TMP/public-fake-git"; chmod +x "$TMP/public-fake-git"
mkdir -p "$TMP/public-path"; cp "$TMP/public-fake-git" "$TMP/public-path/git"
expect_crash public-git-failure env PATH="$TMP/public-path:$PATH" bash "$PUBLIC/scripts/validate-public.sh"

PMETA="$TMP/public-meta"; cp -R "$PUBLIC" "$PMETA"; "$REAL_GIT" -C "$PMETA" commit -q --allow-empty -m "$SECRET"
expect_finding public-secret-metadata github-token "$SECRET" bash "$PMETA/scripts/validate-public.sh"
PRAW="$TMP/public-raw-meta"; cp -R "$PUBLIC" "$PRAW"
PRAW_COMMIT="$(create_raw_commit "$PRAW" secret-header "$SECRET" raw-secret-header)"
expect_raw_commit_finding public-raw-metadata github-token "$SECRET" "$PRAW_COMMIT" bash "$PRAW/scripts/validate-public.sh"
PRAW_BINARY="$TMP/public-raw-binary"; cp -R "$PUBLIC" "$PRAW_BINARY"
PRAW_BINARY_COMMIT="$(create_raw_commit "$PRAW_BINARY" binary-header '' raw-binary-header)"
expect_raw_commit_finding public-raw-binary binary-text-only '' "$PRAW_BINARY_COMMIT" bash "$PRAW_BINARY/scripts/validate-public.sh"
PTAG="$TMP/public-tag-meta"; cp -R "$PUBLIC" "$PTAG"; "$REAL_GIT" -C "$PTAG" tag -a release-check -m "$SECRET"
expect_finding public-tag-metadata github-token "$SECRET" bash "$PTAG/scripts/validate-public.sh"
PHISTORY="$TMP/public-history"; cp -R "$PUBLIC" "$PHISTORY"; printf '%s\n' "$HOME_FIXTURE" > "$PHISTORY/value.txt"
"$REAL_GIT" -C "$PHISTORY" add value.txt; "$REAL_GIT" -C "$PHISTORY" commit -q -m add; "$REAL_GIT" -C "$PHISTORY" rm -q value.txt; "$REAL_GIT" -C "$PHISTORY" commit -q -m remove
expect_finding public-history personal-home "$HOME_FIXTURE" bash "$PHISTORY/scripts/validate-public.sh"
PBINARY="$TMP/public-binary-history"; cp -R "$PUBLIC" "$PBINARY"
python3 - "$PBINARY/value.bin" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).write_bytes(b'history\x00binary')
PY
"$REAL_GIT" -C "$PBINARY" add value.bin; "$REAL_GIT" -C "$PBINARY" commit -q -m add; "$REAL_GIT" -C "$PBINARY" rm -q value.bin; "$REAL_GIT" -C "$PBINARY" commit -q -m remove
expect_finding public-history-binary binary-text-only '' bash "$PBINARY/scripts/validate-public.sh"

PPATH_HISTORY="$TMP/public-path-history"; cp -R "$PUBLIC" "$PPATH_HISTORY"
printf 'neutral\n' > "$PPATH_HISTORY/$IP_FIXTURE"
"$REAL_GIT" -C "$PPATH_HISTORY" add -- "$IP_FIXTURE"
"$REAL_GIT" -C "$PPATH_HISTORY" commit -q -m add-path
PPATH_COMMIT="$("$REAL_GIT" -C "$PPATH_HISTORY" rev-parse HEAD)"
"$REAL_GIT" -C "$PPATH_HISTORY" rm -q -- "$IP_FIXTURE"
"$REAL_GIT" -C "$PPATH_HISTORY" commit -q -m remove-path
expect_path_finding public-history-only-filename ipv4 "$IP_FIXTURE" "$PPATH_COMMIT" bash "$PPATH_HISTORY/scripts/validate-public.sh"

PENCODED_HISTORY="$TMP/public-encoded-path-history"; cp -R "$PUBLIC" "$PENCODED_HISTORY"
printf 'neutral\n' > "$PENCODED_HISTORY/$BASE64_IP"
"$REAL_GIT" -C "$PENCODED_HISTORY" add -- "$BASE64_IP"
"$REAL_GIT" -C "$PENCODED_HISTORY" commit -q -m add-encoded-path
PENCODED_COMMIT="$("$REAL_GIT" -C "$PENCODED_HISTORY" rev-parse HEAD)"
"$REAL_GIT" -C "$PENCODED_HISTORY" rm -q -- "$BASE64_IP"
"$REAL_GIT" -C "$PENCODED_HISTORY" commit -q -m remove-encoded-path
expect_path_finding public-history-encoded-filename ipv4 "$IP_FIXTURE" "$PENCODED_COMMIT" bash "$PENCODED_HISTORY/scripts/validate-public.sh"

PCONTROL_HISTORY="$TMP/public-control-path-history"; cp -R "$PUBLIC" "$PCONTROL_HISTORY"
printf 'neutral\n' > "$PCONTROL_HISTORY/$CONTROL_BIDI_NAME"
"$REAL_GIT" -C "$PCONTROL_HISTORY" add -- "$CONTROL_BIDI_NAME"
"$REAL_GIT" -C "$PCONTROL_HISTORY" commit -q -m add-control-path
PCONTROL_COMMIT="$("$REAL_GIT" -C "$PCONTROL_HISTORY" rev-parse HEAD)"
"$REAL_GIT" -C "$PCONTROL_HISTORY" rm -q -- "$CONTROL_BIDI_NAME"
"$REAL_GIT" -C "$PCONTROL_HISTORY" commit -q -m remove-control-path
expect_path_finding public-history-control path-control-character "$CONTROL_BIDI_NAME" "$PCONTROL_COMMIT" bash "$PCONTROL_HISTORY/scripts/validate-public.sh"

PSYMLINK="$TMP/public-symlink"; cp -R "$PUBLIC" "$PSYMLINK"; ln -s README.md "$PSYMLINK/linked"
expect_crash public-current-symlink bash "$PSYMLINK/scripts/validate-public.sh"
PHARDLINK="$TMP/public-hardlink"; cp -R "$PUBLIC" "$PHARDLINK"; ln "$PHARDLINK/README.md" "$PHARDLINK/linked"
expect_crash public-current-hardlink bash "$PHARDLINK/scripts/validate-public.sh"
PHISTORY_SYMLINK="$TMP/public-history-symlink"; cp -R "$PUBLIC" "$PHISTORY_SYMLINK"
ln -s README.md "$PHISTORY_SYMLINK/linked"
"$REAL_GIT" -C "$PHISTORY_SYMLINK" add linked
"$REAL_GIT" -C "$PHISTORY_SYMLINK" commit -q -m add-symlink
rm "$PHISTORY_SYMLINK/linked"
"$REAL_GIT" -C "$PHISTORY_SYMLINK" add -u
"$REAL_GIT" -C "$PHISTORY_SYMLINK" commit -q -m remove-symlink
expect_crash public-history-symlink bash "$PHISTORY_SYMLINK/scripts/validate-public.sh"

echo "security mutation tests passed"
