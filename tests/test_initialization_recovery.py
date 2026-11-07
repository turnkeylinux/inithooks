#!/usr/bin/env python3
"""Keep package upgrades and Hub-service retries safe during initialization."""
import importlib.util
import io
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

    def run_main(self, init_status=0, stderr="", fqdn=False, failed_command="tklbam-init"):
        calls = []
        self.last_calls = calls

        def run(command, **kwargs):
            calls.append(command)
            code = init_status if command[0] == failed_command else 0
            result = subprocess.CompletedProcess(
                command, code, stderr=stderr if command[0] == failed_command else "")
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

    def test_backup_failure_still_attempts_dns(self):
        with patch.object(sys, "stderr"), self.assertRaises(SystemExit):
            self.run_main(1, "backup unavailable\n", fqdn=True)
        self.assertEqual([command[0] for command in self.last_calls],
                         ["tklbam-init", "hubdns-init", "hubdns-update"])

    def test_free_browser_connection_does_not_attempt_backup(self):
        dialog = unittest.mock.Mock()
        dialog.menu.return_value = "Link"
        dialog.yesno.return_value = True
        responses = [{"verification_uri": "https://hub.turnkeylinux.org/link", "user_code": "ABCD&1234"},
                     {"status": "ready", "domain": "sample.tklapp.com"}]
        with patch.object(sys, "argv", ["hubservices.py"]), patch.object(self.module.signal, "signal"), \
                patch.object(self.module, "Dialog", return_value=dialog), \
                patch.object(self.module, "mail_call", side_effect=responses), \
                patch.object(self.module.subprocess, "run") as run:
            self.module.main()
        run.assert_not_called()
        self.assertIn("sample.tklapp.com", dialog.msgbox.call_args.args[1])

    def test_retry_accepts_only_tklbam_completed_initialization(self):
        self.assertEqual(self.run_main(1, "error: already initialized\n"),
                         [["tklbam-init", "test-key"]])

    def test_new_account_is_initialized(self):
        self.assertEqual(self.run_main(), [["tklbam-init", "test-key"]])

    def test_existing_backup_account_does_not_skip_dns_configuration(self):
        self.assertEqual(self.run_main(1, "error: already initialized\n", fqdn=True), [
            ["tklbam-init", "test-key"],
            ["hubdns-init", "test-key", "example.tklapp.com"], ["hubdns-update"]])

    def test_partial_initialization_retries_profile_download(self):
        # A linked account without a profile still needs tklbam-init, even
        # though tklbam-status would report NO BACKUPS (10).
        self.assertEqual(self.run_main(), [["tklbam-init", "test-key"]])

    def test_partial_initialization_profile_failure_propagates(self):
        with patch.object(sys, "stderr") as stderr:
            with self.assertRaises(SystemExit):
                self.run_main(1, "error: profile download failed\n")
            self.assertIn("error: profile download failed\n",
                          [call.args[0] for call in stderr.write.call_args_list])

    def test_other_failure_containing_already_initialized_still_propagates(self):
        with patch.object(sys, "stderr"):
            with self.assertRaises(SystemExit):
                self.run_main(1, "error: already initialized\nerror: corrupt profile\n")
            with self.assertRaises(SystemExit):
                self.run_main(2, "error: already initialized\n")

    def test_initialization_errors_do_not_disclose_api_key(self):
        for command in ("tklbam-init", "hubdns-init"):
            for message in ("error: profile download failed\n",
                            "error: 'test-key' is an invalid API-KEY\n"):
                with self.subTest(command=command, message=message):
                    output = io.StringIO()
                    with patch.object(sys, "stderr", output):
                        with self.assertRaises(SystemExit) as raised:
                            self.run_main(1, message, fqdn=True, failed_command=command)
                    self.assertNotEqual(raised.exception.code, 0)
                    diagnostic = output.getvalue() + str(raised.exception)
                    self.assertNotIn("test-key", diagnostic)
                    self.assertIn("failed", diagnostic)


if __name__ == "__main__":
    unittest.main()


class ExplicitLegacyPreseedTests(unittest.TestCase):
    def exercise(self, apikey):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'bin').mkdir()
            config = root / 'inithooks.conf'
            config.write_text('HUB_EMAIL_TOKEN=fixture-token\nHUB_APIKEY=' + apikey + '\nFQDN=fixture.tklapp.com\n')
            defaults = root / 'defaults'
            defaults.write_text('INITHOOKS_CONF=' + str(config) + '\nINITHOOKS_PATH=' + str(root) + '\n')
            log = root / 'calls'
            helper = root / 'bin/turnkey-mail'
            helper.write_text('#!/bin/sh\nread token\n[ "$token" = fixture-token ] || exit 1\necho enroll >> "' + str(log) + '"\n')
            legacy = root / 'bin/hubservices.py'
            legacy.write_text('#!/bin/sh\n[ "$1" = --apikey=legacy-fixture-key ] || exit 1\n[ "$2" = --fqdn=fixture.tklapp.com ] || exit 1\necho legacy >> "' + str(log) + '"\n')
            helper.chmod(0o755)
            legacy.chmod(0o755)
            script = (ROOT / 'firstboot.d/80hub-services').read_text().replace('/etc/default/inithooks', str(defaults))
            result = subprocess.run(['bash'], input=script, text=True, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
            return log.read_text().splitlines()

    def test_explicit_legacy_key_preseed_runs_after_token_enrollment(self):
        self.assertEqual(['enroll', 'legacy'], self.exercise('legacy-fixture-key'))

    def test_new_token_only_launch_never_runs_legacy_key_initializer(self):
        self.assertEqual(['enroll'], self.exercise(''))
