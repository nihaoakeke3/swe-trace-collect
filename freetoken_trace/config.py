"""Central configuration: parses config.env (the single source of truth)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


@dataclass
class Config:
    root: Path
    run_id: str
    model_path: str
    served_model_name: str
    vllm_python: str
    gpu_id: int
    vllm_port: int
    max_model_len: int
    gpu_mem_util: float
    max_num_seqs: int
    tool_call_parser: str
    proxy_port: int
    proxy_upstream: str
    ping_interval_s: float
    upstream_sock_read_timeout_s: float
    claude_bin: str
    max_turns: int
    max_context_tokens: int
    max_output_tokens: int
    silence_timeout_min: int
    wall_timeout_min: int
    api_timeout_ms: int
    hf_mirror: str
    dataset_repo: str
    num_tasks: int
    smoke_tasks: int
    cleanup_workspace: bool

    @property
    def vllm_base(self) -> str:
        return f"http://127.0.0.1:{self.vllm_port}"

    @property
    def proxy_base(self) -> str:
        return f"http://127.0.0.1:{self.proxy_port}"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    def run_dir(self, run_id: str | None = None) -> Path:
        return self.runs_dir / (run_id or self.run_id)

    def task_dir(self, run_id: str, task_id: str) -> Path:
        return self.run_dir(run_id) / "tasks" / task_id


def load_config(root: str | Path | None = None) -> Config:
    root_p = Path(
        root
        or os.environ.get("STC_ROOT")
        or Path(__file__).resolve().parent.parent
    )
    raw = _parse_env(root_p / "config.env")
    g = raw.get
    return Config(
        root=root_p,
        run_id=g("RUN_ID", "r1"),
        model_path=g("MODEL_PATH", ""),
        served_model_name=g("SERVED_MODEL_NAME", "served-model"),
        vllm_python=g("VLLM_PYTHON", "python"),
        gpu_id=int(g("GPU_ID", "1")),
        vllm_port=int(g("VLLM_PORT", "1923")),
        max_model_len=int(g("MAX_MODEL_LEN", "262144")),
        gpu_mem_util=float(g("GPU_MEM_UTIL", "0.92")),
        max_num_seqs=int(g("MAX_NUM_SEQS", "16")),
        tool_call_parser=g("TOOL_CALL_PARSER", "hermes"),
        proxy_port=int(g("PROXY_PORT", "1924")),
        proxy_upstream=g("PROXY_UPSTREAM", "http://127.0.0.1:1923"),
        ping_interval_s=float(g("PING_INTERVAL_S", "15")),
        upstream_sock_read_timeout_s=float(g("UPSTREAM_SOCK_READ_TIMEOUT_S", "660")),
        claude_bin=g("CLAUDE_BIN", "claude"),
        max_turns=int(g("MAX_TURNS", "60")),
        max_context_tokens=int(g("MAX_CONTEXT_TOKENS", "200000")),
        max_output_tokens=int(g("MAX_OUTPUT_TOKENS", "32000")),
        silence_timeout_min=int(g("SILENCE_TIMEOUT_MIN", "30")),
        wall_timeout_min=int(g("WALL_TIMEOUT_MIN", "150")),
        api_timeout_ms=int(g("API_TIMEOUT_MS", "600000")),
        hf_mirror=g("HF_MIRROR", "https://hf-mirror.com"),
        dataset_repo=g("DATASET_REPO", "ScaleAI/SWE-bench_Pro"),
        num_tasks=int(g("NUM_TASKS", "20")),
        smoke_tasks=int(g("SMOKE_TASKS", "3")),
        cleanup_workspace=bool(int(g("CLEANUP_WORKSPACE", "1"))),
    )
