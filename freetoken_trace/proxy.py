"""Capturing reverse proxy between Claude Code and the vLLM Anthropic endpoint.

Relays SSE bytes verbatim (so Claude Code behaves exactly as against upstream)
while recording every API call: full request/response bodies (gzipped),
dual-clock timestamps, token usage, retry/shim events and the Claude Code
attribution headers (x-claude-code-session-id / -agent-id / -parent-agent-id)
used to rebuild the main-agent / subagent call tree.

Records are JSON Lines appended to <run-dir>/tasks/<task_id>/trace/calls.jsonl;
the active task is switched via POST /__task (collection is serial, so a
single proxy process serves a whole run and call_ids stay globally ordered).

Run:  venvs/tools/bin/python -m freetoken_trace.proxy [--selftest]
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import logging
import sys
import time
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, web

from .config import Config, load_config

SCHEMA_VERSION = "1.0"

log = logging.getLogger("proxy")

HOP_HEADERS = {
    "host", "content-length", "connection", "accept-encoding", "keep-alive",
    "transfer-encoding", "upgrade", "proxy-connection", "te", "trailers",
}
ATTRIBUTION_HEADERS = (
    "x-claude-code-session-id",
    "x-claude-code-agent-id",
    "x-claude-code-parent-agent-id",
)
# Request fields dropped in one retry if upstream answers 400 (compat shim).
SHIM_DROP_FIELDS = ("thinking", "metadata", "top_k")

SSE_PING_EVENT = b'event: ping\ndata: {"type":"ping"}\n\n'
SSE_ERROR_TMPL = 'event: error\ndata: {"type":"error","error":{"type":"api_error","message":%s}}\n\n'


class RunRecorder:
    """Background writer: never blocks request handlers on disk I/O."""

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=20000)
        self._writer: asyncio.Task | None = None
        self.dropped = 0

    def start(self) -> None:
        if self._writer is not None:
            return
        self._writer = asyncio.get_running_loop().create_task(self._writer_loop())

    async def stop(self) -> None:
        await self.queue.put(None)
        if self._writer:
            await self._writer

    async def _writer_loop(self) -> None:
        while True:
            item = await self.queue.get()
            if item is None:
                return
            kind, path, payload = item
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                if kind == "line":
                    with open(path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
                elif kind == "gz":
                    tmp = path.with_suffix(path.suffix + ".tmp")
                    with gzip.open(tmp, "wb", compresslevel=6) as f:
                        f.write(payload)
                    tmp.replace(path)  # atomic: readers never see partial gz
            except Exception:  # noqa: BLE001
                log.exception("writer failed for %s", path)

    def submit(self, kind: str, path: Path, payload) -> None:
        try:
            self.queue.put_nowait((kind, path, payload))
        except asyncio.QueueFull:
            self.dropped += 1
            log.error("record queue full; dropped=%d", self.dropped)


class State:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.call_seq = 0
        self.current_task = "unknown"
        self.current_task_since = time.time_ns()
        self.task_calls = 0


def _sse_iter(buf: bytes):
    """Yield (event_name, data_bytes) for each complete SSE event in buf."""
    for chunk in buf.split(b"\n\n"):
        if not chunk.strip():
            continue
        name = None
        data_lines = []
        for line in chunk.split(b"\n"):
            if line.startswith(b"event:"):
                name = line[6:].strip().decode("utf-8", "replace")
            elif line.startswith(b"data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            yield name, b"\n".join(data_lines)


class MessageAccumulator:
    """Reassembles an Anthropic SSE stream into a final message summary."""

    def __init__(self) -> None:
        self.msg_id = None
        self.model = None
        self.usage: dict = {}
        self.stop_reason = None
        self.content_types: list[str] = []
        self.tool_uses: list[dict] = []
        self.error = None
        self.ended = False

    def feed(self, event: str | None, data: bytes) -> None:
        try:
            d = json.loads(data)
        except Exception:  # noqa: BLE001
            return
        etype = d.get("type") or event
        if etype == "message_start":
            msg = d.get("message", {})
            self.msg_id = msg.get("id")
            self.model = msg.get("model")
            u = msg.get("usage") or {}
            for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                if u.get(k) is not None:
                    self.usage[k] = u[k]
        elif etype == "content_block_start":
            cb = d.get("content_block", {})
            t = cb.get("type")
            if t:
                self.content_types.append(t)
            if t == "tool_use":
                self.tool_uses.append({"id": cb.get("id"), "name": cb.get("name")})
        elif etype == "message_delta":
            delta = d.get("delta") or {}
            if delta.get("stop_reason"):
                self.stop_reason = delta["stop_reason"]
            u = d.get("usage") or {}
            for k, v in u.items():
                if v is not None:
                    self.usage[k] = v
        elif etype == "message_stop":
            self.ended = True
        elif etype == "error":
            self.error = d.get("error", d)

    def merge_usage(self) -> dict:
        u = dict(self.usage)
        u.setdefault("input_tokens", None)
        u.setdefault("cache_read_input_tokens", 0)
        u.setdefault("cache_creation_input_tokens", 0)
        u.setdefault("output_tokens", None)
        total = None
        if u["input_tokens"] is not None:
            total = int(u["input_tokens"]) + int(u["cache_read_input_tokens"] or 0) \
                + int(u["cache_creation_input_tokens"] or 0)
        u["prompt_tokens_total"] = total
        return u


def _attribution(request: web.Request) -> dict:
    h = request.headers
    session_id = h.get("x-claude-code-session-id")
    agent_id = h.get("x-claude-code-agent-id")
    parent_agent_id = h.get("x-claude-code-parent-agent-id")
    return {
        "session_id": session_id,
        "agent_id": agent_id,
        "parent_agent_id": parent_agent_id,
        # Claude Code sends x-claude-code-agent-id for subagents; the main
        # agent carries only the session id (parent-agent-id is only set for
        # nested subagents).
        "is_subagent": bool(agent_id or parent_agent_id),
    }


def _base_record(cfg: Config, state: State, call_id: int, task_id: str,
                 endpoint: str, record_type: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": record_type,
        "run_id": cfg.run_id,
        "task_id": task_id,
        "call_id": call_id,
        "endpoint": endpoint,
    }


def _forward_headers(request: web.Request) -> dict:
    return {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}


async def handle_messages(request: web.Request) -> web.StreamResponse:
    app = request.app
    cfg: Config = app["cfg"]
    state: State = app["state"]
    rec: RunRecorder = app["rec"]

    ts_epoch = time.time_ns()
    ts_mono = time.monotonic_ns()
    raw_body = await request.read()
    body_sha = hashlib.sha256(raw_body).hexdigest()

    try:
        body = json.loads(raw_body)
    except Exception:  # noqa: BLE001
        body = None
    is_stream = isinstance(body, dict) and bool(body.get("stream"))

    async with state.lock:
        state.call_seq += 1
        call_id = state.call_seq
        task_id = state.current_task
    state.task_calls += 1

    trace_dir = app["run_dir"] / "tasks" / task_id / "trace"
    rec.submit("gz", trace_dir / "bodies" / f"req-{call_id:06d}.json.gz", raw_body)

    attr = _attribution(request)
    fwd_headers = _forward_headers(request)

    # --- compat shim: rewrite model name to the served model ---
    shim: dict = {"model_rewritten": False}
    req_meta: dict = {}
    if isinstance(body, dict):
        requested = body.get("model")
        if requested and requested != cfg.served_model_name:
            shim["model_rewritten"] = True
            shim["model_requested"] = requested
            body = {**body, "model": cfg.served_model_name}
        req_meta = {
            "model_requested": requested,
            "model_served": cfg.served_model_name,
            "stream": bool(body.get("stream")),
            "max_tokens": body.get("max_tokens"),
            "temperature": body.get("temperature"),
            "n_messages": len(body.get("messages") or []),
            "n_tools": len(body.get("tools") or []),
            "betas": (request.headers.get("anthropic-beta") or "").split(",") or None,
        }
        raw_body = json.dumps(body, ensure_ascii=False).encode()

    timing: dict = {
        "ts_epoch_ns": ts_epoch,
        "ts_monotonic_ns": ts_mono,
        "t_first_byte_ns": None,
        "t_first_content_ns": None,
        "t_end_ns": None,
    }

    status = 0
    usage: dict = {}
    resp_summary: dict = {}
    error_note: str | None = None

    try:
        timeout = ClientTimeout(
            sock_connect=15,
            sock_read=cfg.upstream_sock_read_timeout_s,
            total=None,
        )
        up_resp = await app["client"].post(
            cfg.proxy_upstream + "/v1/messages",
            data=raw_body,
            headers=fwd_headers,
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        error_note = "upstream_headers_timeout"
        up_resp = None
    except Exception as exc:  # noqa: BLE001
        error_note = f"upstream_connect_error: {exc!r}"
        up_resp = None

    if up_resp is not None:
        status = up_resp.status
        if status == 400 and isinstance(body, dict):
            # one shim retry without suspect fields / server-side tools
            dropped = [k for k in SHIM_DROP_FIELDS if k in body]
            tools = body.get("tools")
            if isinstance(tools, list):
                kept = [t for t in tools
                        if isinstance(t, dict) and t.get("type") in (None, "custom")]
                if len(kept) != len(tools):
                    dropped.append(f"tools:{len(tools) - len(kept)} server-side")
                    retry_tools = kept
                else:
                    retry_tools = tools
            else:
                retry_tools = tools
            if dropped:
                retry_body = {k: v for k, v in body.items() if k not in SHIM_DROP_FIELDS}
                retry_body["tools"] = retry_tools
                try:
                    up_resp2 = await app["client"].post(
                        cfg.proxy_upstream + "/v1/messages",
                        data=json.dumps(retry_body, ensure_ascii=False).encode(),
                        headers=fwd_headers,
                        timeout=timeout,
                    )
                except Exception as exc:  # noqa: BLE001
                    error_note = f"shim_retry_error: {exc!r}"
                    up_resp2 = None
                if up_resp2 is not None and up_resp2.status == 200:
                    shim["dropped_fields_on_400"] = dropped
                    up_resp.release()
                    up_resp = up_resp2
                    status = up_resp2.status

    if up_resp is None or status != 200:
        # Non-200 (or unreachable): relay as-is, Claude Code's retry layer reacts.
        detail = b"{}"
        if up_resp is not None:
            retry_after = up_resp.headers.get("retry-after")
            x_should_retry = up_resp.headers.get("x-should-retry")
            detail = await up_resp.read()
            up_resp.release()
        else:
            retry_after = None
            x_should_retry = None
        rec.submit("gz", trace_dir / "bodies" / f"resp-{call_id:06d}.err.json.gz", detail)
        timing["t_end_ns"] = time.monotonic_ns()
        record = _base_record(cfg, state, call_id, task_id, "/v1/messages", "llm_call")
        record.update(attr)
        record["request"] = {**req_meta, "body_ref": f"bodies/req-{call_id:06d}.json.gz",
                             "body_sha256": body_sha}
        record["response"] = {"status": status, "error": error_note}
        record["timing"] = timing
        record["shim"] = shim
        rec.submit("line", trace_dir / "calls.jsonl", record)
        resp = web.Response(status=status or 502, body=detail, content_type="application/json")
        if retry_after:
            resp.headers["retry-after"] = retry_after
        if x_should_retry:
            resp.headers["x-should-retry"] = x_should_retry
        return resp

    # --- success: streaming or plain JSON ---
    if is_stream:
        sse_chunks: list[bytes] = []
        acc = MessageAccumulator()
        out = web.StreamResponse(status=200, headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        })
        await out.prepare(request)  # commit headers immediately: Claude Code must not time out during long prefills

        async def inject_pings() -> None:
            interval = cfg.ping_interval_s
            while True:
                await asyncio.sleep(5)
                last = timing["t_first_byte_ns"]
                idle = time.monotonic_ns() - (last or ts_mono)
                if idle > interval * 1e9:
                    try:
                        await out.write(SSE_PING_EVENT)
                    except Exception:  # noqa: BLE001
                        return

        pinger = asyncio.get_running_loop().create_task(inject_pings())
        try:
            buf = b""
            async for chunk in up_resp.content.iter_any():
                now_mono = time.monotonic_ns()
                if timing["t_first_byte_ns"] is None:
                    timing["t_first_byte_ns"] = now_mono
                sse_chunks.append(chunk)
                try:
                    await out.write(chunk)
                except Exception:  # noqa: BLE001
                    error_note = "client_disconnected"
                    break
                buf += chunk
                while b"\n\n" in buf:
                    event_blob, buf = buf.split(b"\n\n", 1)
                    for ev_name, ev_data in _sse_iter(event_blob + b"\n\n"):
                        acc.feed(ev_name, ev_data)
                        if ev_name in ("content_block_delta", "message_delta",
                                       "content_block_start", "message_stop") \
                                and timing["t_first_content_ns"] is None:
                            timing["t_first_content_ns"] = now_mono
        except Exception as exc:  # noqa: BLE001
            error_note = error_note or f"upstream_stream_error: {exc!r}"
            try:
                await out.write(SSE_ERROR_TMPL % json.dumps(str(exc)).encode())
            except Exception:  # noqa: BLE001
                pass
        finally:
            pinger.cancel()
        timing["t_end_ns"] = time.monotonic_ns()
        try:
            await out.write(b"")  # flush
            await out.write_eof()
        except Exception:  # noqa: BLE001
            pass

        usage = acc.merge_usage()
        resp_summary = {
            "status": 200,
            "id": acc.msg_id,
            "model": acc.model,
            "stop_reason": acc.stop_reason,
            "content_types": acc.content_types,
            "tool_uses": acc.tool_uses,
            "usage": usage,
            "stream_error": error_note,
        }
        rec.submit("gz", trace_dir / "bodies" / f"resp-{call_id:06d}.sse.gz",
                   b"".join(sse_chunks))
        out_response = web.Response(status=200)  # stream already sent
    else:
        detail = await up_resp.read()
        up_resp.release()
        timing["t_end_ns"] = time.monotonic_ns()
        rec.submit("gz", trace_dir / "bodies" / f"resp-{call_id:06d}.json.gz", detail)
        try:
            msg = json.loads(detail)
            usage = _merge_plain_usage(msg.get("usage") or {})
            resp_summary = {
                "status": 200,
                "id": msg.get("id"),
                "model": msg.get("model"),
                "stop_reason": (msg.get("stop_reason")),
                "content_types": [b.get("type") for b in msg.get("content", []) if isinstance(b, dict)],
                "tool_uses": [
                    {"id": b.get("id"), "name": b.get("name")}
                    for b in msg.get("content", []) if isinstance(b, dict) and b.get("type") == "tool_use"
                ],
                "usage": usage,
            }
        except Exception:  # noqa: BLE001
            resp_summary = {"status": 200, "parse_error": True}
        out_response = web.Response(status=200, body=detail, content_type="application/json")

    record = _base_record(cfg, state, call_id, task_id, "/v1/messages", "llm_call")
    record.update(attr)
    record["request"] = {**req_meta, "body_ref": f"bodies/req-{call_id:06d}.json.gz",
                         "body_sha256": body_sha}
    record["response"] = resp_summary
    ttft = None
    if timing["t_first_content_ns"]:
        ttft = (timing["t_first_content_ns"] - ts_mono) / 1e6
    timing["ttft_ms"] = ttft
    timing["e2e_ms"] = (timing["t_end_ns"] - ts_mono) / 1e6
    record["timing"] = timing
    record["shim"] = shim
    rec.submit("line", trace_dir / "calls.jsonl", record)
    return out_response


def _merge_plain_usage(u: dict) -> dict:
    out = {
        "input_tokens": u.get("input_tokens"),
        "cache_read_input_tokens": u.get("cache_read_input_tokens", 0),
        "cache_creation_input_tokens": u.get("cache_creation_input_tokens", 0),
        "output_tokens": u.get("output_tokens"),
    }
    if out["input_tokens"] is not None:
        out["prompt_tokens_total"] = (
            int(out["input_tokens"]) + int(out["cache_read_input_tokens"] or 0)
            + int(out["cache_creation_input_tokens"] or 0)
        )
    return out


async def handle_count_tokens(request: web.Request) -> web.Response:
    app = request.app
    cfg: Config = app["cfg"]
    state: State = app["state"]
    rec: RunRecorder = app["rec"]

    ts_epoch = time.time_ns()
    ts_mono = time.monotonic_ns()
    raw = await request.read()
    async with state.lock:
        state.call_seq += 1
        call_id = state.call_seq
        task_id = state.current_task
    trace_dir = app["run_dir"] / "tasks" / task_id / "trace"
    rec.submit("gz", trace_dir / "bodies" / f"req-{call_id:06d}.count.json.gz", raw)

    status = 502
    detail = b"{}"
    try:
        async with app["client"].post(
            cfg.proxy_upstream + "/v1/messages/count_tokens",
            data=raw,
            headers=_forward_headers(request),
            timeout=ClientTimeout(total=60),
        ) as up:
            status = up.status
            detail = await up.read()
    except Exception as exc:  # noqa: BLE001
        detail = json.dumps({"error": repr(exc)}).encode()
    timing = {
        "ts_epoch_ns": ts_epoch,
        "ts_monotonic_ns": ts_mono,
        "e2e_ms": (time.monotonic_ns() - ts_mono) / 1e6,
    }
    record = _base_record(cfg, state, call_id, task_id,
                          "/v1/messages/count_tokens", "count_tokens")
    record.update(_attribution(request))
    record["response"] = {"status": status, "detail_ref": f"bodies/req-{call_id:06d}.count.json.gz"}
    record["timing"] = timing
    rec.submit("line", trace_dir / "calls.jsonl", record)
    return web.Response(status=status, body=detail, content_type="application/json")


async def passthrough(request: web.Request) -> web.Response:
    app = request.app
    cfg: Config = app["cfg"]
    raw = await request.read() if request.can_read_body else b""
    try:
        async with app["client"].request(
            request.method,
            cfg.proxy_upstream + request.rel_url.path_qs,
            data=raw,
            headers=_forward_headers(request),
            timeout=ClientTimeout(total=120),
        ) as up:
            body = await up.read()
            return web.Response(status=up.status, body=body,
                                content_type=up.headers.get("Content-Type", "application/json"))
    except Exception as exc:  # noqa: BLE001
        return web.Response(status=502, text=f"upstream error: {exc!r}")


async def handle_health(request: web.Request) -> web.Response:
    app = request.app
    state: State = app["state"]
    upstream_ok = False
    try:
        async with app["client"].get(
            app["cfg"].proxy_upstream + "/health",
            timeout=ClientTimeout(total=5),
        ) as up:
            upstream_ok = up.status == 200
    except Exception:  # noqa: BLE001
        pass
    return web.json_response({
        "status": "ok" if upstream_ok else "degraded",
        "upstream": upstream_ok,
        "current_task": state.current_task,
        "calls_total": state.call_seq,
    })


async def handle_task(request: web.Request) -> web.Response:
    state: State = request.app["state"]
    data = await request.json()
    task_id = str(data.get("task_id") or "unknown")
    async with state.lock:
        state.current_task = task_id
        state.current_task_since = time.time_ns()
        state.task_calls = 0
    log.info("current task -> %s", task_id)
    return web.json_response({"ok": True, "current_task": state.current_task})


async def handle_status(request: web.Request) -> web.Response:
    state: State = request.app["state"]
    rec: RunRecorder = request.app["rec"]
    return web.json_response({
        "current_task": state.current_task,
        "calls_total": state.call_seq,
        "recorder_dropped": rec.dropped,
    })


async def on_cleanup(app: web.Application) -> None:
    await app["rec"].stop()
    if app["client"] is not None:
        await app["client"].close()


def build_app(cfg: Config, run_dir: Path) -> web.Application:
    app = web.Application(client_max_size=1024 * 1024 * 256)
    app["cfg"] = cfg
    app["run_dir"] = run_dir
    app["state"] = State()
    rec = RunRecorder(run_dir)
    app["rec"] = rec
    app["client"] = None  # created in on_startup (needs a running loop)
    app.on_cleanup.append(on_cleanup)

    app.router.add_post("/v1/messages", handle_messages)
    app.router.add_post("/v1/messages/count_tokens", handle_count_tokens)
    app.router.add_get("/v1/models", passthrough)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/__task", handle_task)
    app.router.add_get("/__status", handle_status)
    app.router.add_route("*", "/{tail:.*}", passthrough)
    return app


async def _selftest() -> None:
    """Spin a canned SSE upstream + the proxy, verify relay and records."""
    import aiohttp
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="stc-selftest-"))
    canned = (
        b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_t1",'
        b'"type":"message","role":"assistant","model":"fake-model","content":[],'
        b'"usage":{"input_tokens":100,"output_tokens":0}}}\n\n'
        b'event: ping\ndata: {"type":"ping"}\n\n'
        b'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
        b'"content_block":{"type":"text","text":""}}\n\n'
        b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
        b'"delta":{"type":"text_delta","text":"OK"}}\n\n'
        b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
        b'"usage":{"output_tokens":5}}\n\n'
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )

    async def fake_upstream(request: web.Request) -> web.StreamResponse:
        out = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await out.prepare(request)
        for i in range(0, len(canned), 64):
            await out.write(canned[i:i + 64])
            await asyncio.sleep(0.01)
        await out.write_eof()
        return out

    async def fake_count(request: web.Request) -> web.Response:
        return web.json_response({"input_tokens": 42})

    up_app = web.Application()
    up_app.router.add_post("/v1/messages", fake_upstream)
    up_app.router.add_post("/v1/messages/count_tokens", fake_count)
    up_runner = web.AppRunner(up_app)
    await up_runner.setup()
    await web.TCPSite(up_runner, "127.0.0.1", 1980).start()

    cfg = load_config()
    cfg.proxy_upstream = "http://127.0.0.1:1980"
    cfg.proxy_port = 1981
    app = build_app(cfg, tmp)
    app["client"] = ClientSession()  # explicit: selftest bypasses web.run_app
    app["rec"].start()
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 1981).start()

    async with aiohttp.ClientSession() as cli:
        async with cli.post("http://127.0.0.1:1981/__task",
                            json={"task_id": "selftest"}) as r:
            assert (await r.json())["ok"]
        async with cli.post(
            "http://127.0.0.1:1981/v1/messages",
            json={"model": "claude-sonnet-4-6", "max_tokens": 64, "stream": True,
                  "messages": [{"role": "user", "content": "hi"}]},
            headers={"x-claude-code-session-id": "sess-1",
                     "x-claude-code-agent-id": "agent-main",
                     "x-claude-code-parent-agent-id": ""},
        ) as r:
            body = await r.read()
            if r.status != 200:
                calls_file = tmp / "tasks" / "selftest" / "trace" / "calls.jsonl"
                debug = calls_file.read_text()[-600:] if calls_file.exists() else "no calls file"
                raise AssertionError((r.status, body[:200], debug))
            relayed = body
        assert b"message_start" in relayed and b"message_stop" in relayed, relayed[:200]
        async with cli.post(
            "http://127.0.0.1:1981/v1/messages/count_tokens",
            json={"model": "x", "messages": []},
        ) as r:
            assert (await r.json())["input_tokens"] == 42
        async with cli.get("http://127.0.0.1:1981/__status") as r:
            st = await r.json()
        assert st["calls_total"] == 2, st

    await asyncio.sleep(0.5)  # let the writer drain
    calls_file = tmp / "tasks" / "selftest" / "trace" / "calls.jsonl"
    lines = [json.loads(l) for l in calls_file.read_text().splitlines() if l.strip()]
    call = [l for l in lines if l["record_type"] == "llm_call"][0]
    assert call["response"]["usage"]["input_tokens"] == 100, call["response"]["usage"]
    assert call["response"]["usage"]["output_tokens"] == 5
    assert call["response"]["usage"]["prompt_tokens_total"] == 100
    assert call["response"]["stop_reason"] == "end_turn"
    assert call["shim"]["model_rewritten"] is True
    assert call["session_id"] == "sess-1" and call["is_subagent"] is False
    assert call["timing"]["ttft_ms"] is not None and call["timing"]["e2e_ms"] > 0
    bodies = tmp / "tasks" / "selftest" / "trace" / "bodies"
    req_gz = bodies / "req-000001.json.gz"
    assert req_gz.exists(), sorted(p.name for p in bodies.iterdir())
    sse_gz = bodies / "resp-000001.sse.gz"
    assert gzip.open(sse_gz, "rb").read() == canned
    assert (bodies / "req-000002.count.json.gz").exists()

    await runner.cleanup()
    await up_runner.cleanup()
    print("SELFTEST_OK", tmp)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=None, help="project root (default: package parent)")
    ap.add_argument("--run-dir", default=None, help="run dir; default runs/<RUN_ID>")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if args.selftest:
        asyncio.run(_selftest())
        return

    cfg = load_config(args.root)
    run_dir = Path(args.run_dir) if args.run_dir else cfg.run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)
    app = build_app(cfg, run_dir)

    async def _start(app: web.Application) -> None:
        if app["client"] is None:
            app["client"] = ClientSession()
        app["rec"].start()

    app.on_startup.append(_start)
    log.info("capture proxy on 127.0.0.1:%d -> %s (run_dir=%s)",
             cfg.proxy_port, cfg.proxy_upstream, run_dir)
    web.run_app(app, host="127.0.0.1", port=cfg.proxy_port, print=None)


if __name__ == "__main__":
    main()
