"""Per-task workspace preparation: partial git clone at base_commit +
best-effort environment install inside an isolated per-task venv.

Everything lives under tasks/<task_id>/workspace so cleanup is a single
`rm -rf`. Failures are recorded (env_status.json) and never block collection:
traces remain valid load shapes even when the environment is imperfect.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from .config import Config
from .dataset import safe_task_id

log = logging.getLogger("env_setup")


def _run(cmd: list[str] | str, cwd: Path | None = None, timeout: int = 600,
         shell: bool = False, env: dict | None = None) -> dict:
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, shell=shell, env=env,
            capture_output=True, text=True, timeout=timeout,
        )
        return {
            "cmd": cmd if isinstance(cmd, str) else " ".join(cmd),
            "rc": proc.returncode,
            "stdout_tail": (proc.stdout or "")[-2000:],
            "stderr_tail": (proc.stderr or "")[-2000:],
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "cmd": cmd if isinstance(cmd, str) else " ".join(cmd),
            "rc": None,
            "stdout_tail": (e.stdout or "")[-2000:] if isinstance(e.stdout, str) else "",
            "stderr_tail": (e.stderr or "")[-2000:] if isinstance(e.stderr, str) else "",
            "timed_out": True,
        }
    except Exception as e:  # noqa: BLE001
        return {"cmd": str(cmd), "rc": None, "stdout_tail": "",
                "stderr_tail": repr(e), "timed_out": False}


def _clone(url: str, commit: str, ws: Path, task_log: Path) -> dict:
    steps: list[dict] = []

    def record(step: dict) -> dict:
        steps.append(step)
        with open(task_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(step, ensure_ascii=False) + "\n")
        return step

    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)
    ws.parent.mkdir(parents=True, exist_ok=True)

    # 1) partial clone (commits+trees only; blobs fetched on checkout)
    r = _run(["git", "clone", "--filter=blob:none", "--no-checkout", url, str(ws)],
             timeout=1200)
    record(r)
    if r["rc"] != 0:
        # 2) fallback: fetch-by-sha shallow init
        if ws.exists():
            shutil.rmtree(ws, ignore_errors=True)
        ws.mkdir(parents=True)
        r2 = _run(["git", "init"], cwd=ws, timeout=60)
        record(r2)
        r3 = _run(["git", "remote", "add", "origin", url], cwd=ws, timeout=60)
        record(r3)
        r4 = _run(["git", "fetch", "--depth", "1", "origin", commit], cwd=ws, timeout=1800)
        record(r4)
        if r4["rc"] != 0:
            return {"clone_ok": False, "steps": steps}
        r5 = _run(["git", "checkout", "-f", "FETCH_HEAD"], cwd=ws, timeout=600)
        record(r5)
        if r5["rc"] != 0:
            return {"clone_ok": False, "steps": steps}
    else:
        r6 = _run(["git", "checkout", "-f", commit], cwd=ws, timeout=1200)
        record(r6)
        if r6["rc"] != 0:
            return {"clone_ok": False, "steps": steps}

    head = _run(["git", "rev-parse", "HEAD"], cwd=ws, timeout=30)
    record(head)
    return {"clone_ok": True, "checked_out": head["stdout_tail"].strip(),
            "steps": steps}


def _python_env(ws: Path, task: dict, task_log: Path) -> dict:
    steps: list[dict] = []

    def record(step: dict) -> dict:
        steps.append(step)
        with open(task_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(step, ensure_ascii=False) + "\n")
        return step

    venv = ws / ".venv-swe"
    r = _run(["python3", "-m", "venv", str(venv)], timeout=300)
    if r["rc"] != 0:
        r = _run(["/home/jinshuai/miniconda3/bin/conda", "create", "-y", "-p",
                  str(venv), "python=3.12"], timeout=900)
    record(r)
    venv_ok = r["rc"] == 0 and (venv / "bin").exists()
    if not venv_ok:
        return {"venv_ok": False, "steps": steps}

    pip = str(venv / "bin" / "pip")
    activate_prefix = f"source {venv}/bin/activate && "

    setup_cmd = task.get("before_repo_set_cmd")
    setup_status = None
    if setup_cmd:
        s = _run(["bash", "-lc", activate_prefix + str(setup_cmd)],
                 cwd=ws, timeout=900, shell=False)
        record(s)
        setup_status = {"rc": s["rc"], "timed_out": s["timed_out"]}

    install = None
    if (ws / "setup.py").exists() or (ws / "pyproject.toml").exists():
        install = _run(["bash", "-lc", activate_prefix + "pip install -e . --no-build-isolation"],
                       cwd=ws, timeout=1500, shell=False)
        if install["rc"] != 0:
            install = _run(["bash", "-lc", activate_prefix + "pip install -e ."],
                           cwd=ws, timeout=1500, shell=False)
        record(install)

    return {
        "venv_ok": True,
        "setup_cmd": setup_status,
        "pip_install": None if install is None else {"rc": install["rc"], "timed_out": install["timed_out"]},
        "steps": steps,
    }


def prepare_task_workspace(cfg: Config, task: dict, task_dir: Path) -> dict:
    """Clone + best-effort env; returns env_status and writes env_status.json."""
    task_dir.mkdir(parents=True, exist_ok=True)
    task_log = task_dir / "env_setup.log"
    status: dict = {
        "task_id": task["task_id"],
        "repo": task.get("repo"),
        "repo_url": task.get("repo_url"),
        "base_commit": task.get("base_commit"),
        "repo_language": task.get("repo_language"),
    }

    if not task.get("repo"):  # pseudo tasks (hello) — empty workspace
        ws = task_dir / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        status.update({"clone_ok": True, "checked_out": None, "venv_ok": False,
                       "pseudo": True})
        (task_dir / "env_status.json").write_text(
            json.dumps(status, indent=1), encoding="utf-8")
        return status

    ws = task_dir / "workspace"
    clone = _clone(task["repo_url"], str(task["base_commit"]), ws, task_log)
    status.update(clone)
    if clone.get("clone_ok") and str(task.get("repo_language") or "").lower().startswith("python"):
        status["python_env"] = _python_env(ws, task, task_log)
    ws_size = sum(f.stat().st_size for f in ws.rglob("*") if f.is_file()) if ws.exists() else 0
    status["workspace_bytes"] = ws_size
    (task_dir / "env_status.json").write_text(json.dumps(status, indent=1), encoding="utf-8")
    log.info("task %s: clone_ok=%s venv status=%s",
             task["task_id"], status.get("clone_ok"), status.get("python_env", {}).get("venv_ok", "n/a"))
    return status


__all__ = ["prepare_task_workspace", "safe_task_id"]
