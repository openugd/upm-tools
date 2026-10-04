#!/bin/sh
# Level-2 gate (real Unity in batchmode; needs an installed editor with an activated licence). See README.md or run
# with --help.
exec python3 "$(dirname "$0")/lib/level2.py" "$@"
