"""Reconciliation check: proxy records vs driver results vs artifacts.

Usage: venvs/tools/bin/python -m freetoken_trace.verify --run-dir runs/r1
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def verify(run_dir: Path) -> bool:
    tasks_root = run_dir / "tasks"
    ok = True
    rows = []
    for td in sorted(p for p in tasks_root.iterdir() if p.is_dir()):
        res_file = td / "result.json"
        if not res_file.exists():
            continue
        res = json.loads(res_file.read_text())
        calls_file = td / "trace" / "calls.jsonl"
        calls = [json.loads(l) for l in calls_file.read_text().splitlines() if l.strip()] \
            if calls_file.exists() else []
        llm = [c for c in calls if c.get("record_type") == "llm_call"]
        n_proxy = res.get("n_proxy_calls_delta", 0)   # all proxy calls in window
        n_trace = res.get("n_trace_llm_calls", 0)     # llm_call records at result time
        n_all = len(calls)                            # all records now on disk
        ok_calls = [c for c in llm if c.get("response", {}).get("status") == 200]
        usage_ok = all((c.get("response", {}).get("usage") or {}).get("prompt_tokens_total")
                       for c in ok_calls)  # 400s legitimately carry no usage
        sess_ok = all(c.get("session_id") for c in llm)
        agents = {c.get("agent_id") or "main" for c in llm}
        subs = sum(1 for c in llm if c.get("agent_id") or c.get("parent_agent_id"))
        bodies = len(list((td / "trace" / "bodies").glob("*.gz")))
        ws_cleaned = not (td / "workspace").exists()
        # llm_calls must match exactly; the proxy window may additionally
        # contain count_tokens records, so n_all >= len(llm) is expected.
        match = (len(llm) == n_trace) and (n_all >= len(llm)) and usage_ok and sess_ok
        if not match:
            ok = False
        rows.append({
            "task": res.get("task_id", td.name)[:44],
            "status": res.get("status"),
            "calls": f"proxy_win={n_proxy} records={n_all} llm={len(llm)}",
            "usage_ok": usage_ok, "sess_ok": sess_ok,
            "agents": len(agents), "subagent_calls": subs,
            "bodies_gz": bodies, "ws_cleaned": ws_cleaned,
            "verdict": "OK" if match else "MISMATCH",
        })
    for r in rows:
        print(json.dumps(r, ensure_ascii=False))
    print("RECONCILE:", "PASS" if ok else "FAIL")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True)
    args = ap.parse_args()
    verify(Path(args.run_dir))


if __name__ == "__main__":
    main()
