#!/bin/sh
# Fast-forward the v2 branches in the main checkouts (dry run unless --apply; never pushes). See README.md.
exec python3 "$(dirname "$0")/lib/finish.py" "$@"
