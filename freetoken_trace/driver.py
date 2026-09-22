"""Per-task driver: launches `claude -p` inside a dedicated tmux session,
monitors liveness (silence / wall-clock timeouts), tears down cleanly, and
writes result.json + a normalized events.jsonl from the stream-json output.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

from .claude_env import build_prompt, build_task_env
from .config import Config
from .dataset import safe_task_id

log = logging.getLogger("driver")


def _sh(cmd: str, timeout: int = 60) -> str:
    return subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True,
                          timeout=timeout).stdout.strip()


def _session_name(cfg: Config, task_id: str) -> str:
    return f"swe-trace-{cfg.run_id}-{task_id[:60]}"


def _set_proxy_task(cfg: Config, task_id: str) -> None:
    try:
        req = urllib.request.Request(
            cfg.proxy_base + "/__task",
            data=json.dumps({"task_id": task_id}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
    except Exception as exc:  # noqa: BLE001
        log.error("failed to set proxy current task: %r", exc)


def _proxy_status(cfg: Config) -> dict:
    try:
        with urllib.request.urlopen(cfg.proxy_base + "/__status", timeout=10) as r:
            return json.loads(r.read())
    except Exception:  # noqa: BLE001
        return {}


def _write_launch_files(cfg: Config, task: dict, task_dir: Path) -> None:
    ws = task_dir / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    build_task_env(cfg, task_dir)

    prompt = task.get("prompt_override") or build_prompt(task)
    (task_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

    launch = f"""#!/usr/bin/env bash
set -u
cd "{ws}" || exit 97
source "{task_dir / 'env.sh'}"
"{cfg.claude_bin}" -p "$(cat "{task_dir / 'prompt.txt'}")" \\
  --output-format stream-json --verbose \\
  --dangerously-skip-permissions \\
  --max-turns {cfg.max_turns} \\
  > "{task_dir / 'stream.jsonl'}" 2> "{task_dir / 'claude.stderr.log'}"
echo $? > "{task_dir / 'exit_code.txt'}"
"""
    p = task_dir / "launch.sh"
    p.write_text(launch, encoding="utf-8")
    p.chmod(0o755)


def _kill_session(session: str) -> None:
    pane_pid = _sh(f"tmux list-panes -t {session} -F '#{{pane_pid}}' 2>/dev/null")
    if pane_pid:
        subprocess.run(["bash", "-lc", f"kill -TERM -- -{pane_pid} 2>/dev/null; "
                                       f"kill -TERM {pane_pid} 2>/dev/null"], timeout=30)
        time.sleep(3)
        subprocess.run(["bash", "-lc", f"kill -KILL -- -{pane_pid} 2>/dev/null"], timeout=30)
    _sh(f"tmux kill-session -t {session} 2>/dev/null")


def run_task(cfg: Config, run_dir: Path, task: dict) -> dict:
    """Runs one task end-to-end; returns the result dict (also result.json)."""
    task_id = safe_task_id(task["task_id"])
    task_dir = run_dir / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    session = _session_name(cfg, task_id)

    _write_launch_files(cfg, task, task_dir)
    _set_proxy_task(cfg, task_id)
    _sh(f"tmux kill-session -t {session} 2>/dev/null")
    for stale in ("exit_code.txt", "stream.jsonl"):
        p = task_dir / stale
        if p.exists():
            p.unlink()

    status_before = _proxy_status(cfg).get("calls_total", 0)
    t0 = time.time()
    _sh(f'tmux new-session -d -s "{session}" -c "{task_dir}" '
        f'"bash {task_dir / "launch.sh"}"')
    log.info("[%s] claude launched in tmux session %s", task_id, session)

    stream_path = task_dir / "stream.jsonl"
    exit_path = task_dir / "exit_code.txt"
    last_size = 0
    last_change = time.time()
    status = "running"
    while True:
        time.sleep(20)
        if exit_path.exists():
            status = "done"
            break
        size = stream_path.stat().st_size if stream_path.exists() else 0
        if size != last_size:
            last_size = size
            last_change = time.time()
        elif time.time() - last_change > cfg.silence_timeout_min * 60 and size == last_size and last_size > 0:
            status = "timeout_silence"
            break
        elif time.time() - last_change > cfg.silence_timeout_min * 60:
            # nothing ever written: claude failed to start
            status = "no_output"
            break
        if time.time() - t0 > cfg.wall_timeout_min * 60:
            status = "timeout_wall"
            break
        if not _sh(f"tmux has-session -t {session} 2>/dev/null && echo alive"):
            status = "crashed"
            break

    wall_s = time.time() - t0
    if status != "done":
        _kill_session(session)

    exit_code = None
    if exit_path.exists():
        try:
            exit_code = int(exit_path.read_text().strip())
        except ValueError:
            pass

    parsed = _parse_stream(task_dir)
    proxy_status = _proxy_status(cfg)
    n_calls = proxy_status.get("calls_total", 0) - status_before

    trace_calls = task_dir / "trace" / "calls.jsonl"
    n_trace = _count_llm_calls(trace_calls)
    if status == "done" and n_trace == 0:
        status = "empty_trace"

    result = {
        "task_id": task_id,
        "status": status,
        "exit_code": exit_code,
        "wall_s": round(wall_s, 1),
        "session_id": parsed.get("session_id"),
        "num_turns": parsed.get("num_turns"),
        "cost_usd": parsed.get("cost_usd"),
        "is_error": parsed.get("is_error"),
        "n_api_retry_events": parsed.get("n_api_retry"),
        "n_proxy_calls_delta": n_calls,
        "n_trace_llm_calls": n_trace,
        "compact_events": parsed.get("compact_events", 0),
    }
    (task_dir / "result.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    log.info("[%s] finished: %s (%.0fs, %d trace calls)", task_id, status, wall_s, n_trace)
    return result


def _count_llm_calls(path: Path) -> int:
    if not path.exists():
        return 0
    n = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                if json.loads(line).get("record_type") == "llm_call":
                    n += 1
            except Exception:  # noqa: BLE001
                continue
    return n


def _parse_stream(task_dir: Path) -> dict:
    """Extract normalized facts from stream.jsonl + write events.jsonl."""
    out: dict = {"session_id": None, "num_turns": None, "cost_usd": None,
                 "is_error": None, "n_api_retry": 0, "compact_events": 0}
    if not (task_dir / "stream.jsonl").exists():
        return out
    events: list[dict] = []
    pending_tools: dict[str, tuple[str, float]] = {}  # tool_use_id -> (name, ts)
    tool_pairs = []
    n_assistant = 0
    with open(task_dir / "stream.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            et = ev.get("type")
            if et == "system" and ev.get("subtype") == "init":
                out["session_id"] = (ev.get("session_id"))
                events.append({"event": "init", "session_id": ev.get("session_id"),
                               "model": ev.get("model")})
            elif et == "system" and ev.get("subtype") == "api_retry":
                out["n_api_retry"] += 1
                events.append({"event": "api_retry",
                               "attempt": ev.get("attempt"),
                               "error_status": ev.get("error_status"),
                               "retry_delay_ms": ev.get("retry_delay_ms")})
            elif et == "assistant":
                n_assistant += 1
                msg = ev.get("message") or {}
                puid = ev.get("parent_tool_use_id")
                for block in (msg.get("content") or []):
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        pending_tools[block.get("tool_use_id") or ""] = (
                            block.get("name"), time.time())
                if puid:
                    events.append({"event": "subagent_assistant",
                                   "parent_tool_use_id": puid})
            elif et == "user":
                msg = ev.get("message") or {}
                content = msg.get("content")
                blocks = content if isinstance(content, list) else []
                for block in blocks:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tid = block.get("tool_use_id")
                        name, t0 = pending_tools.pop(tid, ("unknown", None))
                        dur = None if t0 is None else round(time.time() - t0, 3)
                        tool_pairs.append({"tool_name": name, "tool_use_id": tid,
                                           "duration_s_approx": dur,
                                           "is_subagent_tool": bool(ev.get("parent_tool_use_id"))})
            elif et == "result":
                out["session_id"] = ev.get("session_id") or out["session_id"]
                out["num_turns"] = ev.get("num_turns") or ev.get("turns")
                out["cost_usd"] = ev.get("total_cost_usd")
                out["is_error"] = ev.get("is_error")
                events.append({"event": "result", "subtype": ev.get("subtype"),
                               "is_error": ev.get("is_error"),
                               "num_turns": ev.get("num_turns"),
                               "cost_usd": ev.get("total_cost_usd")})
            raw = line
            if '"isCompactSummary":true' in raw or '"isCompactSummary": true' in raw:
                out["compact_events"] += 1
                events.append({"event": "compact"})
    # events.jsonl: normalized + tool pairing (proxy gaps remain authoritative)
    with open(task_dir / "events.jsonl", "w", encoding="utf-8") as f:
        for t in tool_pairs:
            f.write(json.dumps({"event": "tool_pair", **t}, ensure_ascii=False) + "\n")
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    out["n_assistant_events"] = n_assistant
    return out


def cleanup_workspace(task_dir: Path) -> int:
    """Remove the workspace dir of a finished task; returns freed bytes."""
    ws = task_dir / "workspace"
    if not ws.exists():
        return 0
    size = sum(f.stat().st_size for f in ws.rglob("*") if f.is_file())
    shutil.rmtree(ws, ignore_errors=True)
    return size


__all__ = ["run_task", "cleanup_workspace", "build_prompt", "safe_task_id"]
