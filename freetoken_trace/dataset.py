"""SWE-bench Pro dataset download (via hf-mirror) and task-list building.

The public test split (731 instances) is a single parquet file; no HF token
or `datasets` library required. Task lists are JSON files consumed by the
orchestrator.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import urllib.request
from pathlib import Path

from .config import Config, load_config

log = logging.getLogger("dataset")

PARQUET_NAME = "test-00000-of-00001.parquet"


def dataset_dir(cfg: Config) -> Path:
    d = cfg.root / "dataset"
    d.mkdir(parents=True, exist_ok=True)
    return d


def download_parquet(cfg: Config, force: bool = False) -> Path:
    dest = dataset_dir(cfg) / PARQUET_NAME
    if dest.exists() and dest.stat().st_size > 1_000_000 and not force:
        log.info("parquet already present: %s (%.1f MB)", dest, dest.stat().st_size / 1e6)
        return dest
    url = f"{cfg.hf_mirror}/datasets/{cfg.dataset_repo}/resolve/main/{PARQUET_NAME}"
    log.info("downloading %s", url)
    # curl -L: hf-mirror 302s to a signed xet CDN URL that urllib mangles
    import subprocess
    tmp = dest.with_suffix(".parquet.tmp")
    proc = subprocess.run(["curl", "-sfL", "--max-time", "600", "-o", str(tmp), url],
                          capture_output=True, text=True)
    if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 1_000_000:
        raise RuntimeError(f"parquet download failed rc={proc.returncode} "
                           f"size={tmp.stat().st_size if tmp.exists() else 0}: {proc.stderr[-300:]}")
    tmp.replace(dest)
    log.info("saved %s (%.1f MB)", dest, dest.stat().st_size / 1e6)
    return dest


def load_instances(cfg: Config) -> list[dict]:
    import pyarrow.parquet as pq

    path = download_parquet(cfg)
    table = pq.read_table(path)
    cols = set(table.column_names)
    rows = table.to_pylist()
    out = []
    for r in rows:
        inst = {k: r.get(k) for k in (
            "instance_id", "repo", "base_commit", "problem_statement",
            "requirements", "interface", "repo_language", "before_repo_set_cmd",
            "dockerhub_tag", "issue_specificity", "issue_categories",
        ) if k in cols}
        if inst.get("instance_id") and inst.get("base_commit"):
            out.append(inst)
    log.info("loaded %d instances from %s", len(out), path)
    return out


def repo_to_url(repo: str) -> str:
    if repo.startswith("http://") or repo.startswith("https://"):
        return repo
    if repo.endswith(".git"):
        return f"https://github.com/{repo}"
    return f"https://github.com/{repo}.git"


def safe_task_id(instance_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "__", instance_id)[:120]


def build_task_list(cfg: Config, num: int, offset: int = 0,
                    smoke_n: int | None = None, out_name: str | None = None) -> Path:
    """Deterministic task list: `smoke_n` shortest-problem instances as smoke,
    then `num` batch instances from `offset` (skipping smoke ones)."""
    smoke_n = cfg.smoke_tasks if smoke_n is None else smoke_n
    instances = load_instances(cfg)
    instances.sort(key=lambda x: str(x["instance_id"]))

    by_len = sorted(instances, key=lambda x: len(str(x.get("problem_statement") or "")))
    smoke = by_len[:smoke_n]
    smoke_ids = {s["instance_id"] for s in smoke}

    # Round-robin across repos so a batch covers as many repos/languages as
    # possible (deterministic: repos sorted by name, instances sorted by id).
    by_repo: dict[str, list[dict]] = {}
    for inst in instances:
        if inst["instance_id"] in smoke_ids:
            continue
        by_repo.setdefault(str(inst["repo"]), []).append(inst)
    repo_queues = [sorted(v, key=lambda x: str(x["instance_id"]))
                   for _, v in sorted(by_repo.items())]
    batch: list[dict] = []
    while len(batch) < num:
        progressed = False
        for q in repo_queues:
            if q and len(batch) < num:
                batch.append(q.pop(0))
                progressed = True
        if not progressed:
            break

    def to_task(inst: dict, task_type: str) -> dict:
        iid = str(inst["instance_id"])
        return {
            "task_id": safe_task_id(iid),
            "instance_id": iid,
            "task_type": task_type,
            "repo": inst.get("repo"),
            "repo_url": repo_to_url(str(inst.get("repo") or "")),
            "base_commit": inst.get("base_commit"),
            "repo_language": inst.get("repo_language"),
            "problem_statement": inst.get("problem_statement"),
            "requirements": inst.get("requirements"),
            "interface": inst.get("interface"),
            "before_repo_set_cmd": inst.get("before_repo_set_cmd"),
            "dockerhub_tag": inst.get("dockerhub_tag"),
        }

    tasks = [to_task(s, "smoke") for s in smoke] + [to_task(b, "batch") for b in batch]
    out = dataset_dir(cfg) / (out_name or f"tasks_smoke{smoke_n}_batch{num}.json")
    out.write_text(json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("wrote %d tasks (%d smoke + %d batch) -> %s", len(tasks), len(smoke), len(batch), out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=None)
    ap.add_argument("--download-only", action="store_true")
    ap.add_argument("--num", type=int, default=None)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--smoke-n", type=int, default=None)
    ap.add_argument("--out-name", default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    cfg = load_config(args.root)
    if args.download_only:
        download_parquet(cfg, force=True)
        return
    build_task_list(cfg, args.num or cfg.num_tasks, args.offset,
                    args.smoke_n, args.out_name)


if __name__ == "__main__":
    main()
