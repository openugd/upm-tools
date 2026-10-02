#!/bin/sh
# Compatibility entry point kept from the seed harness; level1.sh in the repository root is the real gate
# and runs every level-1 step. This runs the two steps the seed ran: the asmdef-faithful build of every
# runtime/editor/test asmdef (editor and player variants) and the engine-free tests. Arguments pass through
# (--packages, --root, --out, --unity ...); OPENUGD_HARNESS_OUT still selects the output folder.
exec "$(dirname "$0")/../level1.sh" --steps build,tests "$@"
