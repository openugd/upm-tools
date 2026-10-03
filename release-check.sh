#!/bin/sh
# Release check: is each package's HEAD ready to be tagged as the planned version? Tags nothing. See README.md.
exec python3 "$(dirname "$0")/lib/release_check.py" "$@"
