#!/usr/bin/env bash
# Rotate .bk1 (oldest) → .bk2 → .bk3 (newest pre-edit), then copy file → .bk3
set -euo pipefail
if [ $# -lt 1 ]; then
  echo "usage: $0 <file> [file...]" >&2
  exit 1
fi
for f in "$@"; do
  if [ ! -f "$f" ]; then
    echo "skip missing: $f" >&2
    continue
  fi
  dir=$(cd "$(dirname "$f")" && pwd)
  base=$(basename "$f")
  cd "$dir"
  if [ -f "${base}.bk3" ]; then
    rm -f "${base}.bk1"
    [ -f "${base}.bk2" ] && mv "${base}.bk2" "${base}.bk1"
    mv "${base}.bk3" "${base}.bk2"
  fi
  cp -p "$base" "${base}.bk3"
  echo "backed up → ${dir}/${base}.bk3"
done
