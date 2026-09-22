"""Closed-loop concurrency-slot replayer for agent-trace corpora.

Reference-methodology load model: ``C`` slots, each slot is a closed-loop
client replaying one agent session — send the session's next inference
request, wait for the response, sleep the recorded tool-call duration
(capped), issue the next request; when the session finishes, immediately
start a new session from the corpus pool. With ``--slots C`` and
``--max-running-requests C`` on the server, the engine-side decode batch
tracks the concurrency level without any queue collapse.

Outputs are schema-compatible with replay_cc.py / make_report.py:
  client_requests.jsonl, client_summary.json, watermark.jsonl

Run:  python replay_loop.py --run-dir results/runX --manifest ... --blocks ...
        --slots 4 --duration-s 4200 [--gap-cap-s 10] [--seed 7]
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import random
import time

import httpx

PORT = int(os.environ.get("REPLAY_PORT", "1921"))
MODEL = os.environ.get("REPLAY_MODEL", "served")


def load_corpus(manifest: str):
    sessions: dict[str, list[dict]] = {}
    for line in open(manifest):
        r = json.loads(line)
        sessions.setdefault(r["sess"], []).append(r)
    out = {}
    for s, reqs in sessions.items():
        reqs.sort(key=lambda r: r["t"])
        out[s] = reqs
    return out


def pctl(vals: list[float], q: float):
    if not vals:
        return None
    v = sorted(vals)
    idx = min(len(v) - 1, int(round(q / 100 * (len(v) - 1))))
    return round(v[idx], 4)


async def run_request(idx, sess, k, req, prompt, client, wm, out_f):
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max(1, int(req["out"])),
        "temperature": 0.0,
        "top_k": 1,
        "stream": True,
        "ignore_eos": True,
        "stream_options": {"include_usage": True},
        "user": f"s{idx}",
    }
    send_t = time.time()
    ttft = None
    out_got = 0
    err = None
    try:
        async with client.stream(
            "POST", f"http://127.0.0.1:{PORT}/v1/chat/completions", json=body
        ) as resp:
            if resp.status_code != 200:
                err = f"http {resp.status_code}: {(await resp.aread())[:200].decode(errors='replace')}"
            else:
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    usage = obj.get("usage") if isinstance(obj, dict) else None
                    if usage and usage.get("completion_tokens"):
                        out_got = int(usage["completion_tokens"])
                    content = None
                    try:
                        delta = obj["choices"][0].get("delta") or {}
                        content = delta.get("content") or delta.get("reasoning_content")
                    except (KeyError, IndexError, TypeError):
                        pass
                    if content:
                        if ttft is None:
                            ttft = time.time() - send_t
                        out_got += 1  # per-chunk fallback (1 token/chunk)
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    done_t = time.time()
    wm.done += 1
    if err:
        wm.err += 1
    rec = {
        "i": idx,
        "sess": sess,
        "k": k,
        "in": req["in"],
        "out_req": req["out"],
        "out_got": out_got,
        "send_t": send_t,
        "ttft": ttft,
        "e2e": (done_t - send_t) if ttft is not None else None,
        "api_time": req.get("api_time"),
        "prod_ttft": req.get("prod_ttft"),
        "implied_cached": req.get("implied_cached_tokens"),
        "err": err,
    }
    out_f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    return rec


async def slot_loop(slot_id, sessions_pool, blocks, client, args, wm, out_f, rng, stop_at, state):
    while time.time() < stop_at:
        if not state["queue"]:
            state["queue"] = list(sessions_pool.keys())
            rng.shuffle(state["queue"])
        sess = state["queue"].pop(0)
        reqs = sessions_pool[sess]
        for k, req in enumerate(reqs):
            if time.time() >= stop_at:
                return
            prompt = "".join(blocks[i] for i in req["gids"])
            state["counter"] += 1
            idx = state["counter"]
            wm.sent += 1
            rec = await run_request(idx, sess, k, req, prompt, client, wm, out_f)
            # closed-loop think time: recorded gap between this response and
            # the next request = t_{k+1} - t_k - api_time_k, capped
            if k + 1 < len(reqs):
                gap = reqs[k + 1]["t"] - req["t"] - req.get("api_time", 0.0)
                gap = min(max(gap, 0.0), args.gap_cap_s)
            else:
                gap = 0.0  # session finished: start a new one immediately
            now = time.time()
            if gap > 0 and now + gap < stop_at:
                await asyncio.sleep(gap)


class Watermarks:
    def __init__(self, path):
        self.f = open(path, "w", buffering=1)
        self.sent = 0
        self.done = 0
        self.err = 0

    async def loop(self, stop):
        while not stop.is_set():
            self.f.write(json.dumps(
                {"t": time.time(), "sent": self.sent, "done": self.done, "err": self.err}
            ) + "\n")
            await asyncio.sleep(0.1)


async def main_async(args):
    sessions = load_corpus(args.manifest)
    n_blocks = sum(1 for _ in open(args.blocks))
    blocks = [None] * n_blocks
    for line in open(args.blocks):
        o = json.loads(line)
        blocks[o["i"]] = o["text"]
    assert all(b is not None for b in blocks)
    print(f"[loop] {len(sessions)} sessions, {sum(len(v) for v in sessions.values())} requests, "
          f"{n_blocks} blocks, slots={args.slots}, duration={args.duration_s}s")

    os.makedirs(args.run_dir, exist_ok=True)
    out_f = open(os.path.join(args.run_dir, "client_requests.jsonl"), "w", buffering=1)
    wm = Watermarks(os.path.join(args.run_dir, "watermark.jsonl"))
    stop = asyncio.Event()
    wm_task = asyncio.create_task(wm.loop(stop))
    rng = random.Random(args.seed)
    state = {"queue": [], "counter": 0}

    limits = httpx.Limits(max_connections=args.slots * 4,
                          max_keepalive_connections=args.slots * 2)
    timeout = httpx.Timeout(connect=30.0, read=43200.0, pool=43200.0, write=60.0)
    stop_at = time.time() + args.duration_s
    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
        tasks = [
            asyncio.create_task(
                slot_loop(s, sessions, blocks, client, args, wm, out_f, rng, stop_at, state)
            )
            for s in range(args.slots)
        ]
        await asyncio.gather(*tasks)
        stop.set()
        await wm_task

    recs = [json.loads(l) for l in open(os.path.join(args.run_dir, "client_requests.jsonl"))]
    oks = [r for r in recs if not r["err"] and r["ttft"] is not None]
    ttfts = [r["ttft"] for r in oks]
    e2es = [r["e2e"] for r in oks]
    tpots = [r["e2e"] / max(1, r["out_got"]) for r in oks if r["out_got"] > 1]
    span = (max(r["send_t"] + (r["e2e"] or 0) for r in oks) - min(r["send_t"] for r in oks)) if oks else 0
    summary = {
        "mode": "closedloop",
        "slots": args.slots,
        "duration_s": args.duration_s,
        "n_reqs": len(recs),
        "n_ok": len(oks),
        "n_err": len(recs) - len(oks),
        "ttft_s": {q: pctl(ttfts, q) for q in (50, 90, 99)},
        "ttft_max": pctl(ttfts, 100),
        "e2e_s": {q: pctl(e2es, q) for q in (50, 90, 99)},
        "tpot_s": {q: pctl(tpots, q) for q in (50, 90, 99)},
        "out_tokens_total": sum(r["out_got"] for r in oks),
        "in_tokens_total": sum(r["in"] for r in oks),
        "aggregate_decode_tps": round(sum(r["out_got"] for r in oks) / span, 2) if span else None,
        "aggregate_prefill_tps": round(sum(r["in"] for r in oks) / span, 2) if span else None,
    }
    json.dump(summary, open(os.path.join(args.run_dir, "client_summary.json"), "w"), indent=1)
    print("[loop] summary:", json.dumps(summary, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--blocks", required=True)
    ap.add_argument("--slots", type=int, required=True)
    ap.add_argument("--duration-s", type=int, default=4200)
    ap.add_argument("--gap-cap-s", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
