#!/bin/sh
# IL2CPP smoke: build one IL2CPP (WebGL) player of the family at Medium stripping, serve it and check that it boots a
# container. See README.md or run with --help.
exec python3 "$(dirname "$0")/lib/il2cpp_smoke.py" "$@"
