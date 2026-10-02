#!/bin/sh
# Level-1 gate (per commit, no Unity licence). See README.md or run with --help.
exec python3 "$(dirname "$0")/lib/level1.py" "$@"
