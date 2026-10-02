#!/bin/sh
# UnityLinker gate for com.openugd.context (audit P0-4). See README.md or run with --help.
exec python3 "$(dirname "$0")/lib/linker_gate.py" "$@"
