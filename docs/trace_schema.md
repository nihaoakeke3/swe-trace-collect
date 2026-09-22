# Trace Schema (schema_version 1.0)

All collection output is JSON Lines (UTF-8). Timestamps are recorded on two
clocks everywhere: `ts_epoch_ns` (wall clock, aligns across proxy/driver/
server logs) and `ts_monotonic_ns` (CLOCK_MONOTONIC, safe for durations).

## File layout per task

```
runs/<run_id>/tasks/<task_id>/
├── workspace/                  # cloned repo @ base_commit (+ per-task venv); deleted after success
├── claude_config/              # isolated CLAUDE_CONFIG_DIR (onboarding pre-seeded)
├── prompt.txt                  # driver prompt (problem_statement + guidelines)
├── env.sh                      # exported env for the claude process
├── launch.sh                   # tmux launch script
├── stream.jsonl                # raw `claude -p --output-format stream-json` output
├── claude.stderr.log
├── events.jsonl                # normalized driver events (see below)
├── env_status.json             # clone/env install outcome
├── result.json                 # task outcome summary
└── trace/
    ├── calls.jsonl             # PROXY records: one line per API call (primary)
    └── bodies/
        ├── req-NNNNNN.json.gz      # exact request body (after model-name shim)
        ├── resp-NNNNNN.sse.gz      # exact upstream SSE byte stream (streaming calls)
        ├── resp-NNNNNN.json.gz     # plain JSON response (non-streaming calls)
        └── req-NNNNNN.count.json.gz# count_tokens requests
```

## calls.jsonl — record_type "llm_call"

```jsonc
{
  "schema_version": "1.0",
  "record_type": "llm_call",
  "run_id": "r1",
  "task_id": "instance_NodeBB__NodeBB-00c7...",
  "call_id": 42,                       // monotonic within the run (proxy-assigned)
  "endpoint": "/v1/messages",

  // ---- attribution (Claude Code headers -> agent tree) ----
  "session_id": "uuid",                // x-claude-code-session-id
  "agent_id": "...",                   // x-claude-code-agent-id
  "parent_agent_id": "..." | null,     // x-claude-code-parent-agent-id
  "is_subagent": true,                 // parent_agent_id != null

  // ---- request ----
  "request": {
    "model_requested": "claude-sonnet-4-6",       // what Claude Code asked for
    "model_served": "qwen3.6-35b-a3b-awq-nvfp4",  // shim rewrite target
    "stream": true,
    "max_tokens": 32000,
    "temperature": 1.0,
    "n_messages": 57,                  // conversation length (grows per turn)
    "n_tools": 15,
    "betas": ["..."],                  // anthropic-beta header, verbatim
    "body_ref": "bodies/req-000042.json.gz",
    "body_sha256": "..."
  },

  // ---- response ----
  "response": {
    "status": 200,
    "id": "msg_...",
    "model": "...",
    "stop_reason": "tool_use" | "end_turn" | "max_tokens" | "stop_sequence",
    "content_types": ["thinking", "text", "tool_use"],  // in block order
    "tool_uses": [{"id": "toolu_...", "name": "Bash"}],
    "usage": {
      "input_tokens": 51234,           // non-cached prompt tokens
      "cache_read_input_tokens": 38000,
      "cache_creation_input_tokens": 0,
      "output_tokens": 911,
      "prompt_tokens_total": 89234     // = input + cache_read + cache_creation
    },
    "stream_error": null               // set if the SSE stream aborted
  },

  // ---- timing (dual clock) ----
  "timing": {
    "ts_epoch_ns": 1789999649000000000,  // request received by proxy
    "ts_monotonic_ns": 1746248123456789,
    "t_first_byte_ns": ...,              // first upstream SSE byte
    "t_first_content_ns": ...,           // first content/delta event (TTFT anchor)
    "t_end_ns": ...,
    "ttft_ms": 812.3,
    "e2e_ms": 9134.2
  },

  // ---- compat shim events ----
  "shim": {
    "model_rewritten": true,
    "model_requested": "claude-sonnet-4-6",
    "dropped_fields_on_400": ["thinking"]   // only when the 400-retry fired
  }
}
```

Non-200 calls are recorded with `"response": {"status": <code>, "error": ...}`
and the upstream error body under `bodies/resp-NNNNNN.err.json.gz`.

## calls.jsonl — record_type "count_tokens"

Lightweight record for `/v1/messages/count_tokens`: attribution + timing +
`response.status`. Excluded from replay corpora.

## events.jsonl (driver-normalized)

- `{"event":"tool_pair","tool_name":"Bash","tool_use_id":"toolu_...",
     "duration_s_approx":5.2,"is_subagent_tool":false}` — client-side
  tool_use→tool_result pairing (approximate; the authoritative tool time is
  the inter-call gap in the proxy records).
- `{"event":"api_retry","attempt":1,"error_status":429,"retry_delay_ms":2100}`
- `{"event":"compact"}` — context auto-compaction marker
- `{"event":"init","session_id":...,"model":...}` / `{"event":"result",...}`

## Replay corpus (runs/<run_id>/corpus/)

- `cc_traces.jsonl` — one session record **per agent** (main or subagent):
  `{"id":"<task>#<session>/<agent>","models":[served],"block_size":64,
    "hash_id_scope":"local","requests":[{"t","model","in","out","hash_ids",
    "api_time","type":"s","ttft"}]}`
  - `t` = seconds since the task's first API call. Because every agent of a
    task shares this origin, the recorded wall-clock overlap between the main
    agent and concurrent subagents is preserved: replaying all session lines
    of a task reproduces the original intra-task concurrency.
  - `in` = prompt_tokens_total, `out` = output_tokens,
    `api_time` = e2e seconds, `ttft` = seconds.
  - `hash_ids` = content hashes of consecutive 64-token blocks of the
    canonicalized prompt (system + messages + tools), tokenized with the
    served model's tokenizer. Identical prefixes across calls/sessions get
    identical ids → replay preserves the radix/prefix-cache reuse structure
    (same convention as the semianalysisai cc-traces dataset).
- `tuples.jsonl` — per-agent closed-loop replay units:
  `{"task_id","agent_key","call_idx","t_s","input_tokens","output_tokens",
    "tool_gap_s","api_time_s","ttft_s","is_subagent"}` where
  `tool_gap_s = t(next call) - t_end(this call)` (tool execution + subagent
  wait; null for an agent's last call).
- `full_trace.jsonl` — enriched per-call records (identity, timing, usage,
  body refs, agent tree).
- `stats.json` / `hash_registry.json`.

## Correspondence rules (cross-checks)

1. proxy `calls.jsonl` ↔ driver `stream.jsonl`: call counts must match;
   session_id from `system/init`/`result` must equal the proxy header value.
2. Agent tree: proxy headers (`agent_id`/`parent_agent_id`) are authoritative;
   stream.jsonl `parent_tool_use_id` is the second channel. corpus.py warns on
   disagreement.
3. Token counts: usage from the server is authoritative; `prompt_tokens_total`
   is the replay-facing prompt length regardless of cache split.
