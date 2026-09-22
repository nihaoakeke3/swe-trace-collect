#!/usr/bin/env bash
set -u
cd "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/hello/workspace" || exit 97
source "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/hello/env.sh"
"/home/jinshuai/.npm-global/bin/claude" -p "$(cat "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/hello/prompt.txt")" \
  --output-format stream-json --verbose \
  --dangerously-skip-permissions \
  --max-turns 60 \
  > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/hello/stream.jsonl" 2> "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/hello/claude.stderr.log"
echo $? > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/hello/exit_code.txt"
