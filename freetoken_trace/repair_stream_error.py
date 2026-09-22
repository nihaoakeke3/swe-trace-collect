"""One-off data repair: null out the benign stream_error recorded on every
streaming call between v1 deployment and the at_eof fix.

The bug: after the upstream SSE generator was fully consumed, the proxy
touched `up_resp.at_eof` (an attribute ClientResponse does not have), so the
except clause stamped `upstream_stream_error: AttributeError('ClientResponse'
object has no attribute 'at_eof')` onto an otherwise complete, successful
stream. Bytes, usage, and timing were unaffected.

Run: venvs/tools/bin/python -m freetoken_trace.repair_stream_error --run-dir runs/r1
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

MARKER = "AttributeError(\"'ClientResponse' object has no attribute 'at_eof'\""


def repair(run_dir: Path) -> None:
    fixed = 0
    kept = 0
    for calls_file in sorted(run_dir.glob("tasks/*/trace/calls.jsonl")):
        lines = calls_file.read_text(encoding="utf-8").splitlines()
        out = []
        for line in lines:
            if not line.strip():
                continue
            rec = json.loads(line)
            se = rec.get("response", {}).get("stream_error")
            if se and MARKER in se:
                rec["response"]["stream_error"] = None
                fixed += 1
            elif se:
                kept += 1
            out.append(json.dumps(rec, ensure_ascii=False))
        calls_file.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"cleaned benign stream_error on {fixed} records; "
          f"{kept} genuine stream errors kept")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True)
    args = ap.parse_args()
    repair(Path(args.run_dir))


if __name__ == "__main__":
    main()
