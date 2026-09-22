# swe-trace-collect

在 gpu10 上收集 **SWE-bench Pro** 真实 Claude Code agent trace：vLLM 服务模型，
Claude Code（含 subagent）真实解题，所有 API 流量经捕获代理规范化记录，产出可
直接回放的语料，供后续 serving 系统对比实验（closed-loop 并发回放、1 小时窗口）。

方法论对齐：Claude Code gateway 协议（code.claude.com/docs/en/llm-gateway-protocol）、
Talaria/VAMP 的 trace-replay 评估法、semianalysisai cc-traces 语料格式。

## 架构

```
tmux 会话（每任务一个）
  claude -p "<issue>" --output-format stream-json --verbose --max-turns 60
    │ env: ANTHROPIC_BASE_URL=127.0.0.1:1924 (捕获代理)
    │      ANTHROPIC_MODEL = ANTHROPIC_DEFAULT_*_MODEL = CLAUDE_CODE_SUBAGENT_MODEL = 服务模型
    ▼
捕获代理 @1924（纯记录器：SSE 原样中继 + 双时钟时间戳 + 归因头 + gzip 落盘）
    ▼
vLLM @1923 (GPU 1, 原生 /v1/messages, qwen3.6-35b-a3b-awq-nvfp4, max-model-len 262144)
```

不碰 GPU 0；所有产物收敛在 `runs/<RUN_ID>/`；任务成功后立即删 workspace（仓库+venv）。

## 快速开始

```bash
cd /home/jinshuai/code/swe-trace-collect

# 1) 环境安装（隔离 venv，一次性；vllm 0.29.0 + aiohttp/pyarrow）
bash scripts/setup_vllm.sh

# 2) 数据集下载 + 任务清单（3 冒烟 + 20 正式，11 仓库轮转采样）
bash scripts/prepare_dataset.sh

# 3) 起服务（GPU 1，含 KV 容量实测与 max-model-len 自动回退）
bash scripts/serve.sh          # 停止: bash scripts/stop_serve.sh

# 4) 起捕获代理
bash scripts/start_proxy.sh    # 停止: bash scripts/stop_proxy.sh

# 5) 冒烟（hello + 3 个 SWE 任务 + 语料试构建）
bash scripts/run_smoke.sh      # 检查 runs/r1/tasks/*/result.json 与 trace/

# 6) 批量收集 20 任务（tmux 控制器内串行；断点续跑：重跑本脚本即可）
bash scripts/collect.sh        # 进度: tail -f runs/r1/orchestrator.log

# 7) 语料构建 + 统计
venvs/vllm/bin/python -m freetoken_trace.corpus --run-dir runs/r1

# 8) 清理（默认 dry-run 预览）
bash scripts/clean.sh          # --apply 执行；--all 连 trace 一起清（慎用）
```

## 产物

见 `docs/trace_schema.md`（schema_version 1.0）。每个任务：

- `trace/calls.jsonl` — 每 API 调用一条：请求/响应全文（gzip 引用）、usage 四元组、
  TTFT/e2e（双时钟）、`session_id/agent_id/parent_agent_id` 归因头（重建主/subagent 树）、
  重试与兼容 shim 事件
- `stream.jsonl` / `events.jsonl` — 驱动器视角（tool_use→tool_result 配对、api_retry、压缩事件）
- `result.json` — 结局（done/timeout/…）、turns、cost、session 对账
- `corpus/`（run 级）— `cc_traces.jsonl`（每 agent 一条 session 记录，t 对齐任务起点，
  保留主/子 agent 真实并发重叠；hash_ids 按 64-token 块哈希保留 prefix-cache 结构，
  与 ft-agentx-bench `replay_cc.py` 直接兼容）、`tuples.jsonl`（(in, out, tool_gap) 闭环
  回放单元）、`full_trace.jsonl`、`stats.json`

## 配置

`config.env` 是唯一配置源（bash 与 python 共用）：端口、模型路径、max-model-len、
超时、批量 N、清理开关。改完重跑相应脚本即可。

## 隔离与清理

- vLLM 装在 `venvs/vllm/`（不动机器上的 vllm-qwen36 / FreeToken venv）；工具在 `venvs/tools/`
- 每任务：独立 workspace（`git clone --filter=blob:none` 部分克隆）+ 独立 venv
  （workspace 内）+ 独立 `CLAUDE_CONFIG_DIR`（预置 onboarding）+ 独立 tmux 会话
- 任务 done 后立即删 workspace；`scripts/clean.sh` 兜底清扫（dry-run 默认）

## 采集质量纪律（与参考方案一致）

- 串行逐任务，避免跨任务干扰服务端时序
- trace 静默 >30 min 或墙钟 >150 min → 终止该任务并记录状态，语料只收完整轨迹
- 每任务记录结局与环境状态，回放侧可按需筛除
