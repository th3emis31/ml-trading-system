#!/usr/bin/env bash
# Start Claude Code in THIS project, whatever directory you are in when you run it.
# See start.cmd for why. Same guarantee: the working directory is always the project root.
cd "$(dirname "$0")" || exit 1
echo "Starting Claude in $(pwd)"
exec claude "$@"
