#!/usr/bin/env bash
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
set -a; source "$ROOT/config.env"; set +a
RUNDIR="$ROOT/runs/$RUN_ID"
if [ -f "$RUNDIR/serve.pid" ]; then
  PID=$(cat "$RUNDIR/serve.pid")
  kill "$PID" 2>/dev/null && echo "sent TERM to $PID"
  for i in $(seq 1 24); do kill -0 "$PID" 2>/dev/null || break; sleep 5; done
  kill -KILL "$PID" 2>/dev/null
  rm -f "$RUNDIR/serve.pid"
  echo "vLLM stopped"
else
  echo "no serve.pid for run $RUN_ID"
fi
