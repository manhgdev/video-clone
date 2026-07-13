#!/usr/bin/env bash
# Restore file from .bk3 then .bk2 then .bk1
set -euo pipefail
if [ $# -lt 1 ]; then
  echo "usage: $0 <file>" >&2
  exit 1
fi
f=$1
dir=$(cd "$(dirname "$f")" && pwd)
base=$(basename "$f")
cd "$dir"
for b in bk3 bk2 bk1; do
  if [ -f "${base}.$b" ]; then
    cp -p "${base}.$b" "$base"
    echo "restored $base from ${base}.$b"
    exit 0
  fi
done
echo "no backup for $f" >&2
exit 1
