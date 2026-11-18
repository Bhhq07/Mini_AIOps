#!/usr/bin/env python3
"""Run existing Mini AIOps monitors and persist one validated report per run."""

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parent.parent
CODES = {"OK": 0, "WARNING": 1, "CRITICAL": 2, "ERROR": 3}


class RunBusy(Exception):
    pass


class RunInterrupted(Exception):
    pass


def interrupt_run(signum, frame):
    raise RunInterrupted("Dispatcher interrupted by signal {}".format(signum))


def timestamp():
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def default_modules(root):
    root = Path(root).resolve()
    return [
        ("cpu", ["bash", str(root / "shell/check_cpu.sh"), "--json"]),
        ("memory", ["bash", str(root / "shell/check_memory.sh"), "--json"]),
        ("disk", [sys.executable, str(root / "python/check_disk.py"), "/", "--json"]),
        ("service", ["bash", str(root / "shell/check_services.sh"), "--json", "nginx", "sshd"]),
        ("nginx", ["bash", str(root / "shell/check_nginx_json_wrapper.sh"), "--json"]),
    ]


def strict_json(text):
    def invalid_constant(value):
        raise ValueError("non-finite JSON number: " + value)

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result

    return json.loads(text, parse_constant=invalid_constant, object_pairs_hook=unique_object)


def validate_result(value, module, return_code):
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    required = {"module", "timestamp", "status", "message", "metrics", "exit_code"}
    missing = required - value.keys()
    if missing:
        raise ValueError("missing fields: " + ", ".join(sorted(missing)))
    if value["module"] != module:
        raise ValueError("module name does not match invoked module")
    status = value["status"]
    if not isinstance(status, str) or status not in CODES:
        raise ValueError("unknown status")
    if type(value["exit_code"]) is not int or value["exit_code"] != CODES[status]:
        raise ValueError("status and exit_code disagree")
    if return_code != value["exit_code"]:
        raise ValueError("process return code and JSON exit_code disagree")
    stamp = value["timestamp"]
    if not isinstance(stamp, str) or "T" not in stamp:
        raise ValueError("timestamp must be ISO 8601 with a timezone")
    parsed_stamp = datetime.fromisoformat(stamp[:-1] + "+00:00" if stamp.endswith("Z") else stamp)
    if parsed_stamp.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    if not isinstance(value["message"], str) or not isinstance(value["metrics"], dict):
        raise ValueError("message must be a string and metrics an object")
    for name, metric in value["metrics"].items():
        if not name or not isinstance(metric, dict) or not {"value", "unit"} <= metric.keys():
            raise ValueError("each metric must contain value and unit")
        if not isinstance(metric["unit"], str) or not metric["unit"]:
            raise ValueError("metric unit must be a nonempty string")
        number = metric["value"]
        if number is not None and type(number) not in (int, float, str, bool):
            raise ValueError("metric value must be a JSON scalar")
        if isinstance(number, float) and not math.isfinite(number):
            raise ValueError("metric value must be finite")
        for key in ("threshold_warning", "threshold_critical"):
            if key in metric:
                threshold = metric[key]
                if type(threshold) not in (int, float) or not math.isfinite(threshold):
                    raise ValueError("metric threshold must be a finite number")
    # Validate extensions too; json.loads accepts exponent overflow as infinity.
    json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return value


def _kill_process_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def execute_module(module, command, timeout, cwd):
    started_at, start = timestamp(), time.monotonic()
    stdout, stderr, return_code, source = "", "", None, None
    outcome, message, payload = "execution_failed", "", None
    try:
        process = subprocess.Popen(
            command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace", start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_group(process)
            stdout, stderr = process.communicate()
            outcome, message = "timeout", "Module exceeded {:.3g}s timeout".format(timeout)
        except BaseException:
            _kill_process_group(process)
            process.communicate()
            raise
        return_code = process.returncode
        if outcome != "timeout":
            if return_code not in CODES.values():
                message = "Process exited outside monitor convention: {}".format(return_code)
            else:
                source = "stdout"
                text = stdout.strip()
                # Stage 1 emits some ERROR/3 objects on stderr, not stdout.
                if not text and return_code == 3:
                    text, source = stderr.strip(), "stderr"
                try:
                    decoded = strict_json(text)
                except (ValueError, RecursionError) as exc:
                    outcome, message = "invalid_json", "Cannot parse module JSON: " + str(exc)
                else:
                    try:
                        payload = validate_result(decoded, module, return_code)
                    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
                        outcome, message = "invalid_output", "Invalid module contract: " + str(exc)
                    else:
                        outcome = "module_error" if payload["status"] == "ERROR" else "completed"
    except OSError as exc:
        outcome, message = "execution_failed", "Cannot execute module: " + str(exc)
    if outcome not in ("completed", "module_error"):
        payload = {
            "module": module, "timestamp": timestamp(), "status": "ERROR",
            "message": message, "metrics": {}, "exit_code": 3,
        }
    result = dict(payload)
    result["execution"] = {
        "outcome": outcome, "command": list(command), "return_code": return_code,
        "stdout": stdout, "stderr": stderr, "json_source": source,
        "started_at": started_at, "finished_at": timestamp(),
        "duration_ms": round((time.monotonic() - start) * 1000, 3),
    }
    return result


@contextmanager
def run_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RunBusy("Another dispatcher holds " + str(path)) from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def append_history(path, report):
    record = (json.dumps(report, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.seek(0, os.SEEK_END)
        original_size = handle.tell()
        if original_size:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                raise OSError("History ends in an incomplete record; refusing to merge records")
        try:
            remaining = memoryview(record)
            while remaining:
                written = os.write(handle.fileno(), remaining)
                if written == 0:
                    raise OSError("Zero-byte history write")
                remaining = remaining[written:]
            os.fsync(handle.fileno())
        except BaseException:
            # Roll back a partial append while still holding the history lock.
            try:
                os.ftruncate(handle.fileno(), original_size)
                os.fsync(handle.fileno())
            except OSError:
                pass
            raise


def key_metrics(metrics):
    selected = {}
    for name, metric in metrics.items():
        value, warning = metric["value"], metric.get("threshold_warning")
        if warning is not None and type(value) in (int, float) and value >= warning:
            selected[name] = metric
        elif metric["unit"] == "state" and value != "active":
            selected[name] = metric
    return dict(list((selected or metrics).items())[:4])


def summarize(report, start):
    counts = Counter(item["status"] for item in report["results"])
    code = max((CODES[item["status"]] for item in report["results"]), default=0)
    if report["scheduler_errors"]:
        code = 3
    report["status"] = next(name for name, value in CODES.items() if value == code)
    report["exit_code"] = code
    report["metrics"] = {
        "modules_total": {"value": report["expected_modules"], "unit": "count"},
        "modules_completed": {"value": len(report["results"]), "unit": "count"},
        **{name.lower() + "_count": {"value": counts[name], "unit": "count"} for name in CODES},
        "scheduler_error_count": {"value": len(report["scheduler_errors"]), "unit": "count"},
    }
    report["message"] = "{} modules checked: {} OK, {} WARNING, {} CRITICAL, {} ERROR; {} scheduler errors".format(
        len(report["results"]), counts["OK"], counts["WARNING"], counts["CRITICAL"],
        counts["ERROR"], len(report["scheduler_errors"]),
    )
    report["alerts"] = [
        {"module": item["module"], "status": item["status"], "message": item["message"],
         "metrics": key_metrics(item["metrics"]), "outcome": item["execution"]["outcome"]}
        for item in report["results"] if item["status"] != "OK"
    ] + [
        {"module": "scheduler", "status": "ERROR", "message": error["message"],
         "metrics": {}, "outcome": error["kind"]}
        for error in report["scheduler_errors"]
    ]
    report["timestamp"] = report["finished_at"] = timestamp()
    report["duration_ms"] = round((time.monotonic() - start) * 1000, 3)


def text_report(report):
    lines = ["Mini AIOps run={} status={} exit_code={}".format(
        report["run_id"], report["status"], report["exit_code"]), report["message"]]
    for item in report["results"]:
        lines.append("  {}: {} ({}) {}".format(
            item["module"], item["status"], item["execution"]["outcome"], item["message"]))
    if report.get("system_info"):
        lines.append("  uptime: " + report["system_info"]["stdout"].strip())
    return "\n".join(lines)


def alert_lines(report):
    return ["ALERT {} {} {} metrics={}".format(
        item["status"], item["module"], json.dumps(item["message"], ensure_ascii=False),
        json.dumps(item["metrics"], ensure_ascii=False, allow_nan=False, separators=(",", ":")),
    ) for item in report["alerts"]]


def run(root=ROOT, timeout=10.0, history_path=None, modules=None):
    root = Path(root).resolve()
    specs = default_modules(root) if modules is None else modules
    history = Path(history_path) if history_path is not None else root / "logs/monitor_history.jsonl"
    if not history.is_absolute():
        history = root / history
    history = history.resolve()
    start = time.monotonic()
    report = {
        "module": "system", "run_id": str(uuid.uuid4()), "started_at": timestamp(),
        "expected_modules": len(specs), "results": [], "scheduler_errors": [],
        "history": {"path": str(history), "status": "not_written"},
    }
    if not math.isfinite(timeout) or timeout <= 0:
        report["scheduler_errors"].append({"kind": "invalid_timeout", "message": "Timeout must be positive and finite"})
        summarize(report, start)
        return report
    try:
        with run_lock(root / "state/run_aiops.lock"):
            try:
                for module, command in specs:
                    report["results"].append(execute_module(module, command, timeout, root))
            except RunInterrupted as exc:
                report["scheduler_errors"].append({"kind": "interrupted", "message": str(exc)})
            try:
                info = subprocess.run(["uptime"], capture_output=True, encoding="utf-8", errors="replace", timeout=min(timeout, 2.0), cwd=root)
                report["system_info"] = {"stdout": info.stdout, "stderr": info.stderr, "return_code": info.returncode}
            except (OSError, subprocess.TimeoutExpired) as exc:
                report["system_info"] = {"stdout": "", "stderr": str(exc), "return_code": None}
                report["scheduler_errors"].append({"kind": "system_info_failed", "message": "Cannot collect legacy uptime: " + str(exc)})
            summarize(report, start)
            try:
                log = root / "logs/run_aiops.log"
                log.parent.mkdir(parents=True, exist_ok=True)
                with log.open("a", encoding="utf-8") as handle:
                    handle.write(text_report(report) + "\n" + "\n".join(alert_lines(report)) + "\n")
            except OSError as exc:
                report["scheduler_errors"].append({"kind": "console_log_failed", "message": "Console log write failed: " + str(exc)})
            summarize(report, start)
            report["history"]["status"] = "written"
            try:
                append_history(history, report)
            except OSError as exc:
                report["history"].update(status="failed", error=str(exc))
                report["scheduler_errors"].append({"kind": "history_write_failed", "message": "History write failed: " + str(exc)})
                summarize(report, start)
                try:
                    with (root / "logs/run_aiops.log").open("a", encoding="utf-8") as handle:
                        handle.write("run={} history_write_failed {}\n".format(report["run_id"], json.dumps(str(exc), ensure_ascii=False)))
                except OSError:
                    pass  # The JSON report and stderr still expose the failure.
    except RunBusy as exc:
        report["scheduler_errors"].append({"kind": "lock_busy", "message": str(exc)})
        summarize(report, start)
    except OSError as exc:
        report["scheduler_errors"].append({"kind": "lock_failed", "message": "Cannot acquire run lock: " + str(exc)})
        summarize(report, start)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print only one complete JSON report on stdout")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-module timeout in seconds (default: 10)")
    parser.add_argument("--history", type=Path, help="JSONL file; relative paths resolve against the project root")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be positive and finite")
    previous_handlers = {signum: signal.signal(signum, interrupt_run) for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        report = run(timeout=args.timeout, history_path=args.history)
    except RunInterrupted as exc:
        print(str(exc), file=sys.stderr)
        return 3
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))
    else:
        print(text_report(report))
    for line in alert_lines(report):
        print(line, file=sys.stderr)
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
