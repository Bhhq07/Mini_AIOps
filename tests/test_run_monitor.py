"""Stage 2 tests: temporary modules only; no real services or privileged files."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import run_monitor as monitor


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.history = self.root / "logs/monitor_history.jsonl"
        self.names = ["cpu", "memory", "disk", "service", "nginx"]

    def payload(self, name="cpu", status="OK"):
        return {
            "module": name, "timestamp": "2026-11-14T12:00:00+08:00",
            "status": status, "message": name + " says " + status,
            "metrics": {"usage": {"value": 85 if status == "WARNING" else 10, "unit": "percent", "threshold_warning": 80}},
            "exit_code": monitor.CODES[status],
        }

    def command(self, value, code=None, stderr=False):
        code = value.get("exit_code", 0) if code is None else code
        raw = json.dumps(value)
        stream = "sys.stderr" if stderr else "sys.stdout"
        return [sys.executable, "-c", "import sys; print({!r}, file={}); sys.exit({})".format(raw, stream, code)]

    def specs(self, statuses=None):
        statuses = statuses or {}
        return [(name, self.command(self.payload(name, statuses.get(name, "OK")))) for name in self.names]

    def run_specs(self, specs=None, **kwargs):
        return monitor.run(root=self.root, modules=self.specs() if specs is None else specs, **kwargs)

    def assert_consistent(self, report, status):
        self.assertEqual(report["status"], status)
        self.assertEqual(report["exit_code"], monitor.CODES[status])
        self.assertEqual(len(report["results"]), 5)

    def test_01_five_modules_ok_in_order(self):
        report = self.run_specs()
        self.assert_consistent(report, "OK")
        self.assertEqual([r["module"] for r in report["results"]], self.names)
        self.assertTrue(all(r["execution"]["outcome"] == "completed" for r in report["results"]))
        self.assertFalse(report["alerts"])
        self.assertEqual(report["system_info"]["return_code"], 0)
        self.assertIn("uptime:", monitor.text_report(report))

    def test_02_warning_is_monitor_result_not_execution_failure(self):
        report = self.run_specs(self.specs({"memory": "WARNING"}))
        self.assert_consistent(report, "WARNING")
        alert = report["alerts"][0]
        self.assertEqual((alert["module"], alert["outcome"]), ("memory", "completed"))
        self.assertIn("usage", alert["metrics"])

    def test_03_critical_preserves_metrics(self):
        report = self.run_specs(self.specs({"service": "CRITICAL"}))
        self.assert_consistent(report, "CRITICAL")
        self.assertEqual(report["results"][3]["metrics"], self.payload("service", "CRITICAL")["metrics"])

    def test_04_module_error_is_distinct_from_launch_failure(self):
        report = self.run_specs(self.specs({"nginx": "ERROR"}))
        self.assert_consistent(report, "ERROR")
        self.assertEqual(report["results"][4]["execution"]["outcome"], "module_error")
        self.assertIn("nginx says ERROR", report["results"][4]["message"])

    def test_05_multiple_anomalies_use_worst_status(self):
        report = self.run_specs(self.specs({"cpu": "WARNING", "disk": "CRITICAL", "nginx": "ERROR"}))
        self.assert_consistent(report, "ERROR")
        self.assertEqual(len(report["alerts"]), 3)
        self.assertEqual([report["metrics"][s + "_count"]["value"] for s in ("ok", "warning", "critical", "error")], [2, 1, 1, 1])

    def test_06_launch_failure_does_not_skip_later_modules(self):
        specs = self.specs()
        specs[0] = ("cpu", [str(self.root / "missing-executable")])
        report = self.run_specs(specs)
        self.assert_consistent(report, "ERROR")
        self.assertEqual(report["results"][0]["execution"]["outcome"], "execution_failed")
        self.assertEqual(report["results"][-1]["status"], "OK")

    def test_07_exit_outside_convention_is_execution_failure(self):
        specs = self.specs()
        specs[0] = ("cpu", [sys.executable, "-c", "import sys; print('failure',file=sys.stderr); sys.exit(7)"])
        report = self.run_specs(specs)
        self.assertEqual(report["results"][0]["execution"]["return_code"], 7)
        self.assertEqual(report["results"][0]["execution"]["stderr"].strip(), "failure")
        self.assertEqual(report["results"][0]["execution"]["outcome"], "execution_failed")
        self.assertEqual(report["results"][-1]["status"], "OK")

    def test_08_timeout_kills_process_group_and_continues(self):
        specs = self.specs()
        pid_file = self.root / "child.pid"
        script = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); open(sys.argv[1],'w').write(str(p.pid)); print('started',flush=True); time.sleep(30)"
        specs[0] = ("cpu", [sys.executable, "-c", script, str(pid_file)])
        report = self.run_specs(specs, timeout=0.7)
        first = report["results"][0]
        self.assertEqual(first["execution"]["outcome"], "timeout")
        self.assertIn("started", first["execution"]["stdout"])
        self.assertEqual(report["results"][-1]["status"], "OK")
        if pid_file.exists():
            proc_stat = Path("/proc") / pid_file.read_text() / "stat"
            if proc_stat.exists():
                self.assertEqual(proc_stat.read_text().rsplit(")", 1)[1].split()[0], "Z")

    def test_09_invalid_json_is_not_module_error(self):
        specs = self.specs()
        specs[0] = ("cpu", [sys.executable, "-c", "print('not JSON')"])
        report = self.run_specs(specs)
        self.assertEqual(report["results"][0]["execution"]["outcome"], "invalid_json")
        self.assertEqual(report["results"][-1]["status"], "OK")

    def test_10_missing_required_field(self):
        value = self.payload()
        del value["metrics"]
        result = monitor.execute_module("cpu", self.command(value), 2, self.root)
        self.assertEqual(result["execution"]["outcome"], "invalid_output")
        self.assertIn("metrics", result["message"])

    def test_11_status_and_exit_code_must_agree(self):
        value = self.payload()
        value["exit_code"] = 1
        result = monitor.execute_module("cpu", self.command(value), 2, self.root)
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["execution"]["outcome"], "invalid_output")

    def test_12_process_code_must_match_json(self):
        result = monitor.execute_module("cpu", self.command(self.payload(), code=2), 2, self.root)
        self.assertEqual(result["execution"]["outcome"], "invalid_output")
        self.assertEqual(result["execution"]["return_code"], 2)

    def test_13_wrong_module_name_is_rejected(self):
        result = monitor.execute_module("cpu", self.command(self.payload("disk")), 2, self.root)
        self.assertEqual(result["execution"]["outcome"], "invalid_output")

    def test_14_naive_or_bad_timestamp_is_rejected(self):
        for stamp in ("not a timestamp", "2026-11-14T12:00:00"):
            with self.subTest(timestamp=stamp):
                value = self.payload()
                value["timestamp"] = stamp
                result = monitor.execute_module("cpu", self.command(value), 2, self.root)
                self.assertEqual(result["execution"]["outcome"], "invalid_output")

    def test_15_metrics_shape_and_finite_numbers(self):
        for metrics in ({"x": {"value": 1}}, {"x": {"value": [], "unit": "count"}}, {"x": {"value": float("inf"), "unit": "count"}}):
            with self.subTest(metrics=metrics):
                value = self.payload()
                value["metrics"] = metrics
                result = monitor.execute_module("cpu", self.command(value), 2, self.root)
                self.assertIn(result["execution"]["outcome"], ("invalid_json", "invalid_output"))

    def test_16_stderr_error_json_compatibility(self):
        result = monitor.execute_module("nginx", self.command(self.payload("nginx", "ERROR"), stderr=True), 2, self.root)
        self.assertEqual(result["execution"]["outcome"], "module_error")
        self.assertEqual(result["execution"]["json_source"], "stderr")
        self.assertEqual(result["execution"]["stdout"], "")

    def test_17_capture_stderr_without_polluting_json(self):
        value = json.dumps(self.payload())
        command = [sys.executable, "-c", "import sys; print({!r}); print('diagnostic',file=sys.stderr)".format(value)]
        result = monitor.execute_module("cpu", command, 2, self.root)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["execution"]["stderr"].strip(), "diagnostic")

    def test_18_history_record_is_complete_json_line(self):
        report = self.run_specs()
        lines = self.history.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0]), report)
        self.assertEqual(report["history"]["status"], "written")

    def test_19_consecutive_runs_append_unique_ids(self):
        first = self.run_specs()
        initial = self.history.read_bytes()
        second = self.run_specs()
        self.assertTrue(self.history.read_bytes().startswith(initial))
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(len(self.history.read_text().splitlines()), 2)

    def test_20_competing_process_cannot_run_modules_or_write_history(self):
        with monitor.run_lock(self.root / "state/run_aiops.lock"):
            source = "import sys,json; sys.path.insert(0,sys.argv[1]); import run_monitor as m; print(json.dumps(m.run(root=sys.argv[2])) )"
            result = subprocess.run([sys.executable, "-c", source, str(monitor.ROOT / "python"), str(self.root)], capture_output=True, text=True, timeout=3)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
        self.assertEqual(report["exit_code"], 3)
        self.assertEqual(report["scheduler_errors"][0]["kind"], "lock_busy")
        self.assertEqual(report["results"], [])
        self.assertFalse(self.history.exists())

    def test_21_history_write_failure_is_reported_and_fails_run(self):
        self.history.mkdir(parents=True)
        report = self.run_specs()
        self.assert_consistent(report, "ERROR")
        self.assertEqual(report["history"]["status"], "failed")
        self.assertEqual(report["scheduler_errors"][0]["kind"], "history_write_failed")
        self.assertEqual(report["alerts"][-1]["module"], "scheduler")

    def test_22_partial_write_is_rolled_back(self):
        report = self.run_specs()
        original = self.history.read_bytes()
        real_write = os.write
        calls = []

        def fail_after_prefix(fd, data):
            calls.append(True)
            if len(calls) == 1:
                return real_write(fd, data[:20])
            raise OSError("simulated disk full")

        with patch.object(monitor.os, "write", side_effect=fail_after_prefix):
            with self.assertRaises(OSError):
                monitor.append_history(self.history, report)
        self.assertEqual(self.history.read_bytes(), original)

    def test_23_incomplete_history_tail_is_not_overwritten(self):
        self.history.parent.mkdir()
        self.history.write_text('{"interrupted":')
        original = self.history.read_bytes()
        report = self.run_specs()
        self.assertEqual(report["history"]["status"], "failed")
        self.assertEqual(report["exit_code"], 3)
        self.assertEqual(self.history.read_bytes(), original)

    def test_24_lock_released_after_failure(self):
        with patch.object(monitor, "append_history", side_effect=OSError("simulated failure")):
            self.assertEqual(self.run_specs()["exit_code"], 3)
        self.assertEqual(self.run_specs()["exit_code"], 0)

    def test_25_cli_json_and_alerts_are_separate_and_exit_code_matches(self):
        for status in monitor.CODES:
            with self.subTest(status=status):
                report = self.run_specs(self.specs({"cpu": status}))
                stdout, stderr = StringIO(), StringIO()
                with patch.object(monitor, "run", return_value=report), redirect_stdout(stdout), redirect_stderr(stderr):
                    code = monitor.main(["--json"])
                self.assertEqual(code, monitor.CODES[status])
                self.assertEqual(json.loads(stdout.getvalue()), report)
                self.assertEqual(bool(stderr.getvalue()), status != "OK")

    def test_26_default_commands_and_shell_entry_compatibility(self):
        specs = monitor.default_modules(self.root)
        self.assertEqual([name for name, _ in specs], self.names)
        self.assertTrue(all("--json" in command for _, command in specs))
        (self.root / "python").mkdir()
        shutil.copyfile(monitor.ROOT / "run_aiops.sh", self.root / "run_aiops.sh")
        (self.root / "python/run_monitor.py").write_text("import sys,json; print(json.dumps(sys.argv[1:])); sys.exit(2)\n")
        result = subprocess.run(["bash", str(self.root / "run_aiops.sh"), "--json", "--timeout", "4"], cwd="/", capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout), ["--json", "--timeout", "4"])

    def test_27_nginx_adapter_maps_failure_and_thresholds_without_real_logs(self):
        (self.root / "shell").mkdir()
        (self.root / "config").mkdir()
        (self.root / "config/thresholds.conf").write_text("NGINX_ERROR_WARNING=10\nNGINX_ERROR_CRITICAL=50\n")
        wrapper = self.root / "shell/check_nginx_json_wrapper.sh"
        shutil.copyfile(monitor.ROOT / "shell/check_nginx_json_wrapper.sh", wrapper)
        scanner = self.root / "shell/check_nginx.sh"
        for scanner_code, text, status in ((1, 'ERROR denied "quoted" path', "ERROR"), (0, "scanned_bytes=100 error_count=10", "WARNING"), (0, "scanned_bytes=500 error_count=50", "CRITICAL"), (0, "", "OK")):
            with self.subTest(status=status):
                scanner.write_text("printf '%s\\n' " + "'" + text + "'\nexit " + str(scanner_code) + "\n")
                result = monitor.execute_module("nginx", ["bash", str(wrapper), "--json"], 3, self.root)
                self.assertEqual(result["status"], status)
                self.assertEqual(result["exit_code"], monitor.CODES[status])
                self.assertIn(result["execution"]["outcome"], ("completed", "module_error"))

    def test_28_duplicate_keys_and_multiple_documents_are_rejected(self):
        for text in ('{"module":"cpu","module":"disk"}', '{}\n{}'):
            with self.subTest(text=text):
                command = [sys.executable, "-c", "print({!r})".format(text)]
                result = monitor.execute_module("cpu", command, 2, self.root)
                self.assertEqual(result["execution"]["outcome"], "invalid_json")

    def test_29_console_log_failure_is_not_silent(self):
        (self.root / "logs/run_aiops.log").mkdir(parents=True)
        report = self.run_specs()
        self.assertEqual(report["exit_code"], 3)
        self.assertEqual(report["scheduler_errors"][0]["kind"], "console_log_failed")
        self.assertEqual(report["history"]["status"], "written")
        self.assertEqual(json.loads(self.history.read_text()), report)

    def test_30_interrupted_run_is_saved_and_lock_released(self):
        with patch.object(monitor, "execute_module", side_effect=monitor.RunInterrupted("test interruption")):
            report = self.run_specs()
        self.assertEqual(report["exit_code"], 3)
        self.assertEqual(report["results"], [])
        self.assertEqual(report["scheduler_errors"][0]["kind"], "interrupted")
        self.assertEqual(json.loads(self.history.read_text()), report)
        with monitor.run_lock(self.root / "state/run_aiops.lock"):
            pass


if __name__ == "__main__":
    unittest.main()
