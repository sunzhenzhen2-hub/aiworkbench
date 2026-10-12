#!/usr/bin/env bash
# 把本机已安装的完整 Cursor 远程服务端同步到离线目录（跳过残缺下载）。
set -euo pipefail

SRC="${CURSOR_SERVER_SRC:-$HOME/.cursor-server}"
DST="${CURSOR_OFFLINE_DIR:-/home/workbench/offline/cursor-remote}"
MIN_BYTES="${CURSOR_OFFLINE_MIN_BYTES:-100000000}"

if [[ ! -d "$SRC/bin/linux-x64" ]]; then
  echo "找不到本机 Cursor 远程目录: $SRC/bin/linux-x64" >&2
  exit 1
fi

mkdir -p "$DST/bin/linux-x64" "$DST/extensions"

copied=0
for dir in "$SRC/bin/linux-x64"/*; do
  [[ -d "$dir" ]] || continue
  commit="$(basename "$dir")"
  size="$(du -sb "$dir" | awk '{print $1}')"
  if [[ "$size" -lt "$MIN_BYTES" ]]; then
    echo "跳过残缺版本 $commit (${size} 字节)"
    continue
  fi
  echo "同步版本 $commit"
  rsync -a --delete "$dir/" "$DST/bin/linux-x64/$commit/"
  copied=$((copied + 1))
  if [[ -f "$SRC/cursor-$commit" ]]; then
    rsync -a "$SRC/cursor-$commit" "$DST/"
  fi
done

if [[ -d "$SRC/extensions" ]]; then
  echo "同步 extensions"
  rsync -a "$SRC/extensions/" "$DST/extensions/"
fi

if [[ "$copied" -eq 0 ]]; then
  echo "没有完整版本可同步" >&2
  exit 1
fi

echo "离线包已更新: $DST （完整版本 ${copied} 套）"
du -sh "$DST" "$DST/bin/linux-x64"/* 2>/dev/null || true
