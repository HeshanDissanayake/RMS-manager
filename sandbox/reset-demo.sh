#!/usr/bin/env bash
# Build (or rebuild) the sandbox: a real git "root" repo with source repos as
# submodules and one branch per thread, from the tracked template.
#
#   sandbox/demo/
#   ├── origins/<repo>.git   bare "remote" source repos (stand-ins for llvm, spike, ...)
#   └── root/                the project ("the root"): rms.json, threads/, src/<repo> submodules
#
# sandbox/demo/ is ignored by RMS-manager's git; run this again to start over.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RMS_HOME="$(cd "$HERE/.." && pwd)"
RMS="$RMS_HOME/.venv/bin/rms"
TEMPLATE="$HERE/demo-project"
DEMO="${RMS_DEMO_DIR:-$HERE/demo}"
SOURCES=(fastlib simulator)

[ -x "$RMS" ] || { echo "rms not installed: python3 -m venv .venv && .venv/bin/pip install -e ." >&2; exit 1; }

g() { git -c user.name="RMS sandbox" -c user.email="sandbox@localhost" -c protocol.file.allow=always "$@"; }

rm -rf "$DEMO"
mkdir -p "$DEMO/origins"

# 1. source repos, each with one commit on main
for repo in "${SOURCES[@]}"; do
  work="$DEMO/.seed-$repo"
  mkdir -p "$work"
  g -C "$work" init -q -b main
  echo "# $repo (sandbox source repo)" > "$work/README.md"
  printf 'def version():\n    return "%s 1.0"\n' "$repo" > "$work/$repo.py"
  g -C "$work" add -A
  g -C "$work" commit -q -m "$repo: initial"
  g clone -q --bare "$work" "$DEMO/origins/$repo.git"
  rm -rf "$work"
done

# 2. the root repo from the template
ROOT="$DEMO/root"
mkdir -p "$ROOT"
(cd "$TEMPLATE" && tar --exclude=./store --exclude=./.claude -cf - .) | (cd "$ROOT" && tar -xf -)
g -C "$ROOT" init -q -b main
printf 'store/\n.claude/settings.local.json\n' > "$ROOT/.gitignore"
for repo in "${SOURCES[@]}"; do
  g -C "$ROOT" submodule --quiet add "$DEMO/origins/$repo.git" "src/$repo"
  # record only: never check out on update (LLVM-regsw R21)
  g -C "$ROOT" config -f .gitmodules "submodule.src/$repo.update" none
done
"$RMS" install-hooks "$ROOT" >/dev/null
# T00's shared base build: record its real source commit
FASTLIB_SHA="$(git -C "$DEMO/origins/fastlib.git" rev-parse main)"
sed -i "s/(set by reset-demo.sh)/$FASTLIB_SHA/" "$ROOT/threads/T00-main/store.json"
g -C "$ROOT" add -A
g -C "$ROOT" commit -q -m "Sandbox root: threads, rms.json, hooks, source submodules

Thread: T00"

# 3. one branch per open thread (T00 is main)
for t in "$ROOT"/threads/T*/; do
  id="$(basename "$t" | cut -d- -f1)"
  [ "$id" = "T00" ] && continue
  g -C "$ROOT" branch "thread/$id"
done

# 4. the store (not in git): a shared build, some data, old data of the done thread T03
B="$ROOT/store/builds/fastlib/base"
mkdir -p "$B/lib"
printf 'CMAKE_HOME_DIRECTORY:INTERNAL=%s\nCMAKE_BUILD_TYPE:STRING=Release\n' "$ROOT/src/fastlib" > "$B/CMakeCache.txt"
head -c 200000 /dev/urandom > "$B/lib/libfastlib.a"
mkdir -p "$ROOT/store/data/T01/EXP-T01-01" "$ROOT/store/data/T03/EXP-T03-01"
echo "n,time_ms" > "$ROOT/store/data/T01/EXP-T01-01/results.csv"
head -c 500000 /dev/urandom > "$ROOT/store/data/T03/EXP-T03-01/raw.bin"
touch -d '60 days ago' "$ROOT/store/data/T03/EXP-T03-01/raw.bin" "$ROOT/store/data/T03/EXP-T03-01" "$ROOT/store/data/T03"

echo "sandbox ready: $ROOT"
g -C "$ROOT" branch --list
g -C "$ROOT" submodule status
"$RMS" doctor "$ROOT" | tail -3
