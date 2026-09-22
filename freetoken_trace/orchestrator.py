"""Serial run orchestrator: env prep -> claude driver -> result recording ->
immediate workspace cleanup. Resumable: terminal statuses in manifest.json
are skipped on re-run.

Stages:
  hello  — one pseudo task ("Reply OK") validating the full chain
  smoke  — the 3 smoke tasks from the task list
  batch  — the remaining N batch tasks
"""
from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.request
from pathlib import Path

from .config import Config, load_config
from .dataset import safe_task_id
from .driver import cleanup_workspace, run_task
from .env_setup import prepare_task_workspace

log = logging.getLogger("orchestrator")

TERMINAL = {"done", "empty_trace", "timeout_silence", "timeout_wall",
            "crashed", "no_output", "max_turns", "claude_error", "skipped"}


def http_ok(url: str, timeout: int = 10) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def preflight(cfg: Config) -> None:
    problems = []
    if not http_ok(cfg.proxy_base + "/health"):
        problems.append(f"capture proxy not healthy at {cfg.proxy_base} (start it with scripts/start_proxy.sh)")
    if not http_ok(cfg.vllm_base + "/health"):
        problems.append(f"vLLM not healthy at {cfg.vllm_base} (start it with scripts/serve.sh)")
    if problems:
        raise SystemExit("PREFLIGHT FAILED:\n  " + "\n  ".join(problems))
    log.info("preflight OK: proxy %s, vllm %s", cfg.proxy_base, cfg.vllm_base)


def load_manifest(run_dir: Path) -> dict:
    p = run_dir / "manifest.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"tasks": {}, "started_at": time.time()}


def save_manifest(run_dir: Path, manifest: dict) -> None:
    p = run_dir / "manifest.json"
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


def _hello_task() -> dict:
    return {
        "task_id": "hello",
        "instance_id": "hello",
        "task_type": "hello",
        "repo": None,
        "prompt_override": (
            "Reply with exactly: OK. Do not use any tools, do not explore any "
            "files. This is a connectivity smoke test."
        ),
    }


def run_stage(cfg: Config, stage: str, run_dir: Path,
              num: int | None = None, offset: int = 0) -> None:
    preflight(cfg)
    manifest = load_manifest(run_dir)

    if stage == "hello":
        tasks = [_hello_task()]
    else:
        list_path = cfg.root / "dataset" / f"tasks_smoke{cfg.smoke_tasks}_batch{cfg.num_tasks}.json"
        if not list_path.exists():
            raise SystemExit(f"task list missing: {list_path} (run scripts/prepare_dataset.sh)")
        all_tasks = json.loads(list_path.read_text(encoding="utf-8"))
        if stage == "smoke":
            tasks = [t for t in all_tasks if t["task_type"] == "smoke"]
        else:
            batch = [t for t in all_tasks if t["task_type"] == "batch"]
            if offset:
                batch = batch[offset:]
            tasks = batch[: num or cfg.num_tasks]

    log.info("stage=%s: %d tasks", stage, len(tasks))
    freed_total = 0
    for i, task in enumerate(tasks, 1):
        tid = safe_task_id(task["task_id"])
        prev = manifest["tasks"].get(tid, {})
        if prev.get("status") in TERMINAL:
            log.info("[%d/%d] %s: already terminal (%s), skipping",
                     i, len(tasks), tid, prev["status"])
            continue

        task_dir = run_dir / "tasks" / tid
        log.info("[%d/%d] %s: preparing environment", i, len(tasks), tid)
        env_status = prepare_task_workspace(cfg, task, task_dir)
        manifest["tasks"][tid] = {"status": "env_prepared",
                                  "clone_ok": env_status.get("clone_ok")}
        save_manifest(run_dir, manifest)

        if not env_status.get("clone_ok") and task.get("repo"):
            log.error("[%d/%d] %s: clone failed, skipping collection", i, len(tasks), tid)
            manifest["tasks"][tid] = {"status": "skipped", "reason": "clone_failed"}
            save_manifest(run_dir, manifest)
            continue

        result = run_task(cfg, run_dir, task)
        manifest["tasks"][tid] = result
        save_manifest(run_dir, manifest)

        if result["status"] == "done" and cfg.cleanup_workspace:
            freed = cleanup_workspace(task_dir)
            freed_total += freed
            log.info("[%d/%d] %s: cleaned workspace, freed %.1f MB",
                     i, len(tasks), tid, freed / 1e6)

    statuses: dict[str, int] = {}
    for info in manifest["tasks"].values():
        s = info.get("status", "?")
        statuses[s] = statuses.get(s, 0) + 1
    log.info("stage %s complete. statuses=%s freed_total=%.1f MB",
             stage, statuses, freed_total / 1e6)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=None)
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--stage", choices=["hello", "smoke", "batch"], required=True)
    ap.add_argument("--num", type=int, default=None)
    ap.add_argument("--offset", type=int, default=0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    cfg = load_config(args.root)
    run_dir = Path(args.run_dir) if args.run_dir else cfg.run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)
    run_stage(cfg, args.stage, run_dir, args.num, args.offset)


if __name__ == "__main__":
    main()
