#!/usr/bin/env bash
# Create isolated venvs and install vLLM (latest stable) + proxy tooling.
# Safe to re-run: skips completed steps.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p venvs logs runs

# ---------- load config ----------
set -a; source "$ROOT/config.env"; set +a

TOOLS_VENV="$ROOT/venvs/tools"
VLLM_VENV="$ROOT/venvs/vllm"
VLLM_VERSION="${VLLM_VERSION:-0.29.0}"

echo "=== [1/3] tools venv (proxy/dataset/driver) ==="
if [ ! -x "$TOOLS_VENV/bin/python" ]; then
  if python3 -m venv "$TOOLS_VENV" 2>/dev/null; then
    echo "created tools venv via python3 -m venv"
  else
    echo "python3 -m venv unavailable, falling back to conda"
    /home/jinshuai/miniconda3/bin/conda create -y -p "$TOOLS_VENV" python=3.12 \
      || { echo "FATAL: cannot create tools venv"; exit 1; }
  fi
fi
"$TOOLS_VENV/bin/python" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
"$TOOLS_VENV/bin/python" -m pip install -q aiohttp pyarrow || {
  "$TOOLS_VENV/bin/python" -m pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple aiohttp pyarrow; }
"$TOOLS_VENV/bin/python" -c "import aiohttp, pyarrow; print('tools venv OK: aiohttp', aiohttp.__version__, '| pyarrow', pyarrow.__version__)"

echo "=== [2/3] vllm venv ($VLLM_VERSION) — large download, be patient ==="
if [ ! -x "$VLLM_VENV/bin/python" ]; then
  if python3 -m venv "$VLLM_VENV" 2>/dev/null; then
    echo "created vllm venv via python3 -m venv"
  else
    echo "python3 -m venv unavailable, falling back to conda"
    /home/jinshuai/miniconda3/bin/conda create -y -p "$VLLM_VENV" python=3.12 \
      || { echo "FATAL: cannot create vllm venv"; exit 1; }
  fi
fi
"$VLLM_VENV/bin/python" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
if ! "$VLLM_VENV/bin/python" -c "import vllm" 2>/dev/null; then
  if ! "$VLLM_VENV/bin/python" -m pip install "vllm==${VLLM_VERSION}"; then
    echo "vllm==${VLLM_VERSION} failed on pypi.org; trying tsinghua mirror"
    "$VLLM_VENV/bin/python" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "vllm==${VLLM_VERSION}" \
      || "$VLLM_VENV/bin/python" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple vllm
  fi
fi
"$VLLM_VENV/bin/python" -c "import vllm, transformers, torch; print('vllm venv OK: vllm', vllm.__version__, '| transformers', transformers.__version__, '| torch', torch.__version__)"

echo "=== [3/3] claude CLI check ==="
if [ -x "$CLAUDE_BIN" ]; then
  "$CLAUDE_BIN" --version
else
  echo "WARNING: claude CLI not found at $CLAUDE_BIN"
fi

echo "SETUP_ALL_OK"
