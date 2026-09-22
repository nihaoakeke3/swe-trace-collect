#!/usr/bin/env bash
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
set -a; source "$ROOT/config.env"; set +a
RUNDIR="$ROOT/runs/$RUN_ID"
if [ -f "$RUNDIR/proxy.pid" ]; then
  PID=$(cat "$RUNDIR/proxy.pid")
  kill "$PID" 2>/dev/null && echo "proxy $PID stopped"
  rm -f "$RUNDIR/proxy.pid"
else
  echo "no proxy.pid for run $RUN_ID"
fi
