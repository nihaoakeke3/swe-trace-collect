# swe-trace-collect

收集真实的 Claude Code agent 在 SWE-bench 风格任务上的**全保真 trace**（完整
prompt、工具调用、token 数、时间戳、subagent 并发结构），并提供闭环并发回放器，
用于 LLM serving 系统的公平可重复对比实验（trace-replay 评估范式，方法论对齐
Talaria / VAMP / Mooncake）。

```
SWE-bench 任务 → Claude Code(含 subagent) 真实工作
    → 捕获代理记录全部 Anthropic API 流量（零失真）
    → 全保真 trace → 回放语料 → 任意 /v1/messages 兼容引擎上的闭环压测
```

- 引擎无关：任何实现 Anthropic `/v1/messages`(+`/count_tokens`) 的推理服务均可
  （vLLM ≥0.11.1 原生支持，FreeToken 等）
- agent 无侵入：只重定向 `ANTHROPIC_BASE_URL`，不修改 Claude Code 本体
- 轨迹完整：记录请求/响应全文、usage 四元组、双时钟时间戳、主/子 agent 归因树
- 数据自包含：trace、回放语料、对账与统计工具都在本仓库内

详细方法论、采集终止判定的完整状态机、踩坑记录见 **[SUMMARY.md](SUMMARY.md)**；
trace 字段规范见 **[docs/trace_schema.md](docs/trace_schema.md)**。

## 环境要求

| 依赖 | 说明 |
|---|---|
| Linux + NVIDIA GPU | 服务引擎用；采集侧本身不需要 GPU |
| Claude Code CLI | `npm install -g @anthropic-ai/claude-code`（≥2.x） |
| python3.10+ | 含 venv 模块 |
| tmux / git / curl | 长跑与数据获取 |
| 网络 | pypi.org、github.com、hf-mirror.com（或自行替换镜像） |
| 推理服务 | 一个 Anthropic Messages API 兼容端点（本仓库提供 vLLM 启动脚本） |

## 快速开始

```bash
# 0) 克隆 + 配置
git clone https://github.com/nihaoakeke3/swe-trace-collect.git
cd swe-trace-collect
cp config.env.example config.env     # 按注释逐项填写（模型路径/端口/超时等）

# 1) 环境（隔离 venv：服务引擎 + 采集工具）
bash scripts/setup_vllm.sh           # vllm 0.29.0 + aiohttp + pyarrow

# 2) 数据集 + 任务清单（3 冒烟 + 20 正式，仓库轮转采样）
bash scripts/prepare_dataset.sh

# 3) 起推理服务（GPU 1）与捕获代理
bash scripts/serve.sh                # vLLM，含 KV 容量实测与上下文长度自动回退
bash scripts/start_proxy.sh          # 捕获代理，默认 127.0.0.1:1924

# 4) 冒烟验证（1 个 hello + 3 个 SWE 任务，~1.5h）
bash scripts/run_smoke.sh
venvs/tools/bin/python -m freetoken_trace.verify --run-dir runs/r1   # 必须 PASS

# 5) 批量收集（tmux 无人值守，断点续跑，任务完成即清理 workspace）
bash scripts/collect.sh
tail -f runs/r1/orchestrator.log

# 6) 构建回放语料
venvs/vllm/bin/python -m freetoken_trace.corpus --run-dir runs/r1
```

### 任务 prompt 与 subagent

每任务的 prompt = 数据集 `problem_statement` + 行为指令（先规划后实施、用 Task
工具派发 subagent 做并行探索）。subagent 流量经 `CLAUDE_CODE_SUBAGENT_MODEL`
映射到同一服务模型——这是 trace 中并发请求的主要来源。模板见
`freetoken_trace/claude_env.py::build_prompt`，可按需修改。

## 采集终止判定（摘要）

driver 每 20s 轮询，按优先级判定：

| 条件 | 结果状态 |
|---|---|
| claude 进程退出（`exit_code.txt` 落盘） | **done**（唯一正常终止） |
| `stream.jsonl` 静默 > `SILENCE_TIMEOUT_MIN` | timeout_silence |
| 从未有输出且静默超时 | no_output |
| 墙钟 > `WALL_TIMEOUT_MIN` | timeout_wall |
| tmux 会话消失 | crashed |
| 退出但 0 次调用 | empty_trace（管道故障） |

结束的权威标志是**进程退出**；协议层标志是 `stream.jsonl` 尾部的 `result` 事件
（`subtype: success` = agent 自认完成，`error_max_turns` = 轮数上限截停）。
完整状态机、每个状态的含义与处理策略见 SUMMARY.md §4。

## 产物

```
runs/<RUN_ID>/
├── tasks/<instance_id>/
│   ├── trace/calls.jsonl        ★ 每 API 调用一条记录（回放主数据源）
│   ├── trace/bodies/            请求/响应全文（gzip）
│   ├── stream.jsonl             claude stream-json 原始输出
│   ├── events.jsonl             规范化事件（工具配对/重试/压缩）
│   └── result.json              结局（status/turns/cost，供下游筛除）
└── corpus/                      回放语料（见下）
```

语料三件套（构建：`freetoken_trace/corpus.py`）：

| 文件 | 用途 |
|---|---|
| `cc_traces.jsonl` | 每 agent 一条 session（`t/in/out/hash_ids/api_time/ttft`），t 对齐任务起点 → 主/子 agent 真实并发结构保留；与 semianalysisai cc-traces 格式同构 |
| `manifest.jsonl` + `blocks.jsonl` | 逐请求记录 + 64-token 文本块池（l2 前缀保真回放） |
| `tuples.jsonl` | (input_len, output_len, tool_gap) 闭环回放单元 |
| `full_trace.jsonl` / `stats.json` | 分析与统计 |

## 闭环并发压测

`replay/replay_loop.py`：C 个槽 = C 个闭环客户端；槽内顺序回放一条轨迹
（发请求 → 等响应 → 睡录制的 tool_gap）→ 轨迹完成立即从语料池取新轨迹。
`--slots C` 配合服务端 `--max-running-requests C`，引擎 decode batch 即压测
并发度；`ignore_eos` 锁定输出 token 数，保证跨系统对比公平。

```bash
venvs/vllm/bin/python replay/replay_loop.py \
  --run-dir results/myrun --manifest runs/r1/corpus/manifest.jsonl \
  --blocks runs/r1/corpus/blocks.jsonl --slots 4 --duration-s 3600
```

注意：**不要用开放式（按时间戳回放）模式压小并发**——语料的到达窗远短于服务
需求，开放回放会无界积压并压垮服务前端；闭环模型让并发自调节到服务能力。

## 配置说明（config.env）

所有参数集中在 `config.env`（bash 与 python 共用解析），从
`config.env.example` 复制后按注释填写：服务端口、模型路径与 served-name、
`MAX_MODEL_LEN`、`CLAUDE_BIN` 路径、超时三参数（`SILENCE/WALL/MAX_TURNS`）、
批量任务数、workspace 清理开关、数据集镜像等。

## 已知注意事项

- Qwen3 系模型配 vLLM 时工具解析用 `--tool-call-parser qwen3_coder`
  （hermes 与 Qwen3-Coder 的 XML 工具格式不匹配）
- 服务端流式需支持/透传 `stream_options.include_usage`，否则回放器用
  逐 chunk 计数兜底
- 数据集与 trace 的再分发需注意上游许可（SWE-bench Pro 数据集与各仓库
  license），公开前自行确认

## 引用

若使用本管道或语料，请引用：SWE-bench Pro (arXiv:2509.16941)；
trace-replay 评估范式：Talaria (arXiv:2607.17181)、VAMP (arXiv:2609.13537)、
Mooncake (arXiv:2407.00079)。
