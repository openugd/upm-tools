#!/bin/sh
# UnityLinker gate for com.openugd.context and corelib's commands and presenters. See README.md or --help.
exec python3 "$(dirname "$0")/lib/linker_gate.py" "$@"
