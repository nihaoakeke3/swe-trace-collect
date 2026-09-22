#!/usr/bin/env bash
# Start vLLM on GPU 1 serving the AWQ-NVFP4 model, with automatic
# --max-model-len fallback based on the measured KV cache capacity.
# Never touches GPU 0 and never kills foreign processes.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
set -a; source "$ROOT/config.env"; set +a

RUNDIR="$ROOT/runs/$RUN_ID"
LOG="$ROOT/logs/serve-$RUN_ID.log"
mkdir -p "$RUNDIR" "$ROOT/logs"

if [ -f "$RUNDIR/serve.pid" ] && kill -0 "$(cat "$RUNDIR/serve.pid")" 2>/dev/null; then
  echo "vLLM already running (pid $(cat "$RUNDIR/serve.pid"))"; exit 0
fi

# GPU guard: refuse to start if GPU $GPU_ID is occupied by someone else
USED=$(nvidia-smi --id="$GPU_ID" --query-gpu=memory.used --format=csv,noheader,nounits | tr -d ' ')
if [ "${USED:-0}" -gt 2000 ]; then
  echo "REFUSING: GPU $GPU_ID already has ${USED} MiB in use (gpu_guard)."; exit 1
fi

for MML in "$MAX_MODEL_LEN" 196608 131072; do
  echo "=== starting vLLM with --max-model-len $MML (log: $LOG) ==="
  CUDA_VISIBLE_DEVICES="$GPU_ID" nohup "$ROOT/venvs/vllm/bin/vllm" serve "$MODEL_PATH" \
    --served-model-name "$SERVED_MODEL_NAME" \
    --host 127.0.0.1 --port "$VLLM_PORT" \
    --max-model-len "$MML" \
    --gpu-memory-utilization "$GPU_MEM_UTIL" \
    --max-num-seqs "$MAX_NUM_SEQS" \
    --enable-auto-tool-choice --tool-call-parser "$TOOL_CALL_PARSER" \
    --enable-prefix-caching \
    --trust-remote-code \
    > "$LOG" 2>&1 &
  echo $! > "$RUNDIR/serve.pid"

  # wait for health (model load + graph capture can take minutes)
  ok=""
  for i in $(seq 1 240); do
    sleep 5
    if curl -sf "http://127.0.0.1:$VLLM_PORT/health" >/dev/null 2>&1; then ok=1; break; fi
    if ! kill -0 "$(cat "$RUNDIR/serve.pid")" 2>/dev/null; then echo "server process died; see $LOG"; break; fi
  done
  [ -z "$ok" ] && { echo "startup failed with max-model-len=$MML"; continue; }

  CAP=$(grep -oE 'GPU KV cache size: [0-9,]+ tokens' "$LOG" | tail -1 | grep -oE '[0-9,]+' | tr -d ',')
  echo "KV cache capacity: ${CAP:-unknown} tokens (max-model-len=$MML)"
  if [ -n "${CAP:-}" ] && [ "$CAP" -lt "$MML" ]; then
    echo "capacity < max-model-len; restarting smaller"
    kill "$(cat "$RUNDIR/serve.pid")" 2>/dev/null; sleep 10
    continue
  fi
  grep -E 'Maximum concurrency' "$LOG" | tail -2 || true
  echo "SERVE_OK port=$VLLM_PORT model=$SERVED_MODEL_NAME max_model_len=$MML kv_capacity=${CAP:-unknown}"
  exit 0
done
echo "SERVE_FAILED"; exit 1
