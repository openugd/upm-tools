#!/bin/sh
# UnityLinker gate for com.openugd.context and corelib's commands and presenters (audit P0-4). See README.md or --help.
exec python3 "$(dirname "$0")/lib/linker_gate.py" "$@"
