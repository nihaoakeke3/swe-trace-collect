#!/usr/bin/env bash
# End-to-end smoke: hello task + the 3 smoke SWE tasks, then corpus build.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
set -a; source "$ROOT/config.env"; set +a
RUNDIR="$ROOT/runs/$RUN_ID"
mkdir -p "$RUNDIR"

bash "$ROOT/scripts/start_proxy.sh" || exit 1

echo "=== hello task ==="
"$ROOT/venvs/tools/bin/python" -m freetoken_trace.orchestrator \
  --root "$ROOT" --stage hello 2>&1 | tee -a "$RUNDIR/smoke.log" || exit 1

echo "=== smoke SWE tasks ==="
"$ROOT/venvs/tools/bin/python" -m freetoken_trace.orchestrator \
  --root "$ROOT" --stage smoke 2>&1 | tee -a "$RUNDIR/smoke.log"

echo "=== corpus (smoke tasks) ==="
"$ROOT/venvs/vllm/bin/python" -m freetoken_trace.corpus \
  --root "$ROOT" --run-dir "$RUNDIR" || true

echo "SMOKE_DONE — inspect $RUNDIR before launching the batch"
