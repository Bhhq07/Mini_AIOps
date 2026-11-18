#!/bin/bash

# Adapt only the legacy scanner output; its offset/rotation logic stays untouched.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || exit 3
CONFIG_FILE="${SCRIPT_DIR}/../config/thresholds.conf"
NGINX_ERROR_WARNING=10
NGINX_ERROR_CRITICAL=50
if [ -f "$CONFIG_FILE" ]; then
    source "$CONFIG_FILE"
fi

OUTPUT_MODE="text"
if [[ "${1:-}" == "--json" ]]; then
    OUTPUT_MODE="json"
fi
EXEC_OUTPUT=$(bash "${SCRIPT_DIR}/check_nginx.sh" 2>&1)
SCAN_EXIT_CODE=$?
if [ "$OUTPUT_MODE" = "text" ]; then
    printf '%s\n' "$EXEC_OUTPUT"
    exit "$SCAN_EXIT_CODE"
fi

# json.dumps escapes scanner messages safely and status always matches exit code.
printf '%s' "$EXEC_OUTPUT" | python3 -c '
from datetime import datetime
import json
import re
import sys

raw = sys.stdin.read()
scan_code = int(sys.argv[1])
status, code, metrics = "ERROR", 3, {}
message = raw.strip() or "Nginx scanner failed with exit code {}".format(scan_code)
try:
    warning, critical = float(sys.argv[2]), float(sys.argv[3])
    if not (0 <= warning < critical < float("inf")):
        raise ValueError("invalid Nginx thresholds")
    if scan_code == 0:
        counts = re.findall(r"\berror_count=(\d+)\b", raw)
        sizes = re.findall(r"\bscanned_bytes=(\d+)\b", raw)
        error_count = int(counts[-1]) if counts else 0
        scanned_bytes = int(sizes[-1]) if sizes else 0
        status, code = ("CRITICAL", 2) if error_count >= critical else (("WARNING", 1) if error_count >= warning else ("OK", 0))
        message = "Nginx error requests in this scan: {}".format(error_count)
        metrics = {
            "error_count": {"value": error_count, "unit": "count", "threshold_warning": warning, "threshold_critical": critical},
            "scanned_bytes": {"value": scanned_bytes, "unit": "bytes"},
        }
except ValueError as exc:
    message = str(exc)

print(json.dumps({
    "module": "nginx", "timestamp": datetime.now().astimezone().isoformat(),
    "status": status, "message": message, "metrics": metrics, "exit_code": code,
}, ensure_ascii=False, allow_nan=False))
sys.exit(code)
' "$SCAN_EXIT_CODE" "$NGINX_ERROR_WARNING" "$NGINX_ERROR_CRITICAL"
