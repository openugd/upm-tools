#!/bin/sh
# Older entry point, kept for scripts that still call it; level1.sh in the repository root is the real gate
# and runs every level-1 step. This runs two of them: the asmdef-faithful build of every
# runtime/editor/test asmdef (editor and player variants) and the engine-free tests. Arguments pass through
# (--packages, --root, --out, --unity ...); OPENUGD_HARNESS_OUT still selects the output folder.
exec "$(dirname "$0")/../level1.sh" --steps build,tests "$@"
