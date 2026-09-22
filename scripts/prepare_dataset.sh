#!/usr/bin/env bash
# Download the SWE-bench Pro parquet via hf-mirror and build the task list.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
"$ROOT/venvs/tools/bin/python" -m freetoken_trace.dataset --root "$ROOT" \
  --num "${NUM_TASKS:-20}" ${SMOKE_N:+--smoke-n "$SMOKE_N"} "$@"
