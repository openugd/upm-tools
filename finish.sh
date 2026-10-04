#!/bin/sh
# Maintainer only: fast-forward each repo's target branch to its source branch in the maintainer's local
# repositories (dry run unless --apply; never pushes). See README.md.
exec python3 "$(dirname "$0")/lib/finish.py" "$@"
