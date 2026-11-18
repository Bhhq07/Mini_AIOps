#!/bin/bash

# Preserve the shell entry point; forward options and the exact aggregate exit code.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || exit 3
exec python3 "${SCRIPT_DIR}/python/run_monitor.py" "$@"
