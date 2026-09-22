"""Build the replay corpus from captured traces.

Inputs : runs/<run_id>/tasks/<task>/trace/calls.jsonl (+ bodies/*.gz)
Outputs: runs/<run_id>/corpus/
  cc_traces.jsonl    one session record per agent (main or subagent), the
                     exact schema consumed by ft-agentx-bench replay_cc.py:
                     {id, models, block_size, hash_id_scope,
                      requests:[{t, model, in, out, hash_ids, api_time, type, ttft}]}
                     t is aligned to the task start, so the recorded wall-clock
                     overlap between the main agent and its subagents (true
                     concurrency) survives into replay.
  tuples.jsonl       (input_len, output_len, tool_gap) closed-loop replay units
  full_trace.jsonl   enriched per-call records (agent grouping, gaps, hashes)
  stats.json         distribution + concurrency statistics
  hash_registry.json 64-token-block hash table (prefix-cache structure)

Run with the vLLM venv python (has transformers for the tokenizer):
  venvs/vllm/bin/python -m freetoken_trace.corpus --run-dir runs/r1
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
from collections import defaultdict
from pathlib import Path

from .config import Config, load_config

log = logging.getLogger("corpus")


# ---------- prompt canonicalization ----------

def _block_text(block) -> str:
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return ""
    t = block.get("type")
    if t == "text":
        return str(block.get("text") or "")
    if t == "thinking":
        return "[thinking]" + str(block.get("thinking") or "")
    if t == "tool_use":
        return f"[tool_use:{block.get('name')}] " + json.dumps(block.get("input") or {}, ensure_ascii=False)
    if t == "tool_result":
        c = block.get("content")
        if isinstance(c, str):
            return "[tool_result] " + c
        if isinstance(c, list):
            return "[tool_result] " + " ".join(_block_text(b) for b in c)
        return "[tool_result]"
    if t == "image":
        return "[image]"
    return f"[{t}]"


def canonical_prompt(body: dict) -> str:
    parts: list[str] = []
    system = body.get("system")
    if isinstance(system, str):
        parts.append("system\n" + system)
    elif isinstance(system, list):
        parts.append("system\n" + "\n".join(_block_text(b) for b in system))
    for m in body.get("messages") or []:
        c = m.get("content")
        if isinstance(c, str):
            text = c
        elif isinstance(c, list):
            text = "\n".join(_block_text(b) for b in c)
        else:
            text = ""
        parts.append(f"{m.get('role')}\n{text}")
    tools = body.get("tools") or []
    if tools:
        parts.append("tools\n" + json.dumps(
            [{"name": t.get("name"), "description": t.get("description"),
              "schema": t.get("input_schema")} for t in tools],
            ensure_ascii=False))
    return "\n\n".join(parts)


# ---------- prefix hashing ----------

class BlockHasher:
    """Hashes the tokenized prompt in fixed-size blocks; identical prefixes
    across calls/sessions get identical hash_ids (the radix-cache structure).
    Also retains each block's decoded text so the l2 replayer can rebuild
    prompts from the block pool (blocks.jsonl)."""

    def __init__(self, tokenizer, block_size: int = 64):
        self.tok = tokenizer
        self.block_size = block_size
        self.registry: dict[str, int] = {}
        self.texts: dict[int, str] = {}

    def _block_id(self, token_ids: list[int], text: str) -> int:
        key = hashlib.md5(json.dumps(token_ids).encode()).hexdigest()
        if key not in self.registry:
            self.registry[key] = len(self.registry)
            self.texts[self.registry[key]] = text
        return self.registry[key]

    def hash_ids(self, text: str) -> list[int]:
        if self.tok is not None:
            ids = self.tok(text, add_special_tokens=False)["input_ids"]
            bs = self.block_size
            decode = True
        else:
            ids = [ord(c) for c in text]  # crude fallback: chars as tokens
            bs = self.block_size * 4  # ~4 chars/token heuristic
            decode = False
        out = []
        for i in range(0, len(ids), bs):
            chunk = ids[i:i + bs]
            if decode and self.tok is not None:
                block_text = self.tok.decode(chunk)
            elif decode:
                block_text = "".join(chr(c) for c in chunk)
            else:
                block_text = text[i:i + bs]
            out.append(self._block_id(list(chunk), block_text))
        return out


# ---------- loading ----------

def load_calls(task_dir: Path) -> list[dict]:
    f = task_dir / "trace" / "calls.jsonl"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if rec.get("record_type") == "llm_call" and rec.get("response", {}).get("status") == 200:
            out.append(rec)
    out.sort(key=lambda r: r["timing"]["ts_epoch_ns"])
    return out


def _agent_key(rec: dict) -> str:
    session = rec.get("session_id") or "nosession"
    agent = rec.get("agent_id") or "main"
    return f"{session}/{agent}"


def _is_subagent(rec: dict) -> bool:
    # recompute from identity fields (older records may carry a stale flag)
    return bool(rec.get("agent_id") or rec.get("parent_agent_id"))


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))
    return round(s[idx], 3)


# ---------- per-task corpus ----------

def build_task_corpus(task_dir: Path, task_id: str, served_model: str,
                      hasher: BlockHasher) -> tuple[list[dict], list[dict], list[dict]]:
    calls = load_calls(task_dir)
    if not calls:
        return [], [], []

    # hydrate request bodies for canonical prompt text
    for rec in calls:
        ref = rec.get("request", {}).get("body_ref")
        text = None
        if ref:
            p = task_dir / "trace" / ref
            if p.exists():
                try:
                    body = json.loads(gzip.open(p, "rb").read().decode("utf-8"))
                    rec["_prompt_text"] = canonical_prompt(body if isinstance(body, dict) else {})
                except Exception:  # noqa: BLE001
                    text = None
        if text is None and "_prompt_text" not in rec:
            rec.setdefault("_prompt_text", "")

    origin = min(r["timing"]["ts_epoch_ns"] for r in calls)

    agents: dict[str, list[dict]] = defaultdict(list)
    for r in calls:
        agents[_agent_key(r)].append(r)

    session_records: list[dict] = []
    tuples: list[dict] = []
    full: list[dict] = []

    for agent_key, rcalls in agents.items():
        requests = []
        prev_end = None
        for idx, r in enumerate(rcalls):
            timing = r["timing"]
            usage = r["response"].get("usage") or {}
            t = (timing["ts_epoch_ns"] - origin) / 1e9
            n_in = usage.get("prompt_tokens_total")
            if n_in is None:
                n_in = (usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0) \
                    + (usage.get("cache_creation_input_tokens") or 0)
            n_out = usage.get("output_tokens") or 0
            api_time = (timing.get("e2e_ms") or 0) / 1000.0
            ttft = (timing.get("ttft_ms") or 0) / 1000.0
            hids = hasher.hash_ids(r.get("_prompt_text") or "")
            gap = None if prev_end is None else round(t - prev_end, 6)
            prev_end = t + api_time

            requests.append({
                "t": round(t, 6),
                "model": served_model,
                "in": int(n_in),
                "out": int(n_out),
                "hash_ids": hids,
                "api_time": round(api_time, 6),
                "type": "s",
                "ttft": round(ttft, 6),
            })
            tuples.append({
                "task_id": task_id,
                "agent_key": agent_key,
                "call_idx": idx,
                "t_s": round(t, 6),
                "input_tokens": int(n_in),
                "output_tokens": int(n_out),
                "tool_gap_s": gap,
                "api_time_s": round(api_time, 6),
                "ttft_s": round(ttft, 6),
                "is_subagent": _is_subagent(r),
            })
            full.append({
                "task_id": task_id,
                "agent_key": agent_key,
                "call_idx": idx,
                "call_id": r.get("call_id"),
                "t_s": round(t, 6),
                "timing": {k: timing.get(k) for k in
                           ("ts_epoch_ns", "ts_monotonic_ns", "ttft_ms", "e2e_ms")},
                "request": {k: r.get("request", {}).get(k) for k in
                            ("model_requested", "stream", "max_tokens", "n_messages",
                             "n_tools", "body_ref", "body_sha256")},
                "response": {k: r.get("response", {}).get(k) for k in
                             ("id", "stop_reason", "content_types", "tool_uses", "usage")},
                "session_id": r.get("session_id"),
                "agent_id": r.get("agent_id"),
                "parent_agent_id": r.get("parent_agent_id"),
                "is_subagent": _is_subagent(r),
            })

        session_records.append({
            "id": f"{task_id}#{agent_key}",
            "models": [served_model],
            "block_size": hasher.block_size,
            "hash_id_scope": "local",
            "requests": requests,
        })

    return session_records, tuples, full


# ---------- stats ----------

def compute_stats(all_tuples: list[dict], all_sessions: list[dict]) -> dict:
    n_calls = len(all_tuples)
    n_sub = sum(1 for t in all_tuples if t["is_subagent"])
    ins = [float(t["input_tokens"]) for t in all_tuples]
    outs = [float(t["output_tokens"]) for t in all_tuples]
    apis = [t["api_time_s"] for t in all_tuples]
    ttfts = [t["ttft_s"] for t in all_tuples]
    gaps = [t["tool_gap_s"] for t in all_tuples if t["tool_gap_s"] is not None]

    # peak concurrency across all calls (interval sweep on task-relative times)
    events: list[tuple[float, int]] = []
    for sess in all_sessions:
        for req in sess["requests"]:
            events.append((req["t"], 1))
            events.append((req["t"] + req["api_time"], -1))
    events.sort()
    cur = peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)

    return {
        "n_calls": n_calls,
        "n_subagent_calls": n_sub,
        "subagent_ratio": round(n_sub / n_calls, 4) if n_calls else 0.0,
        "n_agents": len(all_sessions),
        "input_tokens": {"p50": _percentile(ins, 50), "p90": _percentile(ins, 90),
                         "p99": _percentile(ins, 99), "max": max(ins) if ins else 0},
        "output_tokens": {"p50": _percentile(outs, 50), "p90": _percentile(outs, 90),
                          "p99": _percentile(outs, 99), "max": max(outs) if outs else 0},
        "api_time_s": {"p50": _percentile(apis, 50), "p90": _percentile(apis, 90)},
        "ttft_s": {"p50": _percentile(ttfts, 50), "p90": _percentile(ttfts, 90)},
        "tool_gap_s": {"p50": _percentile(gaps, 50), "p90": _percentile(gaps, 90),
                       "max": max(gaps) if gaps else 0},
        "peak_concurrent_calls": peak,
    }


def export_replay(all_sessions: list[dict], hasher: BlockHasher, out_dir: Path,
                  block_size: int) -> None:
    """Emit ft-agentx-bench replay_cc.py (l2) inputs:
    manifest.jsonl (per-request rows) + blocks.jsonl (block text pool)."""
    rows = 0
    with open(out_dir / "manifest.jsonl", "w", encoding="utf-8") as f:
        for s in all_sessions:
            prev_gids: list[int] = []
            traj = s["id"].split("#")[0]
            for req in s["requests"]:
                gids = req["hash_ids"]
                lcp = 0
                for a, b in zip(prev_gids, gids):
                    if a != b:
                        break
                    lcp += 1
                prev_gids = gids
                f.write(json.dumps({
                    "sess": s["id"],
                    "t": req["t"],
                    "in": req["in"],
                    "out": req["out"],
                    "type": req["type"],
                    "traj": traj,
                    "model": req["model"],
                    "api_time": req["api_time"],
                    "prod_ttft": req["ttft"],
                    "gids": gids,
                    "implied_cached_tokens": min(lcp * block_size, req["in"]),
                }, ensure_ascii=False, separators=(",", ":")) + "\n")
                rows += 1
    with open(out_dir / "blocks.jsonl", "w", encoding="utf-8") as f:
        for i in sorted(hasher.texts):
            f.write(json.dumps({"i": i, "text": hasher.texts[i]},
                               ensure_ascii=False, separators=(",", ":")) + "\n")
    log.info("replay export: %d manifest rows, %d blocks", rows, len(hasher.texts))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=None)
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--model-path", default=None,
                    help="tokenizer source; default MODEL_PATH from config.env")
    ap.add_argument("--block-size", type=int, default=64)
    ap.add_argument("--tasks", default=None, help="comma-separated task ids filter")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    cfg = load_config(args.root)
    run_dir = Path(args.run_dir) if args.run_dir else cfg.run_dir()
    model_path = args.model_path or cfg.model_path

    tokenizer = None
    try:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
        log.info("tokenizer loaded from %s", model_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("tokenizer unavailable (%r); falling back to char blocks", exc)

    hasher = BlockHasher(tokenizer, args.block_size)
    out_dir = run_dir / "corpus"
    out_dir.mkdir(parents=True, exist_ok=True)

    task_filter = set(args.tasks.split(",")) if args.tasks else None
    tasks_root = run_dir / "tasks"
    all_sessions: list[dict] = []
    all_tuples: list[dict] = []
    all_full: list[dict] = []
    per_task_summary = {}

    for task_dir in sorted(p for p in tasks_root.iterdir() if p.is_dir()) if tasks_root.exists() else []:
        tid = task_dir.name
        if task_filter and tid not in task_filter:
            continue
        sessions, tuples, full = build_task_corpus(task_dir, tid, cfg.served_model_name, hasher)
        if not sessions:
            log.warning("%s: no successful calls, skipping", tid)
            continue
        all_sessions.extend(sessions)
        all_tuples.extend(tuples)
        all_full.extend(full)
        per_task_summary[tid] = {
            "n_agents": len(sessions),
            "n_calls": sum(len(s["requests"]) for s in sessions),
        }
        log.info("%s: %d agents, %d calls", tid, len(sessions), per_task_summary[tid]["n_calls"])

    with open(out_dir / "cc_traces.jsonl", "w", encoding="utf-8") as f:
        for s in all_sessions:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    with open(out_dir / "tuples.jsonl", "w", encoding="utf-8") as f:
        for t in all_tuples:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    with open(out_dir / "full_trace.jsonl", "w", encoding="utf-8") as f:
        for r in sorted(all_full, key=lambda t: (t["task_id"], t["t_s"])):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    stats = compute_stats(all_tuples, all_sessions)
    stats["per_task"] = per_task_summary
    (out_dir / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    (out_dir / "hash_registry.json").write_text(json.dumps(hasher.registry), encoding="utf-8")
    export_replay(all_sessions, hasher, out_dir, args.block_size)
    log.info("corpus written to %s: %d sessions, %d calls; stats=%s",
             out_dir, len(all_sessions), len(all_tuples), json.dumps(stats)[:300])


if __name__ == "__main__":
    main()
