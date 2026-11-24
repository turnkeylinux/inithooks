"""Security update status reflects completed commands, including failure."""
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("security_status", str(ROOT / "bin/security-updates-status"))
spec = importlib.util.spec_from_loader(loader.name, loader)
status = importlib.util.module_from_spec(spec)
loader.exec_module(status)


class SecurityUpdatesStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.state = root / "state"
        self.runtime = root / "run"
        self.state_patch = patch.object(status, "STATE", self.state)
        self.runtime_patch = patch.object(status, "RUNTIME", self.runtime)
        self.log_patch = patch.object(status, "LOGFILE", root / "log" / "security.log")
        self.state_patch.start()
        self.runtime_patch.start()
        self.log_patch.start()
        self.addCleanup(self.state_patch.stop)
        self.addCleanup(self.runtime_patch.stop)
        self.addCleanup(self.log_patch.stop)

    def test_success_failure_and_last_success_are_distinct(self):
        first = status.attempt(os.getpid(), "first")
        status.change_status(first, "running")
        status.change_status(first, "success", 3)
        successful = status.status()
        self.assertEqual(successful["installed"], 3)
        self.assertIsNotNone(successful["last_success"])
        self.assertEqual(successful["last_install"], successful["last_success"])
        second = status.attempt(os.getpid(), "second")
        status.change_status(second, "running")
        status.change_status(second, "failed")
        failed = status.status()
        self.assertEqual(failed["result"], "failed")
        self.assertIsNone(failed["installed"])
        self.assertEqual(failed["last_success"], successful["last_success"])
        self.assertEqual(failed["last_install"], successful["last_install"])

    def test_index_success_survives_upgrade_failure_and_zero_keeps_last_install(self):
        first = status.attempt(os.getpid(), "first")
        status.change_status(first, "running")
        status.record_check(first)
        status.change_status(first, "success", 2)
        previous = status.status()
        self.assertIsNotNone(previous["last_check"])
        second = status.attempt(os.getpid(), "second")
        status.change_status(second, "running")
        status.record_check(second)
        status.change_status(second, "failed")
        self.assertIsNotNone(status.status()["last_check"])
        third = status.attempt(os.getpid(), "third")
        status.change_status(third, "running")
        status.change_status(third, "success", 0)
        self.assertEqual(status.status()["last_install"], previous["last_install"])

    def test_older_job_cannot_overwrite_newer_attempt(self):
        first = status.attempt(os.getpid(), "first")
        second = status.attempt(os.getpid(), "second")
        status.change_status(first, "running")
        status.change_status(second, "running")
        status.change_status(first, "success", 5)
        self.assertEqual(status.status()["result"], "running")
        status.change_status(second, "failed")
        self.assertEqual(status.status()["result"], "failed")

    def test_success_cannot_override_failure_from_same_attempt(self):
        current = status.attempt(os.getpid(), "current")
        status.change_status(current, "running")
        status.change_status(current, "failed")
        status.change_status(current, "success", 1)
        self.assertEqual(status.status()["result"], "failed")

    def test_unwritable_telemetry_does_not_prevent_cron_update(self):
        env = {"TURNKEY_SECURITY_CRON_ID": "123-456",
               "TURNKEY_SECURITY_CRON_PID": str(os.getpid()),
               "TURNKEY_SECURITY_APT_COMMAND": "/usr/bin/apt-get"}
        with patch.dict(os.environ, env), patch.object(status, "supported_cron_actions", return_value=True), \
             patch.object(status, "cron_monitor_signature", return_value="signature"), \
             patch.object(status, "change_status", side_effect=PermissionError), \
             patch.object(status, "invoke", return_value=(0, None)) as invoke:
            self.assertEqual(status.cron(["update"]), 0)
        invoke.assert_called_once_with(["/usr/bin/apt-get", "update"], warnings=[])

    def test_custom_apt_command_cannot_claim_a_successful_update(self):
        env = {"TURNKEY_SECURITY_CRON_ID": "123-456", "TURNKEY_SECURITY_CRON_PID": str(os.getpid()),
               "TURNKEY_SECURITY_APT_COMMAND": "/usr/bin/echo"}
        with patch.dict(os.environ, env), patch.object(status, "cron_monitor_signature", return_value="sig"), \
             patch.object(status, "supported_cron_actions", return_value=True), \
             patch.object(status, "update_covers_security", return_value=True), \
             patch.object(status, "invoke", return_value=(0, None)):
            self.assertEqual(status.cron(["update"]), 0)
            self.assertEqual(status.status()["daily"]["result"], "unknown")

    def test_wrong_shaped_runfile_does_not_prevent_upgrade(self):
        self.runtime.mkdir()
        (self.runtime / "123-456.json").write_text("[]")
        env = {"TURNKEY_SECURITY_CRON_ID": "123-456",
               "TURNKEY_SECURITY_CRON_PID": str(os.getpid())}
        args = ["upgrade", "-o", "Dir::Etc::sourcelist=" + status.SECURITY_SOURCES,
                "-o", "Dir::Etc::sourceparts=/dev/null"]
        with patch.dict(os.environ, env), patch.object(status, "invoke", return_value=(0, 0)) as invoke:
            self.assertEqual(status.cron(args), 0)
        invoke.assert_called_once_with(["/usr/bin/apt-get"] + args)

    def test_dead_process_or_previous_boot_is_interrupted(self):
        current = status.attempt(os.getpid(), "current")
        status.change_status(current, "running")
        self.assertEqual(status.status()["result"], "running")
        path = self.state / "status.json"
        record = json.loads(path.read_text())
        record["boot_id"] = "different"
        path.write_text(json.dumps(record))
        self.assertEqual(status.status()["result"], "interrupted")

    def test_missing_or_malformed_status_is_unknown(self):
        self.assertEqual(status.status()["result"], "unknown")
        self.state.mkdir()
        for bad in ("{bad", "[]", "null", "42", '"string"',
                    '{"schema_version":1,"result":"success","last_attempt":null}'):
            with self.subTest(bad=bad):
                (self.state / "status.json").write_text(bad)
                self.assertEqual(status.status()["result"], "unknown")

    def test_manual_failure_and_success(self):
        with patch.object(status, "security_sources_available", return_value=True), patch.object(status, "update_covers_security", return_value=True), patch.object(status, "invoke", side_effect=[(0, None), (100, None)]):
            self.assertEqual(status.manual(), 100)
        self.assertEqual(status.status()["result"], "failed")
        with patch.object(status, "security_sources_available", return_value=True), patch.object(status, "update_covers_security", return_value=True), patch.object(status, "invoke", side_effect=[(0, None), (0, None), (0, None), (0, 2)]):
            self.assertEqual(status.manual(), 0)
        self.assertEqual(status.status()["result"], "success")
        self.assertEqual(status.status()["installed"], 2)

    def test_daily_failure_survives_manual_success_and_overlapping_runs(self):
        daily = status.attempt(os.getpid(), "cron:123-456")
        manual = status.attempt(os.getpid(), "manual:123")
        with patch.object(status, "cron_monitor_signature", return_value="sig"), \
             patch.object(status, "supported_cron_actions", return_value=True), \
             patch.object(status, "update_covers_security", return_value=True):
            daily["monitor_signature"] = "sig"
            status.change_status(daily, "running")
            status.change_status(manual, "running")
            daily["stage"] = "repository refresh"
            status.change_status(daily, "failed")
            status.change_status(manual, "success", 2)
            result = status.status()
        self.assertEqual(result["result"], "success")
        self.assertEqual(result["daily"]["result"], "failed")
        self.assertEqual(result["daily"]["failure_stage"], "repository refresh")

    def test_daily_partial_refresh_fails_with_diagnostic_log(self):
        env = {"TURNKEY_SECURITY_CRON_ID": "123-456", "TURNKEY_SECURITY_CRON_PID": str(os.getpid())}
        command = [sys.executable, "-c", 'import sys; print("Failed to fetch repository", file=sys.stderr)']
        with patch.dict(os.environ, env), patch.object(status, "supported_cron_actions", return_value=True), \
             patch.object(status, "cron_monitor_signature", return_value="sig"), \
             patch.object(status, "update_covers_security", return_value=True):
            # APT can return zero while reporting a failed index download.
            real_invoke = status.invoke_cron
            with patch.object(status, "invoke_cron") as run:
                def local_invoke(_args, *, warnings=None, truncate=False):
                    return real_invoke(command, warnings=warnings, truncate=truncate)
                run.side_effect = local_invoke
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(status.cron(["update"]), 0)
            result = status.status()["daily"]
        self.assertEqual(result["result"], "failed")
        self.assertEqual(result["failure_stage"], "repository refresh")
        self.assertIn("Failed to fetch repository", (self.state / "daily.log").read_text())
        self.assertEqual((self.state / "daily.log").stat().st_mode & 0o777, 0o600)

    def test_daily_install_success_and_failure(self):
        env = {"TURNKEY_SECURITY_CRON_ID": "123-456", "TURNKEY_SECURITY_CRON_PID": str(os.getpid())}
        args = ["dist-upgrade", "-o", "Dir::Etc::sourcelist=" + status.SECURITY_SOURCES,
                "-o", "Dir::Etc::sourceparts=/dev/null"]
        with patch.dict(os.environ, env), patch.object(status, "supported_cron_actions", return_value=True), \
             patch.object(status, "cron_monitor_signature", return_value="sig"), \
             patch.object(status, "update_covers_security", return_value=True), \
             patch.object(status, "invoke_cron", side_effect=[(0, None), (42, None), (0, None), (0, 2)]):
            self.assertEqual(status.cron(["update"]), 0)
            self.assertEqual(status.cron(args), 42)
            self.assertEqual(status.status()["daily"]["result"], "failed")
            self.assertEqual(status.status()["daily"]["failure_stage"], "security update installation")
            self.assertEqual(status.cron(["update"]), 0)
            self.assertEqual(status.cron(args), 0)
            result = status.status()["daily"]
        self.assertEqual(result["result"], "success")
        self.assertIsNotNone(result["last_success"])

    def test_cli_shows_latest_manual_failure_and_private_diagnostics(self):
        def failing_invoke(command, log, warnings, log_errors):
            if command[0] == "dpkg":
                return 0, None
            log.write("repository unavailable\n")
            log.flush()
            return 100, None
        with patch.object(status, "security_sources_available", return_value=True), \
             patch.object(status, "update_covers_security", return_value=True), \
             patch.object(status, "invoke", side_effect=failing_invoke):
            self.assertEqual(status.manual(), 100)
        self.assertEqual(status.LOGFILE.stat().st_mode & 0o777, 0o600)
        output = io.StringIO()
        with redirect_stdout(output):
            status.show_status(False)
        self.assertIn("Manual update failed during: repository refresh", output.getvalue())
        self.assertIn("repository unavailable", output.getvalue())
        self.assertIn("Daily security updates: unknown", output.getvalue())

    def test_cli_shows_interrupted_daily_output_and_retry(self):
        daily = status.attempt(os.getpid(), "cron:123-456")
        daily["monitor_signature"] = "sig"
        with patch.object(status, "cron_monitor_signature", return_value="sig"), \
             patch.object(status, "supported_cron_actions", return_value=True), \
             patch.object(status, "update_covers_security", return_value=True):
            status.change_status(daily, "running")
            path = self.state / "daily.json"
            record = json.loads(path.read_text())
            record["boot_id"] = "another boot"
            path.write_text(json.dumps(record))
            (self.state / "daily.log").write_text("last line before interruption\n")
            output = io.StringIO()
            with redirect_stdout(output):
                status.show_status(False)
        self.assertIn("Daily security updates: interrupted", output.getvalue())
        self.assertIn("last line before interruption", output.getvalue())
        self.assertIn("Retry: sudo security-updates", output.getvalue())

    def test_manual_missing_security_sources_returns_failure(self):
        errors = io.StringIO()
        def completed(*args, **kwargs):
            print("APT command finished", file=sys.stderr)
            return 0, 0
        with patch.object(status, "security_sources_available", return_value=False), \
             patch.object(status, "invoke", side_effect=completed), redirect_stderr(errors):
            self.assertEqual(status.manual(), 1)
        self.assertTrue(errors.getvalue().splitlines()[-1].startswith("Could not verify the configured security update sources"))
        self.assertIn("Could not verify the configured security update sources", status.LOGFILE.read_text())
        self.assertEqual(status.status()["result"], "unknown")
        output = io.StringIO()
        with redirect_stdout(output):
            status.show_status(False)
        self.assertIn("Manual update outcome could not be verified", output.getvalue())
        self.assertIn("Could not verify the configured security update sources", output.getvalue())

    def test_security_updates_streams_update_output_and_exit_status(self):
        import subprocess
        root = Path(self.temp.name)
        installer = root / "fake-installer"
        installer.write_text('#!/bin/sh\necho update-output\necho update-error >&2\nexit "$1"\n')
        installer.chmod(0o755)
        # Use an executable launcher at the exact installed command name; the
        # substituted installer emits output and returns a controlled status.
        launcher = root / "security-updates"
        source = (ROOT / "bin/security-updates-status").read_text()
        source = source.replace('INSTALL_COMMAND = "/usr/sbin/turnkey-install-security-updates"',
                                'INSTALL_COMMAND = ' + repr(str(installer)))
        launcher.write_text(source)
        launcher.chmod(0o755)
        for exitcode in (0, 42):
            installer.write_text('#!/bin/sh\necho update-output\necho update-error >&2\nexit ' + str(exitcode) + '\n')
            result = subprocess.run([str(launcher)], text=True, capture_output=True)
            self.assertEqual(result.returncode, exitcode)
            self.assertEqual(result.stdout, "update-output\n")
            self.assertEqual(result.stderr, "update-error\n")

    def test_only_effective_security_sources_qualify(self):
        args = ["dist-upgrade", "-o", "Dir::Etc::sourcelist=" + status.SECURITY_SOURCES,
                "-o", "Dir::Etc::sourceparts=nonexistent"]
        self.assertTrue(status.security_upgrade(args))
        self.assertFalse(status.security_upgrade(args + ["-o", "Dir::Etc::sourcelist=/tmp/other"]))
        self.assertFalse(status.security_upgrade(args + ["-o", "Dir::Etc::sourceparts=/etc/apt/sources.list.d"]))
        self.assertFalse(status.security_upgrade(["upgrade", "-o", "Other=" + status.SECURITY_SOURCES,
                                                  "-o", "Dir::Etc::sourceparts=/dev/null"]))
        self.assertFalse(status.security_upgrade(["upgrade", "-o", "Dir::Etc::sourcelist=" + status.SECURITY_SOURCES + ".extra",
                                                  "-o", "Dir::Etc::sourceparts=/dev/null"]))
        self.assertFalse(status.security_upgrade(args + ["--simulate"]))
        self.assertFalse(status.security_upgrade(args + ["-sy"]))
        self.assertFalse(status.security_upgrade(args + ["-dy"]))
        self.assertFalse(status.security_upgrade(args + ["-o", "APT::Get::Download-Only=true"]))

    def test_custom_update_source_overrides_do_not_claim_coverage(self):
        for args in (["update", "-o", "Dir::Etc::sourcelist=/tmp/other"],
                     ["-o", "Dir::Etc::sourceparts=/tmp/other", "update"],
                     ["update", "--option=Dir::Etc::sourceparts=/tmp/other"],
                     ["update", "-c", "/tmp/apt.conf"]):
            with self.subTest(args=args):
                self.assertFalse(status.update_covers_security(args))

    def test_global_apt_simulation_does_not_claim_coverage(self):
        config = ('Dir "/";\nDir::Etc "etc/apt";\n'
                  'Dir::Etc::sourcelist "sources.list";\n'
                  'Dir::Etc::sourceparts "sources.list.d";\n'
                  'apt::get::simulate "true";\n')
        with patch.object(status.subprocess, "run", return_value=__import__("subprocess").CompletedProcess([], 0, config, "")):
            self.assertFalse(status.update_covers_security([]))

    def test_log_write_failure_does_not_block_either_output_pipe(self):
        class BrokenLog:
            def write(self, text):
                raise OSError("disk full")
            def flush(self):
                raise AssertionError("flush after failure")
        script = ('import sys\n'
                  'for i in range(5000):\n'
                  ' print("out-%04d" % i)\n'
                  ' print("err-%04d" % i, file=sys.stderr)\n')
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code, count = status.invoke([sys.executable, "-c", script], BrokenLog(), log_errors=True)
        self.assertEqual((code, count), (0, None))
        self.assertEqual(len(output.getvalue().splitlines()), 5000)
        self.assertEqual(len(errors.getvalue().splitlines()), 5000)

    def test_log_flush_failure_preserves_real_apt_exit_code(self):
        class BrokenLog:
            def write(self, text):
                pass
            def flush(self):
                raise OSError("disk full")
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code, _ = status.invoke([sys.executable, "-c", 'print("message"); raise SystemExit(37)'], BrokenLog())
        self.assertEqual(code, 37)
        self.assertEqual(output.getvalue(), "message\n")

    def test_log_close_failure_does_not_override_update_result(self):
        real_fdopen = status.os.fdopen
        class CloseFailure:
            def __init__(self, fd, mode):
                self.stream = real_fdopen(fd, mode)
            def write(self, value):
                return self.stream.write(value)
            def flush(self):
                return self.stream.flush()
            def close(self):
                self.stream.close()
                raise OSError("disk full")
        with patch.object(status.os, "fdopen", side_effect=CloseFailure):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code, _ = status.invoke_cron([sys.executable, "-c", 'print("message"); raise SystemExit(37)'])
        self.assertEqual(code, 37)

    def test_large_stdout_and_stderr_are_streamed_and_logged(self):
        output, errors, log = io.StringIO(), io.StringIO(), io.StringIO()
        script = ('import sys\n'
                  'for i in range(1500):\n'
                  ' print("out-%04d" % i)\n'
                  ' print("err-%04d" % i, file=sys.stderr)\n')
        with redirect_stdout(output), redirect_stderr(errors):
            code, count = status.invoke([sys.executable, "-c", script], log, log_errors=True)
        self.assertEqual((code, count), (0, None))
        self.assertEqual(len(output.getvalue().splitlines()), 1500)
        self.assertEqual(len(errors.getvalue().splitlines()), 1500)
        self.assertEqual(len(log.getvalue().splitlines()), 3000)
