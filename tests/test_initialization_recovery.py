#!/usr/bin/env python3
"""Keep package upgrades and Hub-service retries safe during initialization."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CACHE = "usr/lib/python3/dist-packages/libinithooks/inithooks_cache.py"


class PackageUpgradeTest(unittest.TestCase):
    def run_postinst(self, arguments, active=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / CACHE
            cache.parent.mkdir(parents=True)
            cache.write_text("#!/bin/sh\nexit 0\n")
            cache.chmod(0o644)
            (root / "run").mkdir()
            if active:
                (root / "run/inithooks-was-active").touch()
                (root / "run/turnkey-init-fence-was-active").touch()
            source = (ROOT / "debian/postinst").read_text()
            source = source.replace("/" + CACHE, str(cache))
            source = source.replace("/run/", str(root / "run") + "/")
            script = root / "postinst"
            script.write_text(source)
            command = root / "systemctl"
            command.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n'
                '# Restarting either init or its getty can kill dpkg itself.\n'
                'case "$1" in restart|stop|start) exit 81;; esac\n')
            command.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + ":/usr/bin:/bin",
                       CALL_LOG=str(root / "calls"))
            result = subprocess.run(["sh", str(script), *arguments], env=env,
                                    capture_output=True, text=True)
            calls = (root / "calls").read_text().splitlines()
            return result, calls, cache.stat().st_mode & 0o777

    def test_upgrade_does_not_interrupt_active_initialization(self):
        result, calls, mode = self.run_postinst(["configure", "2.3.6"], active=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["daemon-reload"])
        self.assertEqual(mode, 0o755)

    def test_upgrade_does_not_restart_console_session(self):
        result, calls, mode = self.run_postinst(["configure", "2.3.6"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["daemon-reload"])
        self.assertEqual(mode, 0o755)

    def test_first_install_enables_services(self):
        result, calls, mode = self.run_postinst(["configure"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["daemon-reload", "enable inithooks.service",
                                 "enable turnkey-init-fence.service"])
        self.assertEqual(mode, 0o755)

    def test_abort_upgrade_does_not_interrupt_initialization(self):
        result, calls, mode = self.run_postinst(["abort-upgrade", "2.3.6"], active=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["daemon-reload"])
        self.assertEqual(mode, 0o755)

    def test_build_preserves_executable_cache_after_python_mode_normalization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "debian/inithooks" / CACHE
            cache.parent.mkdir(parents=True)
            cache.write_text("#!/bin/sh\nexit 0\n")
            cache.chmod(0o755)
            command = root / "dh_fixperms"
            command.write_text('#!/bin/sh\nchmod 644 "$CACHE"\n')
            command.chmod(0o755)
            result = subprocess.run(
                ["make", "-f", str(ROOT / "debian/rules"), "override_dh_fixperms"],
                cwd=root, env=dict(os.environ, PATH=str(root) + ":/usr/bin:/bin",
                                   CACHE=str(cache)), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(cache.stat().st_mode & 0o777, 0o755)


class HubRetryTest(unittest.TestCase):
    def setUp(self):
        dialog = types.ModuleType("libinithooks.dialog_wrapper")
        dialog.Dialog = object
        spec = importlib.util.spec_from_file_location("hubservices", ROOT / "bin/hubservices.py")
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"libinithooks.dialog_wrapper": dialog}):
            spec.loader.exec_module(self.module)

    def run_main(self, status, init_status=0, fqdn=False):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            code = status if command[0] == "tklbam-status" else init_status
            result = subprocess.CompletedProcess(command, code)
            if kwargs.get("check"):
                result.check_returncode()
            return result

        args = ["hubservices.py", "--apikey=test-key"]
        if fqdn:
            args.append("--fqdn=example.tklapp.com")
        with patch.object(sys, "argv", args), patch.object(self.module.signal, "signal"), \
                patch.object(self.module.subprocess, "run", side_effect=run):
            self.module.main()
        return calls

    def test_retry_preserves_existing_identity_with_and_without_backup(self):
        for status in (0, 10):
            with self.subTest(status=status):
                self.assertEqual(self.run_main(status), [["tklbam-status", "--short"]])

    def test_new_account_is_initialized(self):
        self.assertEqual(self.run_main(11), [["tklbam-status", "--short"],
                                             ["tklbam-init", "test-key"]])

    def test_existing_backup_account_does_not_skip_dns_configuration(self):
        self.assertEqual(self.run_main(10, fqdn=True), [
            ["tklbam-status", "--short"],
            ["hubdns-init", "test-key", "example.tklapp.com"], ["hubdns-update"]])

    def test_unexpected_status_failure_is_not_treated_as_initialized(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_main(1)

    def test_real_initialization_failure_propagates(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_main(11, init_status=1)


if __name__ == "__main__":
    unittest.main()
