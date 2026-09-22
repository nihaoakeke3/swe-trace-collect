# SWE-bench Pro Agent Trace 收集与回放压测 — 项目详细总结

> 机器：gpu10（2× RTX 4090 48GB，256 核，1TB RAM）｜完成日期：2026-09
> 配套文档：`README.md`（快速上手命令）、`docs/trace_schema.md`（trace 字段规范）
> 本文面向：要理解数据怎么来的、要复现/扩展/交接这套管道的人。

---

## 目录

1. [目标与总体思路](#1-目标与总体思路)
2. [收集架构与数据流](#2-收集架构与数据流)
3. [项目目录结构](#3-项目目录结构)
4. **采集生命周期与终止判定**（一个任务从启动到落盘的完整状态机）
5. [收集流程六步](#5-收集流程六步)
6. [回放语料格式](#6-回放语料格式corpus)
7. [回放压测设计](#7-回放压测设计freertoken--vllm)
8. [踩坑记录](#8-踩坑记录复用时必读)
9. **新数据采集手册（他人使用指南）**
10. [数据与产物索引](#10-数据与产物索引)
11. [方法论参考](#11-方法论参考)

---

## 1. 目标与总体思路

**目标**：收集真实的 Claude Code agent 在 SWE-bench Pro 任务上工作的全保真 trace
（完整 prompt、工具调用、token 数、时间戳、subagent 并发结构），用于 LLM serving
系统的公平可重复对比实验（closed-loop 并发回放，方法论对齐 Talaria / VAMP /
Mooncake 的 trace-replay 评估范式）。

**两阶段设计**：

```
阶段一：收集（本仓库 swe-trace-collect）
  SWE-bench Pro 任务 → Claude Code(含 subagent) 真实工作
    → 捕获代理记录全部 API 流量 → 全保真 trace → 回放语料

阶段二：压测（配合 ft-agentx-bench）
  回放语料 → FreeToken/vLLM 服务端 → batch 扫描 → TTFT/吞吐/命中率/expert 观测
```

**核心方法论**（与参考方案一致）：
- 所有 API 调用经中间代理，不改 agent 本身；记录每对请求/响应、prompt 与
  completion token 数、工具调用时长（客户端间隔）、墙钟时间
- driver 一次编排一个任务：clone 仓库@base_commit → tmux 内启动 headless agent
  → 监控/超时 → 结局落盘
- 每条轨迹可重建为 (input_length, output_length, tool_gap) 元组序列 +
  64-token 块 hash（保留 prefix-cache 结构）
- 闭环并发回放：C 个槽各回放一条轨迹，轨迹完成立即从语料池取新轨迹，
  固定时间窗聚合指标

**一个重要的口径**：trace 收集的"完成"指**轨迹完整采集**（agent 正常结束或被
确定性策略终止），不要求 issue 被真正解决——收集的是负载形状，解题与否只作为
元数据记录（result.json），供下游实验自行筛除。

---

## 2. 收集架构与数据流

```
tmux 会话（每任务一个，互不干扰）
  claude -p "<issue prompt>" --output-format stream-json --verbose
    --dangerously-skip-permissions --max-turns 60
    │ env: ANTHROPIC_BASE_URL=127.0.0.1:1924（捕获代理）
    │      ANTHROPIC_MODEL = ANTHROPIC_DEFAULT_*_MODEL =
    │      CLAUDE_CODE_SUBAGENT_MODEL = <服务模型名>   ← subagent 流量同源
    ▼
捕获代理 @1924（freetoken_trace/proxy.py，纯记录器）
    │  · SSE 字节原样中继（Claude Code 行为与直连完全一致）
    │  · 每调用一条记录：请求/响应全文(gzip)、usage 四元组、
    │    双时钟时间戳(epoch+monotonic)、TTFT/e2e、
    │    x-claude-code-{session,agent,parent-agent}-id 归因头、
    │    兼容 shim 事件、错误与 retry-after
    ▼ 原样转发（仅兜底重写 model 字段）
vLLM @1923（GPU 1，原生 Anthropic /v1/messages，--enable-prefix-caching）
```

### 为什么这样设计

- **代理是纯记录器不做协议转换**：vLLM 0.11.1+ 原生实现 Anthropic Messages API
  （PR #22627，专为 Claude Code 兼容），代理只记录不加工，保证捕获零失真；
  换任何其它实现了 `/v1/messages` 的引擎（FreeToken 等）代理原样可用
- **归因双通道**：HTTP 头（session/agent/parent-agent-id）+ stream-json 事件的
  `parent_tool_use_id`，语料构建时交叉校验 → 主/subagent 调用树可精确重建
- **双时钟**：epoch ns 用于跨日志对齐（proxy/driver/服务端），monotonic ns 用于
  时长计算
- **任务间完全隔离**：独立 workspace（`git clone --filter=blob:none` 部分克隆）+
  独立任务 venv + 独立 CLAUDE_CONFIG_DIR（预置 onboarding）+ 独立 tmux 会话
- **子代理并发**：prompt 明确要求用 Task 工具派发 subagent；实测（FreeToken 论文
  W3 同款场景）会话长到 57-130k tokens、峰值 57 路并发请求

---

## 3. 项目目录结构

```
/home/jinshuai/code/swe-trace-collect/
├── README.md                  # 快速上手与命令
├── SUMMARY.md                 # 本文
├── config.env                 # 唯一配置源（端口/模型/超时/批量 N，bash+python 共用）
├── docs/
│   └── trace_schema.md        # trace 字段规范（schema_version 1.0）
├── scripts/
│   ├── setup_vllm.sh          # 建隔离 venv + 装 vllm 0.29.0 / aiohttp / pyarrow
│   ├── prepare_dataset.sh     # hf-mirror 下载 SWE-bench Pro + 生成任务清单
│   ├── serve.sh               # GPU1 起 vLLM（KV 容量实测 + max-model-len 自动回退）
│   ├── stop_serve.sh / start_proxy.sh / stop_proxy.sh
│   ├── run_smoke.sh           # 冒烟：hello + 3 SWE 任务 + 语料试构建
│   ├── collect.sh             # 批量收集（tmux 控制器，断点续跑）
│   └── clean.sh               # 磁盘卫生（dry-run 默认；删已完成任务 workspace）
├── freetoken_trace/           # 核心 Python 包
│   ├── config.py              # 解析 config.env
│   ├── dataset.py             # 数据集下载 + 任务清单（仓库轮转采样保证多样性）
│   ├── env_setup.py           # clone@base_commit + 尽力装环境（失败不阻塞）
│   ├── claude_env.py          # Claude Code env 映射 + 隔离配置目录 + prompt 构造
│   ├── proxy.py               # ★ 捕获代理（含 --selftest 自测模式）
│   ├── driver.py              # ★ 单任务驱动（tmux + 终止判定 + result/events 解析）
│   ├── orchestrator.py        # 串行编排 + manifest 断点续跑 + 完成即清
│   ├── corpus.py              # trace → 回放语料（cc-traces 兼容 + 元组 + hash_ids）
│   ├── verify.py              # 对账：proxy/driver/artifact 三方一致性
│   └── repair_stream_error.py # 一次性数据修复工具（历史遗留字段清洗）
├── dataset/                   # SWE-bench Pro parquet + tasks_*.json
├── venvs/{tools,vllm}/        # 隔离环境（不动机器上其它 venv）
└── runs/<RUN_ID>/             # ★ 全部产物（结构见 §10）
```

配合使用的既有设施（不在本仓库内）：
`/home/jinshuai/code/ft-agentx-bench/`（FreeToken 回放基准：serve.sh / gpu_guard.sh /
run_replay.sh / replay_cc.py / replay_loop.py / analysis/*）与
`/home/jinshuai/code/FreeToken/`（服务引擎，含本项目加的 expert-trace 插桩）。

---

## 4. 采集生命周期与终止判定（详细）

一个任务从进入 orchestrator 到产出最终 `result.json`，由 `driver.py::run_task`
驱动，经历 **准备 → 运行 → 终止判定 → 结算** 四个阶段。所有时间/轮数参数都在
`config.env`，可按需调整。

### 4.1 阶段一：准备（run_cl 前置）

1. `_write_launch_files`：写 `env.sh`（Claude Code 环境变量）、`prompt.txt`
   （problem_statement + 行为指令）、`launch.sh`（tmux 内执行的命令）
2. **向捕获代理登记当前任务**：`POST /__task {"task_id":...}` —— 代理把此后的
   每条 API 记录归属到该任务（串行收集下这是安全的）
3. 清理同名 tmux 旧会话与上次的 `stream.jsonl / exit_code.txt`
4. 记录代理当前调用计数（用于结算时对账增量）

### 4.2 阶段二：运行

tmux 启动 `launch.sh`，其核心是：

```bash
claude -p "$(cat prompt.txt)" --output-format stream-json --verbose \
  --dangerously-skip-permissions --max-turns 60 \
  > stream.jsonl 2> claude.stderr.log
echo $? > exit_code.txt          # ← 进程终止的权威标志
```

driver 主循环每 **20 秒**轮询一次，按顺序检查：

| 优先级 | 检查项 | 依据 | 结果状态 |
|---|---|---|---|
| 1 | `exit_code.txt` 存在？ | claude 进程已退出 | **done**（唯一正常终止） |
| 2 | `stream.jsonl` 有新字节？ | mtime/size 变化 | 无变化则累计静默时长 |
| 3 | 静默 > `SILENCE_TIMEOUT_MIN`(30min) 且曾有输出 | agent 卡死/网络僵死 | **timeout_silence** |
| 4 | 静默 > 30min 且**从未有输出** | claude 启动失败（路径/env/端口） | **no_output** |
| 5 | 墙钟 > `WALL_TIMEOUT_MIN`(150min) | 兜底上限 | **timeout_wall** |
| 6 | tmux 会话消失且无 exit_code | 异常死亡 | **crashed** |

### 4.3 阶段三：终止动作

- 状态为 `done` → 不做任何干预，等进程自然退出
- 其它状态 → **主动终止**：`tmux list-panes` 取 pane_pid → `kill -TERM -- -<pgid>`
  杀整个进程组 → 3 秒后 `kill -KILL` 兜底 → `tmux kill-session`
  （杀进程组是为了连带 claude 派生的 shell/工具子进程，不留残留）

### 4.4 阶段四：结算（无论哪种终止都会执行）

1. 读 `exit_code.txt` → `result.exit_code`
2. 解析 `stream.jsonl`：最后一个 `result` 事件（`subtype`：`success` /
   `error_max_turns` / …，`num_turns`、`total_cost_usd`、`session_id`）、
   `system/api_retry` 事件数、tool_use→tool_result 配对（写 `events.jsonl`）、
   上下文自动压缩事件计数
3. 读代理 `__status` 与 `trace/calls.jsonl` 行数 → 双向对账写入 result
4. **数据完整性终检**：若 `status==done` 但 `trace/calls.jsonl` 无 llm_call
   记录 → 状态改判 **empty_trace**（管道故障，需人工介入）
5. 写 `result.json`；orchestrator 更新 `manifest.json`

### 4.5 终止判定相关的问题与答案

**Q：一条轨迹"结束"的权威标志是什么？**
三层，从强到弱：
1. **进程层**：`claude` 进程退出且 `exit_code.txt` 写入（唯一正常终止路径）
2. **协议层**：`stream.jsonl` 尾部的 `result` 事件（`subtype: success` =
   agent 自认完成；`error_max_turns` = 被 `--max-turns` 截停）
3. **数据层**：`trace/calls.jsonl` 中该 (session, agent) 的最后一条记录
   —— 语料构建即以此分组定界（一个 agent = 一条轨迹 = 一条 cc-traces session）

**Q：怎么判断"任务没完成"vs"采集没完成"？**
- `result.subtype=error_max_turns` / `is_error=true` → **任务没做完**（agent 被
  轮数预算截停），但**轨迹是完整的**（所有调用都捕获了）
- `status ∈ {timeout_silence, timeout_wall, crashed}` → **采集被终止**（轨迹尾部
  缺失），这类任务在语料里标记完整与否供下游筛选
- `empty_trace` / `skipped(clone_failed)` → 采集失败，无有效轨迹

**Q：为什么要三个超时？**
静默超时针对"agent 卡住"（网络僵死、工具挂起）；墙钟针对"agent 一直有输出但
永不收敛"（长任务兜底）；`--max-turns` 是 claude CLI 层的软上限（防止无限
规划-执行循环）。三者独立触发，任何一个命中都会得到一条**已终止但已落盘**的
trace——这正是与参考方案"轨迹文件静默超时即终止"一致的策略。

**Q：参数怎么调？**（config.env）

| 参数 | 默认 | 调整建议 |
|---|---|---|
| `SILENCE_TIMEOUT_MIN` | 30 | 服务端很慢（长 prefill 排队）时调大 |
| `WALL_TIMEOUT_MIN` | 150 | 换大模型/长任务时调大 |
| `MAX_TURNS` | 60 | 参考方案用了 50-turn 上限；调小截停更早、轨迹更短 |
| 轮询间隔 | 20s（代码内） | 一般不动 |

### 4.6 result.json 状态字典（orchestrator/下游以此为准）

| status | 含义 | workspace 处理 | trace 有效性 |
|---|---|---|---|
| done | claude 正常退出 | **立即删除**（省空间） | 完整 |
| timeout_silence | 静默超时终止 | 保留（可调试） | 完整性需核验 |
| timeout_wall | 墙钟终止 | 保留 | 同上 |
| crashed / no_output | 进程异常 | 保留 | 部分或无效 |
| empty_trace | 退出但 0 次调用 | 保留 | 无效（管道故障） |
| skipped | clone 等前置失败 | 无 | 无轨迹 |

断点续跑规则：`orchestrator` 把状态写进 `manifest.json`，重跑 `collect.sh` 时
**跳过一切终态任务**，只重跑非终态（含 env 阶段失败的 clone_failed→skipped 除外）。

---

## 5. 收集流程六步

1. **数据集**：`ScaleAI/SWE-bench_Pro` test split（731 实例，非 gated）经
   hf-mirror 下载 parquet（必须 `curl -L`：签名 302 用 urllib 会 404）。
   任务清单 = 3 个最短问题作冒烟 + N 个正式任务按**仓库轮转**采样（保证 11 仓库、
   4 语言均匀覆盖，避免单一仓库偏斜）。
2. **环境**：每任务 `git clone --filter=blob:none` + checkout base_commit →
   Python 项目尽力建 venv + `pip install -e .`（pypi 直连/清华源回退）→
   失败记录 `env_status.json` 不阻塞。
3. **Agent**：headless `claude -p`，prompt = problem_statement + "先规划后实施 +
   用 Task 工具派发 subagent 做并行探索"。
4. **捕获**：代理逐调用落盘（字段见 `docs/trace_schema.md`）。
5. **监控**：见 §4（静默/墙钟/turn 三重终止）。
6. **清理与对账**：done 即删 workspace；`verify.py` 对账 proxy↔driver↔artifact
   调用数、usage 完整性、session 归因。

### 本次收集结果（runs/r1）

- 24 任务全部 `done`（1 hello + 3 冒烟 + 20 正式批量，无人值守 6.5h）
- **1177 次 LLM 调用 / 792 turns / 84 个 agent session**，subagent 调用占 39.6%
- 负载形状：input p50=47.6k、p90=105k、max=197k tokens；output max 32k；
  tool gap p50=0.047s、max=318s；**峰值 57 路并发**（subagent 风暴）
- 结局：21 success（agent 自认完成）、2 error_max_turns（turns 62/79）
- RECONCILE PASS：调用数三方对账一致、usage/归因零缺失
- 磁盘：trace+语料 169MB（workspace 即时清理，共释放 3.7GB）

---

## 6. 回放语料格式（corpus/）

| 文件 | 内容 | 消费方 |
|---|---|---|
| `cc_traces.jsonl` | 每 agent 一条 session：`{id, models, block_size:64, requests:[{t,in,out,hash_ids,api_time,type,ttft}]}`，t 对齐任务起点 → 主/子 agent 真实并发重叠结构保留 | ft-agentx-bench `replay_cc.py`（semianalysisai cc-traces 同构） |
| `manifest.jsonl` + `blocks.jsonl` | 逐请求 manifest + 82,656 个去重 64-token 文本块 | l2 前缀保真回放（块文本重建 prompt）、闭环回放器 |
| `tuples.jsonl` | (input, output, tool_gap) 逐调用元组，per-agent 序列 | closed-loop 压测单元 |
| `full_trace.jsonl` | 增强逐调用记录（归因树/时序/usage/body 引用） | 分析 |
| `stats.json` | 分布/并发峰值/subagent 占比 | 报告 |

hash_ids：tokenizer 按 64-token 块对规范化 prompt（system+messages+tools）哈希，
同前缀同 id → 回放保留 radix/prefix-cache 复用结构。轨迹边界定义：**一个 agent
（session_id + agent_id）的全部成功调用 = 一条轨迹**；任务级 = 该 task 下主 agent
与全部 subagent 轨迹的集合（共享任务起点时间基线）。

---

## 7. 回放压测设计（FreeToken / vLLM）

### 闭环并发驱动器 `ft-agentx-bench/replay/replay_loop.py`

- C 个 asyncio 槽 = C 个闭环客户端；槽内顺序回放一条 session：发请求
  （prompt 由 gids×blocks 重建，`ignore_eos` 锁定输出 token 数）→ 等响应 →
  睡 tool_gap（capped 10s，Talaria 惯例）→ 下一请求
- session 耗尽立即从池取新轨迹（重新洗牌）；产物与 replay_cc/make_report 兼容
- **为什么放弃 open-loop**：语料把 ~7.2h 服务需求压进 37min 到达窗，叠加负载下
  连接层崩溃（实测 349×ConnectError）；闭环模型让并发自调节到服务能力

### FreeToken 引擎插桩（env 门控，默认零开销）

改动了 4 个文件（原文件备份 `/tmp/ft_orig_backup/`）：
- 新增 `engine/expert_trace.py`：调度器与 MoE 层共享的缓冲单例
- `engine/metrics_log.py`：三产物 + 后台排干
  - `expert_routing.jsonl`：逐 decode step × 逐层 **router top-8 原始 expert id**
    （在 `ensure_experts` 原地改写 slot id **之前**捕获，仅 eager 路径）
  - `decode_batches.jsonl`：每 step 的 batch uid 组成（与路由按 forward_iter join）
  - `expert_cache.jsonl`：每 2s 的 **expert cache 驻留 expert id 集**（`id_of_slot`
    一次 D2H，graph 安全，性能臂也开启）
- `scheduler/scheduler.py`：`_prepare_batch` 挂 batch 记录钩子
- `layers/moe.py`：`_decode_routed` 挂 topk 捕获钩子
- 另：`server/supervisor.py` 死亡日志带 exitcode；`tokenizer/server.py` 韧性补丁
  （detokenize 异常降级为日志+错误回复，未知消息类型跳过——不再一崩全服）

### batch 扫描矩阵（scripts/sweep_cl.sh，8 轮 × 70min 时间窗）

| 轮次 | 槽位 | CUDA graph | 产出 |
|---|---|---|---|
| clsweep_bs{1,2,4,6} | 1/2/4/6 | 开 | TTFT/TPOT/吞吐/KV 命中/expert miss/驻留快照 |
| cltrace_bs{1,2,4,6} | 1/2/4/6 | 仅 bs=1 | 逐 step router expert id（bs≥2 eager 全捕获） |

服务端统一：`--moe-cache-auto --kv-reserve-tokens 230000 --memory-ratio 0.90
--max-prefill-length 16384 --moe-prefill-hit-d2d --enable-cache-report`，
`--max-running-requests = 槽位数`。每轮前后 SIGKILL 清理 + 端口占用断言。

汇总：`analysis/sweep_compare.py` → `sweep_report.{json,md}`
（逐 step distinct experts、top-16 集中度、routed∩resident 重叠率）。

---

## 8. 踩坑记录（复用时必读）

1. **工具调用解析**：Qwen3.6 系用 Qwen3-Coder XML 格式（`<tool_call><function=..>`），
   必须配 `--tool-call-parser qwen3_coder`（hermes 会 JSONDecodeError）。
   vLLM 侧同理（0.29 注册名相同）。
2. **moe-cache-auto 过贪**：FP8+48GB 会把全部 10,240 专家（31.5GB）解析进显存，
   eager prefill 瞬时分配 OOM → `--memory-ratio 0.90` 留 4.3GB 工作区。
   （副作用：全量专家驻留后 expert miss ≈ 0，命中率会饱和——报告已注明）
3. **open-loop 过载**：到达窗 << 服务需求时前端连接层崩溃（实测 349×ConnectError
   + 503 maintenance）；压测一律用闭环槽。
4. **全 eager 慢 100×**：`--cuda-graph-max-bs 0` 跳过 attention 后端初始化，
   prefill 掉到 117 tok/s；tracing 臂用 `--cuda-graph-bs 1` 替代
   （bs=1 走 graph，bs≥2 eager，路由捕获覆盖全部 eager 步）。
5. **流式 usage**：FreeToken 需显式 `stream_options.include_usage` 才回传
   completion tokens；驱动器同时做逐 chunk 计数兜底。
6. **服务自杀（SIGTERM 链）**：FreeToken supervisor 策略是"任一 worker 死→全服
   停止"（detokenizer worker 曾收到外部 -15 导致连锁停止，exitcode 已加入日志）。
   已加韧性补丁：detokenize 异常降级为日志+错误回复、未知消息类型跳过。
   若复现死亡，`engine.log` 的 `exited exitcode=` 行是第一线索。
7. **hf-mirror 下载**：resolve 端点 302 到签名 URL，必须 `curl -L`。
8. **进程清理**：stop_run.sh 的 SIGTERM 可能被"等待连接关闭"挂住产生端口僵尸；
   sweep 脚本每轮前后做 SIGKILL 清理 + 端口占用断言（避免下一轮连到僵尸——
   实际发生过：僵尸应答健康检查导致整轮数据作废）。
9. **采集侧数据修复模式**：发现历史字段污染时写一次性修复脚本
   （`repair_stream_error.py`）而非手工改文件，保证可追溯。

---

## 9. 新数据采集手册（他人使用指南）

本章假设你在 gpu10（或任何有 Claude Code CLI + NVIDIA GPU + 网络可达
pypi/github/hf-mirror 的 Linux 机器）上从零跑一遍采集。

### 9.0 前置条件清单

| 依赖 | 检查 | 说明 |
|---|---|---|
| Claude Code CLI ≥2.x | `claude --version` | 装在 `~/.npm-global/bin/` 亦可 |
| NVIDIA GPU 空闲 | `nvidia-smi` | 默认用 GPU 1，改 `config.env:GPU_ID` |
| python3 + venv 模块 | `python3 -m venv /tmp/_t && rm -rf /tmp/_t` | 失败则需 conda |
| 网络 | github.com / hf-mirror.com / pypi.org 可达 | 直连或自带镜像 |
| tmux | `tmux -V` | 长跑必须 |
| 磁盘 | ≥20GB（venv+模型+产物） | trace 本身很小 |
| 一个能服务 Anthropic `/v1/messages` 的引擎 | vLLM ≥0.11.1 或 FreeToken | 见 9.3 |

### 9.1 首次部署（一次性，~30 分钟）

```bash
git clone <本仓库>  ~/swe-trace-collect && cd ~/swe-trace-collect
# （或直接 rsync 现有目录，venvs 可跳过重建）

# 1) 改配置：至少确认 GPU_ID / MODEL_PATH / SERVED_MODEL_NAME / 端口
vim config.env

# 2) 装环境（隔离 venv，不影响系统）
bash scripts/setup_vllm.sh          # vllm 0.29.0 + aiohttp/pyarrow

# 3) 下载数据集 + 生成任务清单
bash scripts/prepare_dataset.sh     # 默认 3 冒烟 + 20 正式
```

### 9.2 定制你要采集的任务

`scripts/prepare_dataset.sh` 底层是：

```bash
venvs/tools/bin/python -m freetoken_trace.dataset \
  --num 50 --offset 0 --smoke-n 3 --out-name tasks_my50.json
```

- `--num N --offset M`：仓库轮转采样取第 M..M+N 个（断点续采新批次）
- 换任务集：直接编辑 `dataset/tasks_*.json`（JSON 数组，字段见
  `freetoken_trace/dataset.py::to_task`），或换 `DATASET_REPO`（任意
  SWE-bench 风格 parquet：需要 instance_id/repo/base_commit/problem_statement）
- 指定 instance：写个只含目标实例的 json 传给 orchestrator（见 9.4 的
  `--stage` 机制；也可复制 run_smoke.sh 改 stage 选择逻辑）
- 语言过滤：改 `dataset.py::build_task_list` 加 `repo_language` 过滤

### 9.3 起服务端（两种已验证方案）

**方案 A：vLLM（推荐先跑通）**

```bash
bash scripts/serve.sh      # 读 config.env：MODEL_PATH/PORT=1923/MAX_MODEL_LEN
bash scripts/start_proxy.sh
curl -s http://127.0.0.1:1923/health && curl -s http://127.0.0.1:1924/health
```

**方案 B：FreeToken**（Anthropic 端点原生，含 expert 观测插桩）

```bash
export PATH=/home/jinshuai/code/FreeToken/.venv/bin:$PATH
FREETOKEN_METRICS_LOG=$PWD/runs/$RUN_ID FREETOKEN_EXPERT_TRACE=0 \
  ft serve --model $MODEL_PATH --gpu $GPU_ID --port 1921 \
  --max-running-requests 8 --moe-cache-auto --kv-reserve-tokens 230000 \
  --memory-ratio 0.90 --max-prefill-length 16384 --moe-prefill-hit-d2d \
  --enable-cache-report
# 注意：FreeToken 时把 config.env 的 PROXY_UPSTREAM 指到 1921，
# 或临时起一个指向 1921 的代理实例（代理与引擎解耦，只需端口可达）
```

要点：**引擎只要实现 `/v1/messages`(+`/count_tokens`)、支持 tools 与流式**即可；
服务端慢没关系，采集侧有 ping 兜底与超时参数。

### 9.4 执行采集

```bash
# 冒烟（强烈建议第一次必跑）：1 个 hello + 3 个 SWE 任务，~1.5h
bash scripts/run_smoke.sh

# 检查冒烟产物（对账必须 PASS）
venvs/tools/bin/python -m freetoken_trace.verify --run-dir runs/$RUN_ID

# 正式批量（tmux 内无人值守；断线不受影响）
bash scripts/collect.sh
tail -f runs/$RUN_ID/orchestrator.log        # 实时进度
```

单任务时长参考：本机（4090 + 35B MoE）每任务 10~35 分钟；串行 N 任务 ≈ N×25min。
中断后**直接重跑 `collect.sh`** 即续传（manifest 终态任务自动跳过）。

### 9.5 采集期间巡检什么

```bash
grep -E "finished|cleaned" runs/$RUN_ID/orchestrator.log | tail   # 完成与清理
curl -s http://127.0.0.1:1924/__status                            # 代理视角当前任务/调用数
nvidia-smi -i $GPU_ID --query-compute-apps=pid --format=csv,noheader
```

健康信号：watermark/调用数持续增长、无 Traceback、workspace 完成即被删。

### 9.6 采集完成后的验收清单

1. `verify.py` 输出 RECONCILE **PASS**（调用数三方一致、usage/session 齐全）
2. 抽查一个任务：`result.json` 的 status/turns、`trace/calls.jsonl` 行数与
   `stream.jsonl` 的 assistant 事件数对得上
3. `corpus/stats.json`：subagent 占比 >0（确认 subagent 流量被采到）
4. `clean.sh`（dry-run）确认无残留 workspace
5. 人工抽读一条：`zcat tasks/<id>/trace/bodies/req-000001.json.gz | python3 -m json.tool`

### 9.7 常见失败与处理

| 现象 | 原因 | 处理 |
|---|---|---|
| 大量 `no_output` | 引擎没起/端口不通/claude 不在 PATH | 看 `claude.stderr.log` 与 `curl 引擎/health` |
| `empty_trace` 批量出现 | 代理与引擎之间断了 | 先 `proxy --selftest`，再查 BASE_URL 链路 |
| TTFT 巨大、请求堆积 | 服务端过载（开环思维残留） | 确认闭环 `--slots = --max-running-requests` |
| 服务 worker 死亡连锁停止 | 见 §8.6 | 看 `exited exitcode=` 行；韧性补丁是否在 |
| 磁盘涨 | 清理没生效 | `bash scripts/clean.sh`（dry-run 预览后 --apply） |
| trace 里 usage 缺失 | 引擎没回 usage | l2 回放用逐 chunk 计数兜底；查引擎流式实现 |

### 9.8 换模型 / 换引擎 / 换负载

- **换模型**：改 `config.env`（MODEL_PATH/SERVED_MODEL_NAME），重启 serve；
  语料构建器自动用新 tokenizer 重算 hash_ids。建议新模型先跑冒烟再批量
- **换引擎**：只要 Anthropic `/v1/messages` 兼容即可；不兼容字段由代理的
  400-shim 自动重试剥离（`thinking/metadata` 与 server-side tools）
- **换负载强度**：`MAX_TURNS` 控制轨迹长度；prompt 模板在
  `claude_env.py::build_prompt`（增删 subagent 指令会显著改变并发形状）
- **采集并发**：默认串行（时序最干净）。要加速可改 orchestrator 并行度，
  但注意代理归因按"当前任务"切换，并行会破坏归因——如需并行请给每任务
  独立代理实例

---

## 10. 数据与产物索引

```
/home/jinshuai/code/swe-trace-collect/runs/r1/            # 收集产物（本仓库）
/home/jinshuai/code/swe-trace-collect/runs/r1/corpus/     # 回放语料
/home/jinshuai/code/ft-agentx-bench/results/clsweep_bs*/  # FreeToken 闭环压测（性能臂）
/home/jinshuai/code/ft-agentx-bench/results/cltrace_bs*/  # 逐 step expert 路由（tracing 臂）
/home/jinshuai/code/ft-agentx-bench/analysis/sweep_compare.py   # 汇总对比
```

历史版本目录带后缀：`*_v1_badout`（usage 计数缺陷期）、`*_v2_zombie` /
`*_v3_detok` / `*_v4_sigterm`（服务稳定性排查期的受污染轮次）——**不要混入分析**。

---

## 11. 方法论参考

- Claude Code gateway 协议与 headless 驱动：code.claude.com/docs
  （llm-gateway-protocol / headless / model-config）
- Trace-replay 服务评估：Talaria (arXiv:2607.17181)、VAMP (arXiv:2609.13537)、
  The Replay Gap (arXiv:2608.08239)、Mooncake (arXiv:2407.00079)
- 语料格式：semianalysisai/cc-traces（block_size=64 + hash_ids 惯例）
- SWE-bench Pro：arXiv:2509.16941，ScaleAI/SWE-bench_Pro (HF)，
  scaleapi/SWE-bench_Pro-os (GitHub)
- FreeToken：arXiv:2608.16157（本机部署 v0.1.2）
