#!/usr/bin/env bash
# Start the capture proxy for run $RUN_ID (persistent across tasks).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
set -a; source "$ROOT/config.env"; set +a
RUNDIR="$ROOT/runs/$RUN_ID"
LOG="$ROOT/logs/proxy-$RUN_ID.log"
mkdir -p "$RUNDIR" "$ROOT/logs"

if curl -sf "http://127.0.0.1:$PROXY_PORT/health" >/dev/null 2>&1; then
  echo "proxy already healthy on $PROXY_PORT"; exit 0
fi
nohup "$ROOT/venvs/tools/bin/python" -m freetoken_trace.proxy \
  --root "$ROOT" --run-dir "$RUNDIR" > "$LOG" 2>&1 &
echo $! > "$RUNDIR/proxy.pid"
for i in $(seq 1 30); do
  sleep 1
  curl -sf "http://127.0.0.1:$PROXY_PORT/health" >/dev/null 2>&1 && {
    echo "PROXY_OK port=$PROXY_PORT pid=$(cat "$RUNDIR/proxy.pid")"; exit 0; }
done
echo "PROXY_FAILED see $LOG"; exit 1
