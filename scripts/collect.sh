#!/usr/bin/env bash
# Batch collection inside a tmux controller session (survives disconnects).
# Resumable: rerun this script to continue where it left off.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
set -a; source "$ROOT/config.env"; set +a
RUNDIR="$ROOT/runs/$RUN_ID"
LOG="$RUNDIR/orchestrator.log"
mkdir -p "$RUNDIR"

SESS=swe-trace-ctl
if tmux has-session -t "$SESS" 2>/dev/null; then
  echo "controller session '$SESS' already running (attach: tmux attach -t $SESS)"; exit 0
fi
bash "$ROOT/scripts/start_proxy.sh" || exit 1

tmux new-session -d -s "$SESS" \
  "bash $ROOT/scripts/serve.sh >> $ROOT/logs/ctl.log 2>&1; \
   $ROOT/venvs/tools/bin/python -m freetoken_trace.orchestrator \
     --root $ROOT --stage batch 2>&1 | tee -a $LOG; \
   echo ORCHESTRATOR_DONE >> $LOG"
echo "controller launched in tmux session '$SESS' — log: $LOG"
