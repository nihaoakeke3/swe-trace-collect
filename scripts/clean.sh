#!/usr/bin/env bash
# Disk hygiene: delete workspaces (cloned repos + per-task venvs) of tasks
# that reached a terminal status. Traces/results/corpus are always kept.
# Default is a dry-run preview; pass --apply to actually delete.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1
[ "${1:-}" = "--all" ] && APPLY=2

total=0
for ws in runs/*/tasks/*/workspace; do
  [ -d "$ws" ] || continue
  task_dir=$(dirname "$ws")
  result="$task_dir/result.json"
  status=$(python3 - "$result" <<'EOF' 2>/dev/null || echo none
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("status", "none"))
except Exception:
    print("none")
EOF
)
  if [ "$APPLY" -eq 2 ]; then
    size=$(du -sb "$ws" 2>/dev/null | cut -f1)
    echo "purging (all): $ws ($((size/1024/1024)) MB, status=$status)"
    total=$((total + size))
    [ "$APPLY" = "2" ] && rm -rf "$ws"
    continue
  fi
  case "$status" in
    done|empty_trace|skipped)
      size=$(du -sb "$ws" 2>/dev/null | cut -f1)
      if [ "$APPLY" = "1" ]; then
        rm -rf "$ws"; echo "removed: $ws ($((size/1024/1024)) MB, status=$status)"
      else
        echo "would remove: $ws ($((size/1024/1024)) MB, status=$status)"
      fi
      total=$((total + size))
      ;;
    *) echo "keeping: $ws (status=$status)" ;;
  esac
done
echo "---"
if [ "$APPLY" = "0" ]; then echo "dry run: $((total/1024/1024)) MB reclaimable (use --apply / --all)"; else
  echo "freed ~$((total/1024/1024)) MB"; fi
